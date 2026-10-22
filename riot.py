"""Client Riot Games API — rate limiting, retries, Data Dragon, helpers stats."""

import asyncio
import logging
from typing import Any, Optional

import httpx

from config import CFG, TIER_ORDER, DIV_ORDER

log = logging.getLogger("riot")

PLATFORM_BASE = f"https://{CFG.platform}.api.riotgames.com"
REGION_BASE = f"https://{CFG.region}.api.riotgames.com"
HEADERS = {"X-Riot-Token": CFG.riot_api_key}

DDRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_BASE = "https://ddragon.leagueoflegends.com/cdn/{v}"
COMMUNITY_DRAGON = "https://cdn.communitydragon.org/latest/champion/{cid}/square"


class RiotError(Exception):
    def __init__(self, status: int, body: str = ""):
        self.status = status
        super().__init__(f"Riot API {status}: {body[:150]}")


class DataDragon:
    """Cache champions/items/version depuis Data Dragon (+ CommunityDragon pour Arena)."""

    def __init__(self):
        self.version = "14.1.1"
        self.champs: dict[int, str] = {}
        self.items: dict[int, str] = {}

    async def load(self, client: httpx.AsyncClient):
        try:
            versions = (await client.get(DDRAGON_VERSIONS, timeout=15)).json()
            self.version = versions[0]
        except Exception as e:
            log.warning(f"DDragon versions indisponible: {e}")
        try:
            data = (await client.get(
                f"{DDRAGON_BASE.format(v=self.version)}/data/en_US/champion.json", timeout=15)).json()
            for name, c in data["data"].items():
                self.champs[int(c["key"])] = name
        except Exception as e:
            log.warning(f"DDragon champions indisponible: {e}")
        try:
            data = (await client.get(
                f"{DDRAGON_BASE.format(v=self.version)}/data/en_US/item.json", timeout=15)).json()
            for iid, it in data["data"].items():
                self.items[int(iid)] = it.get("name", str(iid))
        except Exception as e:
            log.warning(f"DDragon items indisponible: {e}")
        log.info(f"Data Dragon {self.version} : {len(self.champs)} champions, {len(self.items)} items")

    def champ_name(self, cid: int) -> str:
        return self.champs.get(cid, f"Champion {cid}")

    def champ_icon(self, cid: int, fallback_name: str = "") -> str:
        # CommunityDragon indexe par ID numérique, couvre aussi Arena/Augments.
        if cid:
            return COMMUNITY_DRAGON.format(cid=cid)
        return f"{DDRAGON_BASE.format(v=self.version)}/img/champion/{fallback_name}.png"

    def item_name(self, iid: int) -> str:
        return self.items.get(iid, f"Item {iid}")


class RiotClient:
    """HTTP client Riot avec gestion 429 (Retry-After) + 5xx."""

    def __init__(self):
        self.http = httpx.AsyncClient(timeout=15, headers=HEADERS)
        self._lock = asyncio.Lock()

    async def close(self):
        await self.http.aclose()

    async def _get(self, url: str, platform: bool = False, retries: int = 3) -> Optional[Any]:
        base = PLATFORM_BASE if platform else REGION_BASE
        full = base + url
        for attempt in range(retries):
            try:
                r = await self.http.get(full)
            except httpx.HTTPError as e:
                log.warning(f"HTTP {e} (retry {attempt+1}/{retries})")
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 5))
                log.warning(f"Rate-limit, attente {wait:.0f}s")
                await asyncio.sleep(wait)
                continue
            if r.status_code >= 500:
                log.warning(f"Riot {r.status_code} (retry {attempt+1}/{retries})")
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise RiotError(r.status_code, r.text)
            return r.json()
        return None

    # ── account-v1 ─────────────────────────────────────────────
    async def puuid(self, game_name: str, tag: str) -> Optional[str]:
        d = await self._get(f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag}")
        return d.get("puuid") if d else None

    async def riot_id(self, puuid: str) -> tuple[str, str]:
        d = await self._get(f"/riot/account/v1/accounts/by-puuid/{puuid}")
        return (d.get("gameName", "?"), d.get("tagLine", "?")) if d else ("?", "?")

    # ── spectator-v5 ───────────────────────────────────────────
    async def active_game(self, puuid: str) -> Optional[dict]:
        return await self._get(f"/lol/spectator/v5/active-games/by-summoner/{puuid}", platform=True)

    # ── match-v5 ───────────────────────────────────────────────
    async def match_ids(self, puuid: str, count: int = 10, start: int = 0,
                        queue: Optional[int] = None) -> list[str]:
        q = f"/lol/match/v5/matches/by-puuid/{puuid}/ids?start={start}&count={count}"
        if queue:
            q += f"&queue={queue}"
        return await self._get(q) or []

    async def match(self, match_id: str) -> Optional[dict]:
        return await self._get(f"/lol/match/v5/matches/{match_id}")

    # ── summoner-v4 ────────────────────────────────────────────
    async def summoner(self, puuid: str) -> Optional[dict]:
        return await self._get(f"/lol/summoner/v4/summoners/by-puuid/{puuid}", platform=True)

    # ── league-v4 (peut être 403 sur clé dev) ──────────────────
    async def leagues(self, puuid: str) -> list[dict]:
        try:
            return await self._get(f"/lol/league-v4/entries/by-puuid/{puuid}", platform=True) or []
        except RiotError as e:
            if e.status == 403:
                log.warning("league-v4 403 — endpoint bloqué sur clé dev (rank indispo)")
                return []
            raise

    # ── champion-mastery-v4 ────────────────────────────────────
    async def masteries(self, puuid: str, count: int = 5) -> list[dict]:
        d = await self._get(
            f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top?count={count}",
            platform=True)
        return d or []

    # ── lol-challenges-v1 (pour achievements potentiels) ───────
    async def challenges(self, puuid: str) -> Optional[dict]:
        return await self._get(f"/lol/challenges/v1/player-data/{puuid}", platform=True)


# ── Helpers stats ─────────────────────────────────────────────────────────

def find_participant(match: dict, puuid: str) -> Optional[dict]:
    for p in match.get("info", {}).get("participants", []):
        if p.get("puuid") == puuid:
            return p
    return None


def team_kills(match: dict, team_id: int) -> int:
    return sum(p.get("kills", 0) for p in match["info"]["participants"] if p.get("teamId") == team_id)


def format_duration(s: int) -> str:
    return f"{s // 60}:{s % 60:02d}"


def league_value(tier: str, div: str, lp: int) -> int:
    """Valeur numérique d'un rang pour calculer les deltas de LP."""
    try:
        t = TIER_ORDER.index(tier.upper())
        d = DIV_ORDER.index(div.upper()) if div else 0
    except ValueError:
        return lp
    # Master+ pas de division
    if tier.upper() in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        d = 4
    return t * 500 + d * 100 + lp


def extract_stats(match: dict, puuid: str, dd: DataDragon) -> Optional[dict]:
    info = match.get("info", {})
    p = find_participant(match, puuid)
    if not p:
        return None
    dur = info.get("gameDuration", 0)
    mins = max(dur / 60, 1)
    k, d, a = p.get("kills", 0), p.get("deaths", 0), p.get("assists", 0)
    cs = p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
    items = [p.get(f"item{i}", 0) for i in range(7)]
    tk = team_kills(match, p.get("teamId", 0))

    return {
        "match_id": match.get("metadata", {}).get("matchId", "?"),
        "win": p.get("win", False),
        "early_surr": info.get("participants", [{}])[0].get("gameEndedInEarlySurrender", False),
        "champ": p.get("championName", "?"),
        "champ_id": p.get("championId", 0),
        "role": (p.get("teamPosition") or p.get("individualPosition") or "?").upper(),
        "queue": info.get("queueId", -1),
        "mode": None,  # rempli par caller via queue_name
        "kills": k, "deaths": d, "assists": a,
        "kda": round((k + a) / max(d, 1), 2),
        "kp": round(100 * (k + a) / max(tk, 1)),
        "dmg": p.get("totalDamageDealtToChampions", 0),
        "dpm": round(p.get("totalDamageDealtToChampions", 0) / mins),
        "dmg_taken": p.get("totalDamageTaken", 0),
        "mitigated": p.get("damageSelfMitigated", 0),
        "cs": cs, "cs_min": round(cs / mins, 1),
        "vision": p.get("visionScore", 0),
        "wards": p.get("wardsPlaced", 0),
        "gold": p.get("goldEarned", 0),
        "largest_multi": p.get("largestMultiKill", 0),
        "penta": p.get("pentaKills", 0), "quadra": p.get("quadraKills", 0),
        "triple": p.get("tripleKills", 0), "double": p.get("doubleKills", 0),
        "first_blood": p.get("firstBloodKill", False),
        "solo_kills": p.get("challenges", {}).get("soloKills", 0) if isinstance(p.get("challenges"), dict) else 0,
        "turret": p.get("turretTakedowns", 0),
        "dur": dur,
        "game_end": info.get("gameEndTimestamp", 0),
        "items": [dd.item_name(i) for i in items if i],
        "team_id": p.get("teamId", 0),
        "team_kills": tk,
        "all_participants": info.get("participants", []),
        "placement": p.get("placement"),  # Arena
        "subteam_placement": p.get("subteamPlacement"),  # Arena
    }
