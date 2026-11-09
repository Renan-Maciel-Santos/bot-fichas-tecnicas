"""
Modulo de chunking de texto para o pipeline RAG.

Divide o texto extraido dos documentos (documentos.texto_extraido) em
chunks dimensionados por tokens, prontos para geracao de embeddings.

Caracteristicas:
- Funcao pura: texto entra, lista de chunks sai (sem I/O, sem banco).
- Tamanho medido em tokens (tiktoken, encoding cl100k_base — mesmo
  usado pelo text-embedding-3-small).
- Hierarquia de separadores: paragrafo -> linha -> sentenca -> palavra
  -> caractere.
- Chunks menores que MIN_CHUNK_SIZE tokens sao descartados (ruido).
"""

from dataclasses import dataclass

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import CHUNK_OVERLAP, CHUNK_SIZE, MIN_CHUNK_SIZE

# Encoding usado pelos modelos de embedding da OpenAI (text-embedding-3-*)
ENCODING_NAME = "cl100k_base"

# Hierarquia: paragrafo -> linha -> sentenca -> palavra -> caractere
SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

_encoding = tiktoken.get_encoding(ENCODING_NAME)

_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
    encoding_name=ENCODING_NAME,
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=SEPARATORS,
)


@dataclass(frozen=True)
class Chunk:
    """Representa um chunk de texto pronto para embedding."""

    chunk_index: int
    texto: str
    num_tokens: int


def count_tokens(texto: str) -> int:
    """Conta tokens de um texto usando o encoding do modelo de embedding."""
    return len(_encoding.encode(texto))


def chunk_text(texto: str) -> list[Chunk]:
    """
    Divide um texto em chunks dimensionados por tokens.

    Args:
        texto: Texto completo do documento (ex.: documentos.texto_extraido).

    Returns:
        Lista de Chunk ordenada por chunk_index (0-based). Chunks com menos
        de MIN_CHUNK_SIZE tokens sao descartados; o chunk_index e sequencial
        apos o descarte (sem buracos).
    """
    if not texto or not texto.strip():
        return []

    raw_chunks = _splitter.split_text(texto)

    chunks: list[Chunk] = []
    for raw in raw_chunks:
        cleaned = raw.strip()
        if not cleaned:
            continue

        num_tokens = count_tokens(cleaned)
        if num_tokens < MIN_CHUNK_SIZE:
            continue

        chunks.append(
            Chunk(
                chunk_index=len(chunks),
                texto=cleaned,
                num_tokens=num_tokens,
            )
        )

    return chunks