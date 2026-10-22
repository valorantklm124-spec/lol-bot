"""Client Anthropic — supporte l'API officielle ET les gateways custom (Bearer token).

Parsing robuste : les réponses peuvent contenir ThinkingBlock avant le texte.
"""

import logging
from typing import Optional

import anthropic

from config import CFG

log = logging.getLogger("ai")


def _build() -> anthropic.AsyncAnthropic:
    if CFG.anthropic_base_url:
        if not CFG.anthropic_auth_token:
            raise RuntimeError("ANTHROPIC_BASE_URL défini mais ANTHROPIC_AUTH_TOKEN manquant")
        return anthropic.AsyncAnthropic(
            base_url=CFG.anthropic_base_url,
            auth_token=CFG.anthropic_auth_token)
    if not CFG.anthropic_api_key:
        raise RuntimeError("Ni ANTHROPIC_BASE_URL/AUTH_TOKEN ni ANTHROPIC_API_KEY dans .env")
    return anthropic.AsyncAnthropic(api_key=CFG.anthropic_api_key)


_client: Optional[anthropic.AsyncAnthropic] = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = _build()
    return _client


def extract_text(resp) -> str:
    """Concatène les blocs texte, ignore thinking/tool_use."""
    parts = []
    for b in getattr(resp, "content", []):
        if getattr(b, "type", None) == "text" and getattr(b, "text", None):
            parts.append(b.text)
    return " ".join(parts).strip()


async def comment(prompt: str, max_tokens: int = 500, retries: int = 2) -> str:
    """Appel Anthropic avec retry. Retourne le texte ou un fallback."""
    c = client()
    for attempt in range(retries):
        try:
            r = await c.messages.create(
                model=CFG.anthropic_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = extract_text(r)
            if text:
                return text
            log.warning(f"Réponse sans texte (attempt {attempt+1}) : {r.content!r}")
        except Exception as e:
            log.warning(f"Anthropic error (attempt {attempt+1}) : {type(e).__name__} {e}")
            if attempt + 1 < retries:
                import asyncio
                await asyncio.sleep(2 * (attempt + 1))
    return "(commentaire indisponible)"
