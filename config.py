"""Configuration centrale du bot — chargée depuis .env / variables d'environnement."""

import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str = "", required: bool = False) -> str:
    v = os.getenv(name, default).strip()
    if required and not v:
        print(f"[FATAL] Variable manquante : {name}  (voir .env.example)", file=sys.stderr)
        sys.exit(1)
    return v


@dataclass(frozen=True)
class Config:
    # Riot
    riot_api_key: str = field(default_factory=lambda: env("RIOT_API_KEY", required=True))
    platform: str = field(default_factory=lambda: env("PLATFORM", default="euw1").lower())
    region: str = field(default_factory=lambda: env("REGION", default="europe").lower())

    # Discord
    discord_token: str = field(default_factory=lambda: env("DISCORD_TOKEN", required=True))
    channel_id: int = field(default_factory=lambda: int(env("DISCORD_CHANNEL_ID", required=True)))
    guild_id: int = field(default_factory=lambda: int(env("DISCORD_GUILD_ID", default="0") or "0"))

    # Anthropic (gateway custom OU api officielle)
    anthropic_base_url: str = field(default_factory=lambda: env("ANTHROPIC_BASE_URL"))
    anthropic_auth_token: str = field(default_factory=lambda: env("ANTHROPIC_AUTH_TOKEN"))
    anthropic_api_key: str = field(default_factory=lambda: env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: env("ANTHROPIC_MODEL", default="claude-opus-5"))

    # Poll
    poll_seconds: int = field(default_factory=lambda: int(env("POLL_SECONDS", default="60") or "60"))


CFG = Config()

# Ordre des tiers pour le calcul de valeur ranked (LP delta, promos)
TIER_ORDER = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
              "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
DIV_ORDER = ["IV", "III", "II", "I"]

QUEUE_NAMES = {
    400: "Normal Draft", 420: "Ranked Solo/Duo", 430: "Normal Blind",
    440: "Ranked Flex", 450: "ARAM", 700: "Clash", 720: "ARAM Clash",
    830: "Coop vs IA", 840: "Coop vs IA", 850: "Coop vs IA",
    900: "ARURF", 1020: "One for All", 1300: "Nexus Blitz",
    1400: "Ultimate Spellbook", 1700: "Arena", 1710: "Arena",
    1900: "URF", 2400: "Broutille", 0: "Custom",
}
RANKED_QUEUES = {420: "RANKED_SOLO_5x5", 440: "RANKED_FLEX_SR"}
ARENA_QUEUES = {1700, 1710}

TIER_EMOJI = {
    "IRON": "⬛", "BRONZE": "🟫", "SILVER": "⬜", "GOLD": "🟨",
    "PLATINUM": "🟩", "EMERALD": "💚", "DIAMOND": "💎",
    "MASTER": "🟪", "GRANDMASTER": "🟥", "CHALLENGER": "👑",
}


def queue_name(qid: int) -> str:
    return QUEUE_NAMES.get(qid, f"Queue {qid}")
