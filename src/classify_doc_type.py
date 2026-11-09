"""
Classificador de tipo de documento (FT, FDS, LAUDO).

Motivacao:
- Preenche `documentos.tipo`, hoje NULL nos 22 documentos.
- Habilita a regra de ouro "resposta cita o tipo do documento fonte"
  (o motor precisa dizer "segundo a FDS de X" vs "segundo a FT de Y").
- Roda no backfill dos existentes e sera chamado no pipeline de ingestao
  futuro, para que documento novo ja entre com tipo preenchido.

Desenho:
- Funcao pura (nao conhece banco, nao conhece filesystem).
- Duas camadas deterministicas: regex no nome + vocabulario no texto.
- Terceira camada opcional via plug-in: heuristicas de dominio recebidas
  como parametro (Dependency Inversion + Open/Closed).
- Sem LLM: 22 documentos, universo pequeno, regras deterministicas
  cobrem >95% e sao auditaveis.

Contrato:
- ClassificationResult(tipo, confianca, motivos)
  - tipo: "FT" | "FDS" | "LAUDO" | None
  - confianca: 1.0 (>=2 fontes concordam) | 0.7 (1 fonte) |
               0.3 (conflito) | 0.0 (nada)
  - motivos: lista de strings explicando quais sinais dispararam.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

TIPOS_VALIDOS: tuple[str, ...] = ("FT", "FDS", "LAUDO")


@dataclass(frozen=True)
class Signal:
    """Sinal detectado no documento. Contribui para a pontuacao de um tipo."""
    tipo: str          # "FT" | "FDS" | "LAUDO"
    fonte: str         # "nome" | "texto" | "heuristica:xxx"
    motivo: str        # descricao legivel para auditoria


@dataclass(frozen=True)
class ClassificationResult:
    tipo: str | None
    confianca: float
    motivos: list[str] = field(default_factory=list)


# ============================================================
# Camada 1: regex no nome do arquivo
# ============================================================
# \b garante limite de palavra: nao casa "FT" em "SOFT".
_RE_FDS_NOME = re.compile(r"\b(FDS|FISPQ)\b", re.IGNORECASE)
_RE_FT_NOME = re.compile(r"\b(FT|ficha[\s_-]*t[eé]cnica)\b", re.IGNORECASE)
_RE_LAUDO_NOME = re.compile(r"\blaudo\b", re.IGNORECASE)


# ============================================================
# Camada 2: vocabulario no texto extraido
# ============================================================
_TEXTO_HEAD_CHARS = 3000  # cabecalho + primeira pagina cobrem tudo

_VOCAB_FDS = (
    "ficha de dados de seguranca",
    "ficha com dados de seguranca",
    "fispq",
    "nbr 14725",  # norma que rege FDS no Brasil
)
_VOCAB_FT = ("ficha tecnica",)
_VOCAB_LAUDO = (
    "relatorio de ensaio",
    "resultado da analise",
    "laudo",
)


def _normalizar(texto: str) -> str:
    """Lowercase + remove acentos, para casamento robusto."""
    nfd = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _detectar_sinais_nome(nome_arquivo: str) -> list[Signal]:
    sinais: list[Signal] = []
    if _RE_FDS_NOME.search(nome_arquivo):
        sinais.append(Signal("FDS", "nome", "'FDS' ou 'FISPQ' no nome"))
    if _RE_FT_NOME.search(nome_arquivo):
        sinais.append(Signal("FT", "nome", "'FT' ou 'ficha tecnica' no nome"))
    if _RE_LAUDO_NOME.search(nome_arquivo):
        sinais.append(Signal("LAUDO", "nome", "'laudo' no nome"))
    return sinais


def _detectar_sinais_texto(texto: str | None) -> list[Signal]:
    if not texto:
        return []
    head = _normalizar(texto[:_TEXTO_HEAD_CHARS])
    sinais: list[Signal] = []

    for kw in _VOCAB_FDS:
        if kw in head:
            sinais.append(Signal("FDS", "texto", f"vocabulario FDS: '{kw}'"))
            break  # um sinal por camada, evita inflar pontuacao
    for kw in _VOCAB_FT:
        if kw in head:
            sinais.append(Signal("FT", "texto", f"vocabulario FT: '{kw}'"))
            break
    for kw in _VOCAB_LAUDO:
        if kw in head:
            sinais.append(Signal("LAUDO", "texto", f"vocabulario LAUDO: '{kw}'"))
            break
    return sinais


# ============================================================
# API publica
# ============================================================
# Assinatura de um detector de heuristicas: (nome, texto) -> Iterable[Signal].
# O classificador nao conhece heuristicas especificas de cliente.
HeuristicDetector = Callable[[str, str | None], Iterable[Signal]]


def classify_doc_type(
    nome_arquivo: str,
    texto_extraido: str | None,
    heuristicas: list[HeuristicDetector] | None = None,
) -> ClassificationResult:
    """Classifica um documento como FT, FDS, LAUDO ou None.

    Args:
        nome_arquivo: nome do arquivo (sem path).
        texto_extraido: texto extraido do PDF (None ou vazio para OCR).
        heuristicas: detectores opcionais de sinais especificos de dominio.

    Returns:
        ClassificationResult com tipo, confianca e motivos.
    """
    sinais: list[Signal] = []
    sinais.extend(_detectar_sinais_nome(nome_arquivo))
    sinais.extend(_detectar_sinais_texto(texto_extraido))

    if heuristicas:
        for detector in heuristicas:
            try:
                sinais.extend(detector(nome_arquivo, texto_extraido))
            except Exception:
                # Heuristica quebrada nao pode derrubar o classificador.
                logger.exception(
                    "Heuristica %s falhou em %s; ignorando.",
                    getattr(detector, "__name__", "<anonima>"),
                    nome_arquivo,
                )

    if not sinais:
        return ClassificationResult(tipo=None, confianca=0.0, motivos=[])

    # Pontuacao por tipo + fontes distintas por tipo
    pontuacao: dict[str, int] = {t: 0 for t in TIPOS_VALIDOS}
    fontes_por_tipo: dict[str, set[str]] = {t: set() for t in TIPOS_VALIDOS}
    for s in sinais:
        pontuacao[s.tipo] += 1
        fontes_por_tipo[s.tipo].add(s.fonte)

    max_pont = max(pontuacao.values())
    vencedores = [t for t, v in pontuacao.items() if v == max_pont]

    # Empate entre tipos diferentes: conflito, nao classifica
    if len(vencedores) > 1:
        motivos = [f"{s.motivo} -> {s.tipo}" for s in sinais]
        return ClassificationResult(tipo=None, confianca=0.3, motivos=motivos)

    vencedor = vencedores[0]
    tem_conflito = any(v > 0 for t, v in pontuacao.items() if t != vencedor)

    if tem_conflito:
        confianca = 0.3
    else:
        confianca = 1.0 if len(fontes_por_tipo[vencedor]) >= 2 else 0.7

    motivos_vencedor = [s.motivo for s in sinais if s.tipo == vencedor]
    return ClassificationResult(
        tipo=vencedor,
        confianca=confianca,
        motivos=motivos_vencedor,
    )