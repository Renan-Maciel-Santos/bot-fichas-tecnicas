"""
Orquestrador da extracao de propriedades (Fase 3).

Fluxo por documento elegivel:
    1. Le o texto completo (documentos.texto_extraido).
    2. Extrai propriedades via LLM (src.extraction, prompt calibrado).
    3. Grava em propriedades_produto com DELETE-then-INSERT por
       documento, em transacao atomica, PRESERVANDO linhas com
       revisado_humano = TRUE.

Elegibilidade:
    - Documento vinculado a exatamente 1 produto (produto_documento);
      documentos multi-produto ficam para quando a semantica de
      atribuicao de propriedades por SKU for definida.
    - Com texto extraido nao vazio e nao-OCR.
    - Sem propriedades gravadas (padrao) ou todos (--force).

Uso:
    python -m src.process_extraction              # so documentos sem propriedades
    python -m src.process_extraction --force      # reprocessa todos
    python -m src.process_extraction --limit 2    # no maximo N documentos
    python -m src.process_extraction --dry-run    # extrai e mostra, nao grava
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field

from src.config import DATABASE_URL_ADMIN
from src.db import get_connection
from src.extraction import extract_properties

logger = logging.getLogger(__name__)

# Limiar do design original: abaixo disso, a propriedade e gravada mas
# entra na fila de revisao humana (v_pendentes_revisao).
LIMIAR_REVISAO = 0.80

SQL_ELIGIBLE_DOCUMENTS = """
    SELECT d.id, d.nome_arquivo, d.texto_extraido, MIN(pd.produto_codigo) AS produto_codigo
    FROM documentos d
    JOIN produto_documento pd ON pd.documento_id = d.id
    WHERE d.texto_extraido IS NOT NULL
      AND length(trim(d.texto_extraido)) > 0
      AND (d.is_ocr IS NULL OR d.is_ocr = FALSE)
    GROUP BY d.id, d.nome_arquivo, d.texto_extraido
    HAVING COUNT(pd.produto_codigo) = 1
       AND (%(force)s OR NOT EXISTS (
           SELECT 1 FROM propriedades_produto pp WHERE pp.documento_id = d.id
       ))
    ORDER BY d.nome_arquivo
"""

SQL_DELETE_PROPRIEDADES = """
    DELETE FROM propriedades_produto
    WHERE documento_id = %(documento_id)s
      AND revisado_humano = FALSE
"""

SQL_INSERT_PROPRIEDADE = """
    INSERT INTO propriedades_produto
        (produto_codigo, documento_id, campo, valor, valor_numerico,
         unidade, confianca)
    VALUES
        (%(produto_codigo)s, %(documento_id)s, %(campo)s, %(valor)s,
         %(valor_numerico)s, %(unidade)s, %(confianca)s)
"""


@dataclass
class ExtractionSummary:
    """Metricas da execucao para o resumo final."""

    processed: int = 0
    no_properties: int = 0
    failed: int = 0
    total_properties: int = 0
    needs_review: int = 0
    failed_files: list[str] = field(default_factory=list)


def fetch_eligible_documents(conn, force: bool, limit: int | None) -> list[dict]:
    """Busca documentos vinculados (1:1) elegiveis para extracao."""
    query = SQL_ELIGIBLE_DOCUMENTS
    if limit is not None:
        query += " LIMIT %(limit)s"
    with conn.cursor() as cur:
        cur.execute(query, {"force": force, "limit": limit})
        rows = cur.fetchall()
    return [
        {
            "id": row[0],
            "nome_arquivo": row[1],
            "texto_extraido": row[2],
            "produto_codigo": row[3],
        }
        for row in rows
    ]


def process_document(conn, doc: dict, dry_run: bool) -> tuple[int, int]:
    """
    Extrai e grava as propriedades de um documento.

    Executa dentro da transacao corrente; o chamador decide commit/rollback.

    Returns:
        (num_propriedades, num_para_revisao)
    """
    propriedades = extract_properties(doc["texto_extraido"])
    if not propriedades:
        return 0, 0

    needs_review = sum(1 for p in propriedades if p.confianca < LIMIAR_REVISAO)

    if dry_run:
        for p in propriedades:
            flag = " [REVISAO]" if p.confianca < LIMIAR_REVISAO else ""
            logger.info(
                "[DRY-RUN] %s: %s = %r (conf %.2f)%s",
                doc["nome_arquivo"],
                p.campo,
                p.valor[:60],
                p.confianca,
                flag,
            )
        return len(propriedades), needs_review

    with conn.cursor() as cur:
        cur.execute(SQL_DELETE_PROPRIEDADES, {"documento_id": doc["id"]})
        for p in propriedades:
            cur.execute(
                SQL_INSERT_PROPRIEDADE,
                {
                    "produto_codigo": doc["produto_codigo"],
                    "documento_id": doc["id"],
                    "campo": p.campo,
                    "valor": p.valor,
                    "valor_numerico": p.valor_numerico,
                    "unidade": p.unidade,
                    "confianca": p.confianca,
                },
            )

    return len(propriedades), needs_review


def run(force: bool = False, limit: int | None = None, dry_run: bool = False) -> ExtractionSummary:
    """Executa o pipeline de extracao e retorna o resumo."""
    summary = ExtractionSummary()

    # Ingestao escreve no acervo: conecta como botuser. A role da aplicacao
    # (bot_app) so le -- ver db/provisioning/001b_bot_app_grants.sql.
    with get_connection(DATABASE_URL_ADMIN) as conn:
        documents = fetch_eligible_documents(conn, force=force, limit=limit)
        logger.info(
            "Documentos elegiveis: %d (force=%s, dry_run=%s)",
            len(documents),
            force,
            dry_run,
        )

        for doc in documents:
            try:
                num_props, num_review = process_document(conn, doc, dry_run=dry_run)

                if dry_run:
                    conn.rollback()
                else:
                    conn.commit()

                if num_props == 0:
                    summary.no_properties += 1
                    logger.warning(
                        "Documento '%s' nao gerou propriedades",
                        doc["nome_arquivo"],
                    )
                else:
                    summary.processed += 1
                    summary.total_properties += num_props
                    summary.needs_review += num_review
                    logger.info(
                        "Documento '%s': %d propriedades (%d para revisao)",
                        doc["nome_arquivo"],
                        num_props,
                        num_review,
                    )

            except Exception:
                conn.rollback()
                summary.failed += 1
                summary.failed_files.append(doc["nome_arquivo"])
                logger.exception(
                    "Falha no documento '%s' (rollback executado)",
                    doc["nome_arquivo"],
                )

    return summary


def print_summary(summary: ExtractionSummary, dry_run: bool) -> None:
    modo = " (DRY-RUN — nada gravado)" if dry_run else ""
    print("\n" + "=" * 50)
    print(f"RESUMO DA EXTRACAO{modo}")
    print("=" * 50)
    print(f"Documentos processados:    {summary.processed}")
    print(f"Sem propriedades:          {summary.no_properties}")
    print(f"Falhas:                    {summary.failed}")
    if summary.failed_files:
        print(f"Arquivos com falha:        {summary.failed_files}")
    print(f"Total de propriedades:     {summary.total_properties}")
    print(f"Na fila de revisao (<{LIMIAR_REVISAO:.2f}): {summary.needs_review}")
    print("=" * 50)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extracao de propriedades via LLM")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocessa todos os documentos elegiveis (preserva revisoes humanas)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Processa no maximo N documentos",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extrai e mostra o resultado sem gravar no banco",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    summary = run(force=args.force, limit=args.limit, dry_run=args.dry_run)
    print_summary(summary, dry_run=args.dry_run)

    return 1 if summary.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())