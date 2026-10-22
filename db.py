"""Stockage SQLite — joueurs trackés, matchs vus, historique LP."""

import aiosqlite
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("db")

DB_PATH = Path(__file__).with_name("lolbot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    puuid       TEXT PRIMARY KEY,
    game_name   TEXT NOT NULL,
    tag         TEXT NOT NULL,
    added_at    INTEGER NOT NULL,
    in_game     INTEGER DEFAULT 0,
    game_id     INTEGER,
    last_lp     INTEGER,          -- dernier league_value vu
    last_rank   TEXT              -- "GOLD II" pour affichage delta
);
CREATE TABLE IF NOT EXISTS matches (
    match_id    TEXT PRIMARY KEY,
    puuid       TEXT NOT NULL,
    queue       INTEGER,
    win         INTEGER,
    champ       TEXT,
    kills       INTEGER, deaths INTEGER, assists INTEGER,
    dur         INTEGER,
    ended       INTEGER,
    posted      INTEGER DEFAULT 0,
    FOREIGN KEY(puuid) REFERENCES players(puuid)
);
CREATE TABLE IF NOT EXISTS lp_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    puuid   TEXT, queue TEXT, league_value INTEGER, lp INTEGER,
    ts      INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT
);
"""


class DB:
    def __init__(self):
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self):
        self._db = await aiosqlite.connect(DB_PATH)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        log.info("SQLite initialisé")

    async def close(self):
        if self._db:
            await self._db.close()

    # ── players ────────────────────────────────────────────────
    async def add_player(self, puuid: str, game_name: str, tag: str, ts: int):
        await self._db.execute(
            "INSERT OR IGNORE INTO players(puuid,game_name,tag,added_at) VALUES(?,?,?,?)",
            (puuid, game_name, tag, ts))
        await self._db.commit()

    async def remove_player(self, puuid: str):
        await self._db.execute("DELETE FROM players WHERE puuid=?", (puuid,))
        await self._db.commit()

    async def players(self) -> list[aiosqlite.Row]:
        async with self._db.execute("SELECT * FROM players") as c:
            return await c.fetchall()

    async def set_ingame(self, puuid: str, in_game: bool, game_id: Optional[int]):
        await self._db.execute(
            "UPDATE players SET in_game=?, game_id=? WHERE puuid=?",
            (1 if in_game else 0, game_id, puuid))
        await self._db.commit()

    async def set_rank(self, puuid: str, league_value: int, rank_str: str):
        await self._db.execute(
            "UPDATE players SET last_lp=?, last_rank=? WHERE puuid=?",
            (league_value, rank_str, puuid))
        await self._db.commit()

    # ── matches ────────────────────────────────────────────────
    async def match_seen(self, match_id: str) -> bool:
        async with self._db.execute("SELECT 1 FROM matches WHERE match_id=?", (match_id,)) as c:
            return await c.fetchone() is not None

    async def record_match(self, match_id: str, puuid: str, queue: int, win: bool,
                           champ: str, k: int, d: int, a: int, dur: int, ended: int):
        await self._db.execute(
            "INSERT OR IGNORE INTO matches(match_id,puuid,queue,win,champ,kills,deaths,assists,dur,ended,posted)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,1)",
            (match_id, puuid, queue, 1 if win else 0, champ, k, d, a, dur, ended))
        await self._db.commit()

    async def history(self, puuid: str, limit: int = 10) -> list[aiosqlite.Row]:
        async with self._db.execute(
            "SELECT * FROM matches WHERE puuid=? ORDER BY ended DESC LIMIT ?",
            (puuid, limit)) as c:
            return await c.fetchall()

    async def count_queue(self, puuid: str, queue: int, limit: int = 50) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM matches WHERE puuid=? AND queue=? ORDER BY ended DESC LIMIT ?)",
            (puuid, queue, limit)) as c:
            return (await c.fetchone())[0]

    # ── streaks ────────────────────────────────────────────────
    async def streak(self, puuid: str) -> tuple[str, int]:
        """Retourne ('W',n) ou ('L',n) — la série en cours sur les ranked."""
        async with self._db.execute(
            "SELECT win FROM matches WHERE puuid=? AND queue IN (420,440) ORDER BY ended DESC LIMIT 20",
            (puuid,)) as c:
            rows = await c.fetchall()
        if not rows:
            return ("", 0)
        first = rows[0]["win"]
        n = 0
        for r in rows:
            if r["win"] == first:
                n += 1
            else:
                break
        return ("W" if first else "L", n)

    # ── lp_history ─────────────────────────────────────────────
    async def record_lp(self, puuid: str, queue: str, league_value: int, lp: int, ts: int):
        await self._db.execute(
            "INSERT INTO lp_history(puuid,queue,league_value,lp,ts) VALUES(?,?,?,?,?)",
            (puuid, queue, league_value, lp, ts))
        await self._db.commit()

    # ── meta ───────────────────────────────────────────────────
    async def get_meta(self, key: str) -> Optional[str]:
        async with self._db.execute("SELECT value FROM meta WHERE key=?", (key,)) as c:
            r = await c.fetchone()
            return r[0] if r else None

    async def set_meta(self, key: str, value: str):
        await self._db.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        await self._db.commit()
