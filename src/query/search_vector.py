"""
Busca vetorial para perguntas abertas (Fase 4).

Embedda a pergunta do vendedor e busca os chunks mais similares via
pgvector (indice HNSW, distancia de cosseno).

Decisoes de desenho:
- LIMIAR_SIMILARIDADE: abaixo dele, o chunk nao entra no resultado.
  Calibrado empiricamente na Fase 2: respostas reais aparecem com
  score >= ~0.55 e destacadas; scores comprimidos abaixo de ~0.50
  indicam ausencia de resposta na base. Resultado vazio = "nao
  encontrei" honesto (regra de ouro n. 1 do design original).
- filtro_produto_codigo opcional: perguntas abertas sobre um produto
  especifico restringem a busca aos chunks daquele produto.
- Procedencia sempre presente (documento fonte e tipo) para citacao.

Injecao de conexao: aceita um parametro opcional `conn`. Producao
chama sem argumento; testes passam a conexao da transacao para
compartilhar rollback no teardown.
"""

import logging
from dataclasses import dataclass

from src.db import _conn_or_get
from src.embeddings import embed_texts

logger = logging.getLogger(__name__)

# Similaridade de cosseno minima para um chunk ser considerado resposta.
# Ver docstring do modulo para a base empirica.
LIMIAR_SIMILARIDADE = 0.50

# Quantos chunks retornar no maximo.
TOP_K = 5

SQL_BUSCA_VETORIAL = """
    SELECT c.documento_id,
           c.chunk_index,
           c.produto_codigo,
           c.texto,
           1 - (c.embedding <=> %(query_embedding)s::vector) AS similaridade,
           d.nome_arquivo,
           d.tipo
    FROM chunks c
    JOIN documentos d ON d.id = c.documento_id
    WHERE 1 - (c.embedding <=> %(query_embedding)s::vector) >= %(limiar)s
      AND d.revogado_em IS NULL  -- migrations/004_soft_delete.sql, principio 4
      AND (%(produto_codigo)s::integer IS NULL
           OR c.produto_codigo = %(produto_codigo)s)
    ORDER BY c.embedding <=> %(query_embedding)s::vector
    LIMIT %(top_k)s
"""


@dataclass(frozen=True)
class ChunkEncontrado:
    """Chunk retornado pela busca vetorial, com procedencia."""

    documento_id: str
    chunk_index: int
    produto_codigo: int | None
    texto: str
    similaridade: float
    documento_fonte: str
    tipo_documento: str | None  # "FT" | "FDS" | "LAUDO" | None


def buscar_chunks(
    pergunta: str,
    filtro_produto_codigo: int | None = None,
    top_k: int = TOP_K,
    limiar: float = LIMIAR_SIMILARIDADE,
    conn=None,
) -> list[ChunkEncontrado]:
    """
    Busca os chunks mais similares a pergunta.

    Args:
        pergunta: texto da pergunta do vendedor.
        filtro_produto_codigo: se informado, restringe aos chunks do
            produto (perguntas abertas sobre produto especifico).
        top_k: maximo de chunks retornados.
        limiar: similaridade minima (abaixo, o chunk e descartado).
        conn: conexao opcional para compartilhar transacao (uso em testes).

    Returns:
        Lista ordenada por similaridade decrescente. Lista VAZIA
        significa "a base nao tem resposta para isso" — a camada de
        resposta deve dizer isso honestamente, nunca inventar.
    """
    pergunta = (pergunta or "").strip()
    if not pergunta:
        return []

    query_embedding = embed_texts([pergunta])[0]

    with _conn_or_get(conn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                SQL_BUSCA_VETORIAL,
                {
                    "query_embedding": str(query_embedding),
                    "limiar": limiar,
                    "produto_codigo": filtro_produto_codigo,
                    "top_k": top_k,
                },
            )
            resultados = [
                ChunkEncontrado(
                    documento_id=str(row[0]),
                    chunk_index=row[1],
                    produto_codigo=row[2],
                    texto=row[3],
                    similaridade=round(float(row[4]), 4),
                    documento_fonte=row[5],
                    tipo_documento=row[6],
                )
                for row in cur.fetchall()
            ]

    logger.info(
        "Busca vetorial: %d chunks acima do limiar %.2f (filtro_produto=%s)",
        len(resultados),
        limiar,
        filtro_produto_codigo,
    )
    return resultados