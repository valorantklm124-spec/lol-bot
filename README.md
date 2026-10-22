# lol-bot

Bot Discord 2026 qui track des joueurs LoL, annonce les games en live, poste une
analyse IA à la fin de chaque game, et offre des slash commands + boutons
"Demander à l'IA" + graphes de dégâts.

## Stack

`discord.py` (Cog + app_commands) · `httpx` · `anthropic` (gateway ou officiel)
`aiosqlite` · `matplotlib`

## Setup

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
# remplis .env (voir .env.example)
venv\Scripts\python.exe bot.py        # ou start.bat
```

## Slash commands

| Commande | Effet |
|---|---|
| `/track <nom> <tag>` | Suivre un joueur (Riot ID) |
| `/untrack <joueur>` | Arrêter le suivi |
| `/list` | Joueurs suivis |
| `/ingame` | Qui est en game + champion |
| `/stats [joueur]` | Rang, masteries, streak, forme |
| `/last [joueur]` | Re-poste la dernière game |
| `/recap [joueur] [n]` | Récap + graphe session + analyse IA |
| `/ask <question>` | Question libre à l'IA |

## Features

- **Live tracking** — poll `spectator-v5` → annonce "en game" (champion + analyse IA).
- **Post-game** — poll `match-v5` → embed stats complet + graphe dégâts + commentaire IA.
- **Delta LP** — détecte +18/-22 LP sur les ranked.
- **Bouton ✨ Demander à l'IA** — modal → question libre sur la game.
- **Multi-joueurs** — track plusieurs comptes, autocomplete dans les commandes.
- **SQLite** — joueurs, matchs, historique LP persistants.
- **Data Dragon** — noms champions/items/icônes auto.
- **Gestion erreurs** — 429 (Retry-After), 5xx, DNS natif (aiodns-safe).

## Notes

- `DISCORD_GUILD_ID` dans `.env` → slash commands instantanées (sinon ~1h global).
- `ANTHROPIC_MODEL` — `claude-opus-5` par défaut (ta config redirige tout vers opus).
- Le fichier `lolbot.db` est créé automatiquement, `.gitignore`d.
