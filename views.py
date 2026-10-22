"""Vues Discord interactives — boutons 'Demander à l'IA' avec modal."""

import logging

import discord

import ai
from prompts import ASK, ASK_SYSTEM

log = logging.getLogger("views")

MAX_ANSWER = 3800  # marge sous la limite Discord 4096


class AskModal(discord.ui.Modal, title="Poser une question sur cette game"):
    question = discord.ui.TextInput(
        label="Ta question",
        placeholder="Ex: mon CS/min était correct pour mon poste ?",
        style=discord.TextStyle.short,
        max_length=300,
    )

    def __init__(self, context: str, thinking_message):
        super().__init__()
        self.context = context
        self._thinking = thinking_message

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        prompt = ASK.format(context=self.context, question=self.question.value)
        # on passe le contexte système en premier message "system-like"
        full = f"{ASK_SYSTEM}\n\n{prompt}"
        answer = await ai.comment(full, max_tokens=1200)
        if len(answer) > MAX_ANSWER:
            answer = answer[:MAX_ANSWER] + "…"
        e = discord.Embed(title="Analyse IA", description=answer, color=0x8B5CF6)
        e.set_footer(text=f"Q : {self.question.value[:180]}")
        await interaction.followup.send(embed=e)


class AskView(discord.ui.View):
    """Bouton persistant 'Demander à l'IA' attaché à un embed de game."""

    def __init__(self, context: str, thinking_message=None, timeout: float = 86400):
        super().__init__(timeout=timeout)
        self.context = context
        self._thinking = thinking_message

    @discord.ui.button(label="Demander à l'IA", style=discord.ButtonStyle.secondary,
                       emoji="✨", custom_id="lolbot:ask")
    async def ask(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AskModal(self.context, self._thinking))


class RecapView(discord.ui.View):
    """Boutons de bas de récap : refresh + générer graphe."""

    def __init__(self, callback, timeout: float = 300):
        super().__init__(timeout=timeout)
        self._cb = callback

    @discord.ui.button(label="Rafraîchir", style=discord.ButtonStyle.primary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self._cb(interaction)
