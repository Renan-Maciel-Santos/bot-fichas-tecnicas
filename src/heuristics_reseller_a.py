"""
Heuristicas de dominio de uma revenda (exemplo de plugin).

O classificador core (classify_doc_type.py) e agnostico: regex generico para
FT/FDS/LAUDO. Mas este cliente tem dois padroes proprios de FT que nao usam
essas palavras:

1. Template REF: cabecalho fixo "COD REF / PRODUTO / MARCA" em fichas
   tecnicas de papeis e toalhas.
2. Tabela tecnica de papel: vocabulario "Gramatura g/m2 / pH / Absorcao /
   Alvura / maciez" tipico de FT de papel de higiene.

Ambas assinaturas indicaram FT com alta especificidade no acervo real.

Este modulo e opt-in: so entra em cena quando o chamador o injeta em
`classify_doc_type(..., heuristicas=[...])`. O core continua portavel.

Se o template do cliente mudar amanha, aqui e o unico ponto de manutencao.
"""

from __future__ import annotations

import re
import unicodedata

from src.classify_doc_type import Signal


# ============================================================
# Heuristica 1: template REF
# ============================================================
_RE_REF = re.compile(r"\bC[ÓO]D\s*REF\b", re.IGNORECASE)
_RE_PRODUTO_TAG = re.compile(r"^\s*PRODUTO\s*$", re.IGNORECASE | re.MULTILINE)
_RE_MARCA_TAG = re.compile(r"^\s*MARCA\s*$", re.IGNORECASE | re.MULTILINE)

_HEAD_REF = 500  # cabecalho


def _detectar_template_ref(texto: str) -> Signal | None:
    head = texto[:_HEAD_REF]
    if (
        _RE_REF.search(head)
        and _RE_PRODUTO_TAG.search(head)
        and _RE_MARCA_TAG.search(head)
    ):
        return Signal(
            tipo="FT",
            fonte="heuristica:ref",
            motivo="template REF no cabecalho (COD REF + PRODUTO + MARCA)",
        )
    return None


# ============================================================
# Heuristica 2: tabela tecnica de papel
# ============================================================
# Exige "gramatura" + pelo menos 2 secundarios para reduzir falso positivo
# (uma FDS pode mencionar "gramatura" isoladamente).
_INDICADORES_PAPEL_SECUNDARIOS = (
    "absorcao",
    "alvura",
    "maciez",
    "pintas",
    "umidade",
    "g/m",
)


def _normalizar(texto: str) -> str:
    nfd = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _detectar_tabela_papel(texto: str) -> Signal | None:
    head = _normalizar(texto[:2000])
    if "gramatura" not in head:
        return None
    hits = sum(1 for kw in _INDICADORES_PAPEL_SECUNDARIOS if kw in head)
    if hits >= 2:
        return Signal(
            tipo="FT",
            fonte="heuristica:tabela_papel",
            motivo=f"tabela tecnica de papel (gramatura + {hits} indicadores)",
        )
    return None


# ============================================================
# API publica: assinatura HeuristicDetector
# ============================================================

def detectar_sinais_reseller_a(
    nome_arquivo: str,
    texto_extraido: str | None,
) -> list[Signal]:
    """Detector de sinais especificos da revenda A.

    Uso:
        from src.classify_doc_type import classify_doc_type
        from src.heuristics_reseller_a import detectar_sinais_reseller_a

        resultado = classify_doc_type(
            nome, texto,
            heuristicas=[detectar_sinais_reseller_a],
        )
    """
    if not texto_extraido:
        return []

    sinais: list[Signal] = []

    # REF e conclusivo — se casou, nao precisa checar a outra.
    ref = _detectar_template_ref(texto_extraido)
    if ref:
        sinais.append(ref)
        return sinais

    tabela = _detectar_tabela_papel(texto_extraido)
    if tabela:
        sinais.append(tabela)

    return sinais