"""lol-bot — tracker LoL + analyses IA pour Discord.

Slash commands, multi-joueurs, boutons "Demander à l'IA", graphes de dégâts,
deltas de LP, streaks. Archi modulaire : config / db / riot / ai / images / views.
"""

import asyncio
import logging
import os
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import ai
import db as dbmod
import images
import prompts
import views
from config import CFG, queue_name, RANKED_QUEUES, ARENA_QUEUES, TIER_EMOJI
from riot import (RiotClient, DataDragon, extract_stats, find_participant,
                  format_duration, league_value)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")


# ─────────────────────────── Tracker ───────────────────────────

class Tracker:
    """Un joueur suivi : puuid, riot id, rang, état en-game."""

    def __init__(self, puuid: str, game_name: str, tag: str):
        self.puuid = puuid
        self.game_name = game_name
        self.tag = tag
        self.in_game = False
        self.game_id: Optional[int] = None
        self.rank_str = "Unranked"
        self.league_value = 0          # valeur numérique pour deltas LP
        self.last_lp = 0
        self.league_str = "—"

    @property
    def display(self) -> str:
        return f"{self.game_name}#{self.tag}"


class LolCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.riot = RiotClient()
        self.dd = DataDragon()
        self.db = dbmod.DB()
        self.channel: Optional[discord.abc.Messageable] = None
        self.trackers: dict[str, Tracker] = {}

    async def cog_load(self):
        await self.db.open()
        await self.dd.load(self.riot.http)
        await self._load_players()
        await self._bootstrap_default_player()
        self.poll_loop.start()

    async def cog_unload(self):
        self.poll_loop.cancel()
        await self.riot.close()
        await self.db.close()

    async def _channel(self) -> discord.abc.Messageable:
        if self.channel is None:
            self.channel = self.bot.get_channel(CFG.channel_id) \
                or await self.bot.fetch_channel(CFG.channel_id)
        return self.channel

    async def _load_players(self):
        for row in await self.db.players():
            t = Tracker(row["puuid"], row["game_name"], row["tag"])
            t.in_game = bool(row["in_game"])
            t.game_id = row["game_id"]
            t.last_lp = row["last_lp"] or 0
            t.rank_str = row["last_rank"] or "Unranked"
            t.league_value = t.last_lp
            self.trackers[t.puuid] = t
        log.info(f"{len(self.trackers)} joueur(s) chargé(s)")

        # Seed : marque les matchs récents comme déjà vus au 1er lancement,
        # et initialise le rang pour les deltas LP.
        first_boot = not await self.db.get_meta("seeded")
        for t in self.trackers.values():
            try:
                if first_boot:
                    ids = await self.riot.match_ids(t.puuid, count=10)
                    for mid in ids:
                        m = await self.riot.match(mid)
                        if not m:
                            continue
                        s = extract_stats(m, t.puuid, self.dd)
                        if s:
                            await self.db.record_match(
                                mid, t.puuid, s["queue"], s["win"], s["champ"],
                                s["kills"], s["deaths"], s["assists"],
                                s["dur"], s["game_end"])
                    log.info(f"Seed {t.display} : {len(ids)} games marquées")
                try:
                    await self.refresh_rank(t)
                except Exception as e:
                    log.warning(f"init rank {t.display}: {e}")
            except Exception as e:
                log.warning(f"init {t.display}: {e}")
        if first_boot:
            await self.db.set_meta("seeded", "1")

    async def _bootstrap_default_player(self):
        """Auto-track le joueur de .env (RIOT_GAME_NAME/RIOT_TAG_LINE) au 1er boot."""
        gn, tg = os.getenv("RIOT_GAME_NAME", "").strip(), os.getenv("RIOT_TAG_LINE", "").strip()
        if not gn or not tg:
            return
        if any(t.game_name.lower() == gn.lower() for t in self.trackers.values()):
            return
        try:
            puuid = await self.riot.puuid(gn, tg)
            if not puuid:
                log.warning(f"Joueur par défaut introuvable : {gn}#{tg}")
                return
            t = Tracker(puuid, gn, tg)
            await self.db.add_player(puuid, gn, tg, int(time.time()))
            try:
                await self.refresh_rank(t)
            except Exception as e:
                log.warning(f"refresh_rank bootstrap: {e}")
                t.rank_str = "Ranked indispo"
            # seed ses matchs récents pour ne pas flooder
            for mid in await self.riot.match_ids(puuid, count=10):
                m = await self.riot.match(mid)
                if m:
                    s = extract_stats(m, puuid, self.dd)
                    if s:
                        await self.db.record_match(
                            mid, puuid, s["queue"], s["win"], s["champ"],
                            s["kills"], s["deaths"], s["assists"], s["dur"], s["game_end"])
            self.trackers[puuid] = t
            log.info(f"Auto-track {gn}#{tg} ({t.rank_str})")
        except Exception as e:
            log.warning(f"bootstrap player: {e}")

    # ── rank refresh ───────────────────────────────────────────
    async def refresh_rank(self, t: Tracker):
        leagues = await self.riot.leagues(t.puuid)
        solo = next((l for l in leagues if l["queueType"] == "RANKED_SOLO_5x5"), None)
        if solo:
            t.rank_str = f"{solo['tier']} {solo['rank']}"
            t.league_value = league_value(solo["tier"], solo["rank"], solo["leaguePoints"])
            t.last_lp = solo["leaguePoints"]
        else:
            t.rank_str = "Unranked"
            t.league_value = 0
            t.last_lp = 0
        await self.db.set_rank(t.puuid, t.league_value, t.rank_str)

    # ── detection en game ──────────────────────────────────────
    async def _check_ingame(self, t: Tracker):
        game = await self.riot.active_game(t.puuid)
        if game and not t.in_game:
            t.in_game = True
            t.game_id = game.get("gameId")
            await self.db.set_ingame(t.puuid, True, t.game_id)
            await self._announce_ingame(t, game)
        elif not game and t.in_game:
            t.in_game = False
            t.game_id = None
            await self.db.set_ingame(t.puuid, False, None)
            log.info(f"{t.display} : fin de game")

    async def _announce_ingame(self, t: Tracker, game: dict):
        champ_id = next(
            (p["championId"] for p in game.get("participants", []) if p.get("puuid") == t.puuid),
            0)
        champ = self.dd.champ_name(champ_id)
        mode = queue_name(game.get("gameQueueConfigId", -1))
        elapsed = round(game.get("gameLength", 0) / 60)

        bans = [self.dd.champ_name(b.get("championId", 0))
                for b in game.get("bannedChampions", [])]
        extras = f"Bans : {', '.join(bans[:10])}" if bans else ""

        comment = await ai.comment(prompts.INGAME.format(
            name=t.game_name, tag=t.tag, rank=t.rank_str,
            champ=champ, mode=mode, elapsed=elapsed, extras=extras))

        e = discord.Embed(
            title=f"🔴 {t.display} — en game",
            description=f"**{champ}** • {mode}",
            color=0x3498DB, timestamp=discord.utils.utcnow())
        e.set_thumbnail(url=self.dd.champ_icon(champ_id, champ))
        e.add_field(name="Analyse", value=comment, inline=False)
        e.set_footer(text=f"Ranked {t.rank_str}")
        chan = await self._channel()
        await chan.send(embed=e)
        log.info(f"{t.display} : entrée en game ({champ} {mode})")

    # ── detection game finie ───────────────────────────────────
    async def _check_matches(self, t: Tracker):
        ids = await self.riot.match_ids(t.puuid, count=5)
        for mid in reversed(ids):
            if await self.db.match_seen(mid):
                continue
            match = await self.riot.match(mid)
            if not match:
                continue
            stats = extract_stats(match, t.puuid, self.dd)
            if not stats:
                continue
            stats["mode"] = queue_name(stats["queue"])
            stats["me"] = find_participant(match, t.puuid)
            await self._post_game(t, match, stats)
            await self.db.record_match(
                mid, t.puuid, stats["queue"], stats["win"], stats["champ"],
                stats["kills"], stats["deaths"], stats["assists"],
                stats["dur"], stats["game_end"])

    async def _post_game(self, t: Tracker, match: dict, stats: dict):
        await self.refresh_rank(t)
        streak_w, streak_n = await self.db.streak(t.puuid)
        streak_txt = f" • {streak_n}{'W' if streak_w=='W' else 'L'} de suite" if streak_n >= 2 else ""

        # delta LP si ranked (compare à la valeur stockée avant refresh)
        lp_txt = ""
        if stats["queue"] in RANKED_QUEUES:
            prev_key = f"prev_lp_{t.puuid}"
            prev_raw = await self.db.get_meta(prev_key)
            await self.db.set_meta(prev_key, str(t.league_value))
            if prev_raw is not None:
                delta = t.league_value - int(prev_raw)
                if delta != 0:
                    lp_txt = f"{'+' if delta > 0 else ''}{delta} LP"

        multis = ", ".join(filter(None, [
            f"{stats['penta']} Penta" if stats['penta'] else "",
            f"{stats['quadra']} Quadra" if stats['quadra'] else "",
            f"{stats['triple']} Triple" if stats['triple'] else "",
            f"{stats['double']} Double" if stats['double'] else "",
        ])) or "—"

        arena_info = ""
        if stats["queue"] in ARENA_QUEUES:
            arena_info = f"- Placement Arena : #{stats.get('placement') or stats.get('subteam_placement') or '?'}"

        # Commentaire IA + graphe dégâts en parallèle
        comment, chart = await asyncio.gather(
            ai.comment(prompts.ANALYSE.format(
                name=t.game_name, tag=t.tag, rank=t.rank_str, lp_change=lp_txt or "—",
                champ=stats["champ"], role=stats["role"], mode=stats["mode"],
                win="Victoire" if stats["win"] else "Défaite",
                dur=format_duration(stats["dur"]),
                k=stats["kills"], d=stats["deaths"], a=stats["assists"],
                kda=stats["kda"], kp=stats["kp"], team_kills=stats["team_kills"],
                dmg=f"{stats['dmg']:,}", dpm=stats["dpm"],
                dmg_taken=f"{stats['dmg_taken']:,}", mitigated=f"{stats['mitigated']:,}",
                cs=stats["cs"], cs_min=stats["cs_min"], vision=stats["vision"],
                wards=stats["wards"], gold=f"{stats['gold']:,}",
                multis=multis, solo=stats["solo_kills"], turret=stats["turret"],
                items=", ".join(stats["items"]) or "—",
                arena_info=arena_info)),
            asyncio.to_thread(images.damage_chart,
                              match["info"]["participants"], t.puuid,
                              champ_of=self.dd.champ_name),
        )

        win = stats["win"]
        color = 0x2ECC71 if win else 0xE74C3C
        title = f"{'✅ Victoire' if win else '❌ Défaite'} — {stats['champ']} ({stats['mode']})"
        e = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
        e.set_thumbnail(url=self.dd.champ_icon(stats["champ_id"], stats["champ"]))
        e.add_field(name="KDA", value=f"**{stats['kills']}/{stats['deaths']}/{stats['assists']}**\n({stats['kda']})", inline=True)
        e.add_field(name="Durée", value=format_duration(stats["dur"]), inline=True)
        e.add_field(name="Poste", value=stats["role"].title(), inline=True)
        e.add_field(name="Dégâts", value=f"{stats['dmg']:,}\n({stats['dpm']}/min)".replace(",", " "), inline=True)
        e.add_field(name="CS", value=f"{stats['cs']}\n({stats['cs_min']}/min)", inline=True)
        e.add_field(name="KP", value=f"{stats['kp']}%", inline=True)
        e.add_field(name="Vision", value=f"{stats['vision']}\n({stats['wards']} wards)", inline=True)
        e.add_field(name="Or", value=f"{stats['gold']:,}".replace(",", " "), inline=True)
        e.add_field(name="Items", value="\n".join(f"• {i}" for i in stats["items"][:6]) or "—", inline=True)
        if lp_txt:
            e.add_field(name="LP", value=lp_txt, inline=True)
        e.add_field(name="Analyse IA", value=comment, inline=False)
        foot = f"{t.display} • {t.rank_str}{streak_txt}"
        e.set_footer(text=foot)

        # contexte pour le bouton Ask
        ctx = (
            f"Joueur: {t.display} | Rang: {t.rank_str} | Champ: {stats['champ']} "
            f"| Poste: {stats['role']} | Mode: {stats['mode']} | "
            f"{'Victoire' if win else 'Défaite'} en {format_duration(stats['dur'])} | "
            f"KDA {stats['kills']}/{stats['deaths']}/{stats['assists']} ({stats['kda']}) "
            f"| KP {stats['kp']}% | Dmg {stats['dmg']} ({stats['dpm']}/min) | "
            f"CS {stats['cs']} ({stats['cs_min']}/min) | Vision {stats['vision']} | "
            f"Items: {', '.join(stats['items'])}"
        )

        chan = await self._channel()
        if chart:
            file = discord.File(chart, filename="dmg.png")
            e.set_image(url="attachment://dmg.png")
            await chan.send(embed=e, file=file, view=views.AskView(ctx))
        else:
            await chan.send(embed=e, view=views.AskView(ctx))
        log.info(f"{t.display} : game postée ({stats['champ']} {'W' if win else 'L'})")

    # ── boucle principale ──────────────────────────────────────
    @tasks.loop(seconds=CFG.poll_seconds)
    async def poll_loop(self):
        if not self.trackers:
            return
        for t in list(self.trackers.values()):
            try:
                await self._check_ingame(t)
                await self._check_matches(t)
            except Exception as e:
                log.warning(f"poll {t.display}: {type(e).__name__} {e}")

    @poll_loop.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


# ─────────────────────────── Commands ───────────────────────────

def autocomp_trackers():
    """Autocomplete : lit les trackers au moment de l'appel (pas au boot)."""
    async def _ac(interaction: discord.Interaction, current: str):
        cog = getattr(interaction.client, "cog", None)
        if not cog or not cog.trackers:
            return []
        return [
            app_commands.Choice(name=t.display, value=t.puuid)
            for t in cog.trackers.values()
            if current.lower() in t.display.lower()
        ][:25]
    return _ac


class LolBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.cog: Optional[LolCog] = None

    async def setup_hook(self):
        self.cog = LolCog(self)
        await self.add_cog(self.cog)
        if CFG.guild_id:
            guild = discord.Object(id=CFG.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info(f"Slash commands sync (guild {CFG.guild_id})")
        else:
            await self.tree.sync()
            log.info("Slash commands sync (global)")

    async def on_ready(self):
        log.info(f"Connecté : {self.user}  |  channel #{CFG.channel_id}")


bot = LolBot()


def tracker_or_none(interaction, puuid: Optional[str]) -> Optional[Tracker]:
    cog = bot.cog
    if puuid:
        return cog.trackers.get(puuid)
    # défaut : premier tracker (le compte principal)
    return next(iter(cog.trackers.values()), None)


# ── /track ─────────────────────────────────────────────────────
@bot.tree.command(name="track", description="Suivre un joueur LoL (Riot ID).")
@app_commands.describe(game_name="Nom Riot (ex: jnr18k)", tag="Tag (ex: kzk)")
async def track(interaction: discord.Interaction, game_name: str, tag: str):
    await interaction.response.defer(thinking=True)
    puuid = await bot.cog.riot.puuid(game_name, tag)
    if not puuid:
        await interaction.followup.send(f"❌ Joueur introuvable : `{game_name}#{tag}`")
        return
    if puuid in bot.cog.trackers:
        await interaction.followup.send(f"ℹ️ `{game_name}#{tag}` est déjà suivi.")
        return
    t = Tracker(puuid, game_name, tag)
    await bot.cog.db.add_player(puuid, game_name, tag, int(time.time()))
    try:
        await bot.cog.refresh_rank(t)
    except Exception as e:
        log.warning(f"refresh_rank {game_name}#{tag}: {e}")
        t.rank_str = "Ranked indispo"
    bot.cog.trackers[puuid] = t
    e = discord.Embed(
        title=f"✅ Suivi activé — {game_name}#{tag}",
        description=f"Rang : **{t.rank_str}**",
        color=0x2ECC71)
    await interaction.followup.send(embed=e)
    log.info(f"Track ajouté : {game_name}#{tag}")


@bot.tree.command(name="untrack", description="Arrêter de suivre un joueur.")
@app_commands.autocomplete(joueur=autocomp_trackers())
async def untrack(interaction: discord.Interaction, joueur: str):
    t = bot.cog.trackers.get(joueur)
    if not t:
        await interaction.response.send_message("Joueur introuvable.", ephemeral=True)
        return
    await bot.cog.db.remove_player(joueur)
    del bot.cog.trackers[joueur]
    await interaction.response.send_message(f"🗑️ `{t.display}` retiré du suivi.")


@bot.tree.command(name="list", description="Joueurs actuellement suivis.")
async def list_players(interaction: discord.Interaction):
    if not bot.cog.trackers:
        await interaction.response.send_message("Aucun joueur suivi. `/track` pour ajouter.")
        return
    lines = [f"• **{t.display}** — {t.rank_str}" for t in bot.cog.trackers.values()]
    await interaction.response.send_message("\n".join(lines))


# ── /ingame ────────────────────────────────────────────────────
@bot.tree.command(name="ingame", description="Qui est en game actuellement ?")
async def ingame(interaction: discord.Interaction):
    await interaction.response.defer()
    out = []
    for t in bot.cog.trackers.values():
        game = await bot.cog.riot.active_game(t.puuid)
        if game:
            champ_id = next(
                (p["championId"] for p in game["participants"] if p.get("puuid") == t.puuid), 0)
            out.append(f"🔴 **{t.display}** — {bot.cog.dd.champ_name(champ_id)} "
                       f"({queue_name(game.get('gameQueueConfigId',-1))}, "
                       f"{round(game.get('gameLength',0)/60)}min)")
        else:
            out.append(f"⚪ {t.display} — pas en game")
    await interaction.followup.send("\n".join(out) or "Personne suivi.")


# ── /stats ─────────────────────────────────────────────────────
@bot.tree.command(name="stats", description="Fiche joueur : rang, masteries, forme récente.")
@app_commands.autocomplete(joueur=autocomp_trackers())
async def stats(interaction: discord.Interaction, joueur: Optional[str] = None):
    await interaction.response.defer(thinking=True)
    t = tracker_or_none(interaction, joueur)
    if not t:
        await interaction.followup.send("Aucun joueur suivi. `/track` d'abord.")
        return

    try:
        await bot.cog.refresh_rank(t)
    except Exception as e:
        log.warning(f"stats refresh_rank: {e}")
    leagues = await bot.cog.riot.leagues(t.puuid)
    masteries = await bot.cog.riot.masteries(t.puuid, 5)
    summ = await bot.cog.riot.summoner(t.puuid)
    streak_w, streak_n = await bot.cog.db.streak(t.puuid)
    history = await bot.cog.db.history(t.puuid, 10)

    e = discord.Embed(
        title=f"{t.display}",
        color=0x8B5CF6,
        timestamp=discord.utils.utcnow())
    if summ:
        e.set_thumbnail(url=(
            f"https://ddragon.leagueoflegends.com/cdn/{bot.cog.dd.version}/img/profileicon/"
            f"{summ.get('profileIconId',0)}.png"))
        e.description = f"Niveau {summ.get('summonerLevel','?')}"

    # Rangs
    for l in leagues:
        qname = {"RANKED_SOLO_5x5": "Solo/Duo", "RANKED_FLEX_SR": "Flex"}.get(l["queueType"], l["queueType"])
        emoji = TIER_EMOJI.get(l["tier"], "")
        e.add_field(
            name=f"{emoji} {qname}",
            value=f"**{l['tier']} {l['rank']}** — {l['leaguePoints']} LP\n"
                  f"{l['wins']}W / {l['losses']}L ({round(100*l['wins']/max(l['wins']+l['losses'],1))}%)",
            inline=True)
    if not leagues:
        e.add_field(name="Rang", value="Unranked", inline=True)

    if streak_n >= 2:
        e.add_field(name="Streak", value=f"{'🔥' if streak_w=='W' else '❄️'} {streak_n}{'W' if streak_w=='W' else 'L'}", inline=True)

    # Masteries
    if masteries:
        lines = [
            f"**{bot.cog.dd.champ_name(m['championId'])}** — {m['championPoints']:,} pts"
            .replace(",", " ")
            for m in masteries
        ]
        e.add_field(name="Top masteries", value="\n".join(lines), inline=False)

    # Forme récente
    if history:
        wins = sum(1 for h in history if h["win"])
        e.add_field(
            name=f"Forme ({len(history)} dernières)",
            value=f"{wins}W {len(history)-wins}L — {round(100*wins/len(history))}%",
            inline=True)

    await interaction.followup.send(embed=e)


# ── /last ──────────────────────────────────────────────────────
@bot.tree.command(name="last", description="Analyse de la dernière game d'un joueur.")
@app_commands.autocomplete(joueur=autocomp_trackers())
async def last(interaction: discord.Interaction, joueur: Optional[str] = None):
    await interaction.response.defer(thinking=True)
    t = tracker_or_none(interaction, joueur)
    if not t:
        await interaction.followup.send("Aucun joueur suivi.")
        return
    ids = await bot.cog.riot.match_ids(t.puuid, count=1)
    if not ids:
        await interaction.followup.send("Aucune game trouvée.")
        return
    match = await bot.cog.riot.match(ids[0])
    if not match:
        await interaction.followup.send("Impossible de récupérer la game.")
        return
    stats = extract_stats(match, t.puuid, bot.cog.dd)
    stats["mode"] = queue_name(stats["queue"])
    # reposte comme si c'était une game détectée (sans re-l'annoncer en DB)
    await bot.cog._post_game(t, match, stats)


# ── /recap ─────────────────────────────────────────────────────
@bot.tree.command(name="recap", description="Récap des N dernières games (avec graphe).")
@app_commands.describe(n="Nombre de games (défaut 10)")
@app_commands.autocomplete(joueur=autocomp_trackers())
async def recap(interaction: discord.Interaction, joueur: Optional[str] = None, n: int = 10):
    await interaction.response.defer(thinking=True)
    t = tracker_or_none(interaction, joueur)
    if not t:
        await interaction.followup.send("Aucun joueur suivi.")
        return
    ids = await bot.cog.riot.match_ids(t.puuid, count=min(n, 15))
    games = []
    for mid in ids:
        m = await bot.cog.riot.match(mid)
        if not m:
            continue
        s = extract_stats(m, t.puuid, bot.cog.dd)
        if s:
            s["mode"] = queue_name(s["queue"])
            games.append(s)
    if not games:
        await interaction.followup.send("Pas de games récentes.")
        return

    wins = sum(1 for g in games if g["win"])
    avg_kda = round(sum(g["kda"] for g in games) / len(games), 2)
    avg_dpm = round(sum(g["dpm"] for g in games) / len(games))

    lines = "\n".join(
        f"{'W' if g['win'] else 'L'} {g['champ']:<12} "
        f"{g['kills']}/{g['deaths']}/{g['assists']} ({g['kda']}) — {g['mode']}"
        for g in games[:10]
    )
    comment = await ai.comment(prompts.RECAP.format(
        name=t.game_name, tag=t.tag, games=lines))

    e = discord.Embed(
        title=f"Récap — {t.display} ({len(games)} games)",
        description=f"**{wins}W {len(games)-wins}L** ({round(100*wins/len(games))}%) • "
                    f"KDA moyen {avg_kda} • {avg_dpm} dmg/min",
        color=0x8B5CF6, timestamp=discord.utils.utcnow())
    e.add_field(name="Analyse IA", value=comment, inline=False)
    e.add_field(name="Games", value=f"```\n{lines}\n```", inline=False)

    chart = images.session_chart(games)
    if chart:
        file = discord.File(chart, filename="session.png")
        e.set_image(url="attachment://session.png")
        await interaction.followup.send(embed=e, file=file)
    else:
        await interaction.followup.send(embed=e)


# ── /ask ───────────────────────────────────────────────────────
@bot.tree.command(name="ask", description="Poser une question LoL à l'IA.")
@app_commands.describe(question="Ta question")
async def ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer(thinking=True)
    answer = await ai.comment(f"{prompts.ASK_SYSTEM}\n\nQuestion : {question}",
                              max_tokens=600)
    e = discord.Embed(title="Analyse IA", description=answer[:3900], color=0x8B5CF6)
    e.set_footer(text=f"Q : {question[:200]}")
    await interaction.followup.send(embed=e)


# ─────────────────────────── Run ───────────────────────────────

def main():
    # Résolveur DNS natif (évite les pannes aiodns/c-ares)
    try:
        import aiohttp.resolver
        aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver
    except Exception:
        pass
    bot.run(CFG.discord_token)


if __name__ == "__main__":
    main()
