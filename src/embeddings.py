"""
Modulo de geracao de embeddings via OpenAI.

Converte lotes de textos em vetores usando o modelo definido em
config.EMBEDDING_MODEL (text-embedding-3-small -> vector(1536)).

Caracteristicas:
- Batching: multiplos textos por chamada de API (menos requests,
  menos latencia, mesmo custo por token).
- Retry com backoff exponencial para erros transitorios (rate limit,
  timeout, erro de servidor).
- Sem estado e sem acesso a banco: recebe textos, devolve vetores.
"""

import logging
import time

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from src.config import EMBEDDING_MODEL
from src.openai_client import get_client

logger = logging.getLogger(__name__)

# Limite de textos por chamada. A API aceita ate 2048 inputs, mas lotes
# menores reduzem o impacto de uma falha e mantem os requests leves.
BATCH_SIZE = 100

# Politica de retry para erros transitorios
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
BACKOFF_MULTIPLIER = 2.0

# Erros que valem retry (transitorios). Erros de autenticacao ou de
# requisicao invalida NAO estao aqui de proposito: retry nao os resolve.
TRANSIENT_ERRORS = (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)


def _embed_batch_with_retry(batch: list[str]) -> list[list[float]]:
    """
    Gera embeddings para um lote, com retry e backoff exponencial.

    Raises:
        Exception: a ultima excecao transitoria, se MAX_RETRIES for
            excedido, ou imediatamente para erros nao-transitorios.
    """
    client = get_client()
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch,
            )
            # A API garante a mesma ordem dos inputs, mas ordenamos por
            # index por seguranca (contrato explicito > implicito).
            sorted_data = sorted(response.data, key=lambda item: item.index)
            return [item.embedding for item in sorted_data]

        except TRANSIENT_ERRORS as exc:
            if attempt == MAX_RETRIES:
                logger.error(
                    "Falha definitiva apos %d tentativas: %s", MAX_RETRIES, exc
                )
                raise
            logger.warning(
                "Erro transitorio (tentativa %d/%d): %s. Aguardando %.1fs...",
                attempt,
                MAX_RETRIES,
                exc,
                backoff,
            )
            time.sleep(backoff)
            backoff *= BACKOFF_MULTIPLIER

    # Inalcancavel (o loop sempre retorna ou levanta), mas explicito
    # para o type checker.
    raise RuntimeError("Fluxo de retry terminou sem retorno nem excecao.")


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Gera embeddings para uma lista de textos, em lotes.

    Args:
        texts: Lista de textos (ex.: chunks de um documento). Nao pode
            conter strings vazias — a API rejeita input vazio.

    Returns:
        Lista de vetores (1536 floats cada), na mesma ordem dos textos.

    Raises:
        ValueError: se a lista contiver texto vazio.
    """
    if not texts:
        return []

    if any(not t.strip() for t in texts):
        raise ValueError("A lista contem texto vazio; a API rejeita input vazio.")

    embeddings: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        logger.info(
            "Gerando embeddings do lote %d-%d de %d textos...",
            start + 1,
            start + len(batch),
            len(texts),
        )
        embeddings.extend(_embed_batch_with_retry(batch))

    return embeddings