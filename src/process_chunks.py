"""
Orquestrador do pipeline de chunking + embeddings (Fase 2).

Fluxo por documento elegivel:
    1. Resolve produto_codigo (Opcao A: preenche se o documento mapeia
       para exatamente 1 produto; NULL se multi-produto ou sem vinculo).
    2. Divide texto_extraido em chunks (src.chunking).
    3. Gera embeddings em lote (src.embeddings).
    4. Grava em chunks com DELETE-then-INSERT, em transacao atomica
       por documento (falhou -> rollback so daquele documento).

Uso:
    python -m src.process_chunks              # so documentos sem chunks
    python -m src.process_chunks --force      # reprocessa todos
    python -m src.process_chunks --limit 5    # processa no maximo 5
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field

from src.chunking import chunk_text
from src.config import DATABASE_URL_ADMIN
from src.db import get_connection
from src.embeddings import embed_texts

logger = logging.getLogger(__name__)

# Preco do text-embedding-3-small (USD por 1M tokens) — apenas para
# estimativa informativa no resumo final.
EMBEDDING_COST_PER_MILLION_TOKENS_USD = 0.02

SQL_ELIGIBLE_DOCUMENTS = """
    SELECT d.id, d.texto_extraido
    FROM documentos d
    WHERE d.texto_extraido IS NOT NULL
      AND length(trim(d.texto_extraido)) > 0
      AND (d.is_ocr IS NULL OR d.is_ocr = FALSE)
      AND (%(force)s OR NOT EXISTS (
          SELECT 1 FROM chunks c WHERE c.documento_id = d.id
      ))
    ORDER BY d.id
"""

SQL_RESOLVE_PRODUTO_CODIGO = """
    SELECT MIN(produto_codigo) AS codigo, COUNT(*) AS total
    FROM produto_documento
    WHERE documento_id = %(documento_id)s
"""

SQL_DELETE_CHUNKS = "DELETE FROM chunks WHERE documento_id = %(documento_id)s"

SQL_INSERT_CHUNK = """
    INSERT INTO chunks (documento_id, produto_codigo, chunk_index, texto,
                        embedding, num_tokens)
    VALUES (%(documento_id)s, %(produto_codigo)s, %(chunk_index)s,
            %(texto)s, %(embedding)s, %(num_tokens)s)
"""


@dataclass
class PipelineSummary:
    """Acumula metricas da execucao para o resumo final."""

    processed: int = 0
    skipped_no_chunks: int = 0
    failed: int = 0
    total_chunks: int = 0
    total_tokens: int = 0
    failed_ids: list[int] = field(default_factory=list)

    @property
    def estimated_cost_usd(self) -> float:
        return (self.total_tokens / 1_000_000) * EMBEDDING_COST_PER_MILLION_TOKENS_USD


def fetch_eligible_documents(conn, force: bool, limit: int | None) -> list[dict]:
    """Busca documentos elegiveis para chunking."""
    query = SQL_ELIGIBLE_DOCUMENTS
    if limit is not None:
        query += " LIMIT %(limit)s"
    with conn.cursor() as cur:
        cur.execute(query, {"force": force, "limit": limit})
        rows = cur.fetchall()
    return [{"id": row[0], "texto_extraido": row[1]} for row in rows]


def resolve_produto_codigo(conn, documento_id: str) -> str | None:
    """
    Opcao A: retorna o codigo do produto se o documento mapeia para
    exatamente 1 produto; caso contrario, None.
    """
    with conn.cursor() as cur:
        cur.execute(SQL_RESOLVE_PRODUTO_CODIGO, {"documento_id": documento_id})
        codigo, total = cur.fetchone()
    return codigo if total == 1 else None


def process_document(conn, documento_id: str, texto: str) -> tuple[int, int]:
    """
    Processa um documento: chunking, embeddings e gravacao.

    Executa dentro da transacao corrente da conexao; o chamador decide
    commit ou rollback.

    Returns:
        (num_chunks, num_tokens) gravados. (0, 0) se o texto nao gerou
        chunks validos.
    """
    chunks = chunk_text(texto)
    if not chunks:
        return 0, 0

    embeddings = embed_texts([c.texto for c in chunks])
    produto_codigo = resolve_produto_codigo(conn, documento_id)

    with conn.cursor() as cur:
        cur.execute(SQL_DELETE_CHUNKS, {"documento_id": documento_id})
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            cur.execute(
                SQL_INSERT_CHUNK,
                {
                    "documento_id": documento_id,
                    "produto_codigo": produto_codigo,
                    "chunk_index": chunk.chunk_index,
                    "texto": chunk.texto,
                    "embedding": str(embedding),
                    "num_tokens": chunk.num_tokens,
                },
            )

    return len(chunks), sum(c.num_tokens for c in chunks)


def run(force: bool = False, limit: int | None = None) -> PipelineSummary:
    """Executa o pipeline completo e retorna o resumo."""
    summary = PipelineSummary()

    # Ingestao escreve no acervo: conecta como botuser. A role da aplicacao
    # (bot_app) so le -- ver db/provisioning/001b_bot_app_grants.sql.
    with get_connection(DATABASE_URL_ADMIN) as conn:
        documents = fetch_eligible_documents(conn, force=force, limit=limit)
        logger.info("Documentos elegiveis: %d (force=%s)", len(documents), force)

        for doc in documents:
            documento_id = doc["id"]
            try:
                num_chunks, num_tokens = process_document(
                    conn, documento_id, doc["texto_extraido"]
                )
                conn.commit()  # fecha a transacao DESTE documento

                if num_chunks == 0:
                    summary.skipped_no_chunks += 1
                    logger.warning(
                        "Documento %s nao gerou chunks validos (texto curto?)",
                        documento_id,
                    )
                else:
                    summary.processed += 1
                    summary.total_chunks += num_chunks
                    summary.total_tokens += num_tokens
                    logger.info(
                        "Documento %s: %d chunks, %d tokens",
                        documento_id,
                        num_chunks,
                        num_tokens,
                    )

            except Exception:
                conn.rollback()  # desfaz so este documento
                summary.failed += 1
                summary.failed_ids.append(documento_id)
                logger.exception("Falha no documento %s (rollback executado)", documento_id)

    return summary


def print_summary(summary: PipelineSummary) -> None:
    print("\n" + "=" * 50)
    print("RESUMO DO PIPELINE")
    print("=" * 50)
    print(f"Documentos processados:  {summary.processed}")
    print(f"Sem chunks validos:      {summary.skipped_no_chunks}")
    print(f"Falhas:                  {summary.failed}")
    if summary.failed_ids:
        print(f"IDs com falha:           {summary.failed_ids}")
    print(f"Total de chunks:         {summary.total_chunks}")
    print(f"Total de tokens:         {summary.total_tokens}")
    print(f"Custo estimado (USD):    ${summary.estimated_cost_usd:.4f}")
    print("=" * 50)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline de chunking + embeddings")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocessa todos os documentos elegiveis, mesmo os que ja tem chunks",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Processa no maximo N documentos (util para teste)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    summary = run(force=args.force, limit=args.limit)
    print_summary(summary)

    return 1 if summary.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())