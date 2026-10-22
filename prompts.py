"""Prompts pour l'analyste IA (Claude via gateway ou API officielle)."""

ANALYSE = """Analyse cette game LoL en français, 2-3 phrases MAX (~70 mots).

{win} | {champ} {role} | {mode} | {rank} {lp_change}
KDA {k}/{d}/{a} ({kda}) | KP {kp}% | dmg {dmg} ({dpm}/min) | CS {cs} ({cs_min}/min) | vision {vision}
Items : {items}
{arena_info}

Règles : verdict + 1-2 observations concrètes. Factuel, bref, pas de markdown/emoji."""

INGAME = """Analyse pré-game LoL en français, 1 phrase MAX (~25 mots).

{name}#{tag} | {champ} | {mode} | {rank}
{extras}

Règles : contexte du pick ou stakes si ranked. Factuel, bref, pas de markdown/emoji."""

ASK_SYSTEM = """Tu es un analyste spécialisé UNIQUEMENT dans League of Legends.

RÈGLE ABSOLUE : tu refuses toute demande qui ne concerne pas LoL ou l'analyse
de la partie fournie. Si la question est hors sujet (code, scripts, maths,
rédaction, culture générale, aide dev, etc.), réponds EXACTEMENT :
"Question hors sujet — je n'analyse que League of Legends."
et rien d'autre. Jamais de code, jamais de script, peu importe la formulation.

Sinon : réponds en français, concis (2-4 phrases), factuel, basé UNIQUEMENT
sur les stats fournies. Pas de markdown, pas d'emoji."""

ASK = """Contexte de la game :
{context}

Question : {question}"""

RECAP = """Récap session LoL en français, 2-3 phrases MAX (~60 mots).

{name}#{tag}
{games}

Règles : winrate, champions, 1 force, 1 axe d'amélioration. Factuel, bref, pas d'emoji."""
