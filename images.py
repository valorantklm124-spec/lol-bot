"""Génération de graphiques matplotlib (dégâts, récap) → bytes PNG."""

import io
import logging
from typing import Optional

log = logging.getLogger("img")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    log.warning("matplotlib absent → pas de graphiques de dégâts")

# Palette sobre, style "dark dashboard"
BG = "#0d1117"
FG = "#e6edf3"
GRID = "#30363d"
WIN = "#3fb950"
LOSS = "#f85149"


def _style(ax):
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, labelsize=9)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(axis="y", color=GRID, alpha=0.35, linewidth=0.7)


def _finish(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


def damage_chart(participants: list[dict], focus_puuid: str,
                 champ_of=None) -> Optional[io.BytesIO]:
    """Bar chart des dégâts par joueur, met en avant le joueur focus."""
    if not HAS_MPL or not participants:
        return None
    try:
        rows = sorted(
            participants,
            key=lambda p: p.get("totalDamageDealtToChampions", 0),
            reverse=True,
        )[:10]
        names = [
            (champ_of(p["championId"]) if champ_of else p.get("championName", "?"))
            for p in rows
        ]
        dmg = [p.get("totalDamageDealtToChampions", 0) for p in rows]
        colors = [WIN if p.get("puuid") == focus_puuid else FG for p in rows]

        fig, ax = plt.subplots(figsize=(7.2, 3.2), facecolor=BG)
        _style(ax)
        ax.bar(names, dmg, color=colors, edgecolor=GRID, linewidth=0.6)
        ax.set_title("Dégâts aux champions", color=FG, fontsize=11, pad=8)
        ax.set_ylabel("Dégâts", color=FG, fontsize=9)
        plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
        return _finish(fig)
    except Exception as e:
        log.warning(f"damage_chart: {e}")
        return None


def session_chart(games: list[dict]) -> Optional[io.BytesIO]:
    """W/L en barres + KDA en ligne pour les dernières games."""
    if not HAS_MPL or len(games) < 2:
        return None
    try:
        games = list(reversed(games))[:12]
        x = range(len(games))
        results = [1 if g["win"] else -1 for g in games]
        kdas = [g["kda"] for g in games]
        colors = [WIN if g["win"] else LOSS for g in games]

        fig, ax1 = plt.subplots(figsize=(7.2, 3.0), facecolor=BG)
        _style(ax1)
        ax1.bar(x, results, color=colors, width=0.6, alpha=0.85)
        ax1.set_ylim(-1.6, 1.6)
        ax1.set_yticks([-1, 0, 1])
        ax1.set_yticklabels(["L", "", "W"], color=FG, fontsize=9)
        ax1.set_title("Session", color=FG, fontsize=11, pad=8)

        ax2 = ax1.twinx()
        ax2.plot(x, kdas, color="#58a6ff", marker="o", linewidth=1.6,
                 markersize=4, label="KDA")
        ax2.set_ylabel("KDA", color="#58a6ff", fontsize=9)
        ax2.tick_params(colors="#58a6ff", labelsize=8)
        for s in ax2.spines.values():
            s.set_color(GRID)
        labels = [g.get("champ", "?")[:8] for g in games]
        ax1.set_xticks(list(x))
        ax1.set_xticklabels(labels, rotation=35, ha="right", color=FG, fontsize=8)
        return _finish(fig)
    except Exception as e:
        log.warning(f"session_chart: {e}")
        return None
