"""
Importa a planilha Produtos.xlsx para a tabela `produtos` no Postgres.

Uso:
    python -m src.ingestion.import_excel data/Produtos.xlsx

Comportamento:
- Lê todas as linhas válidas do Excel
- Normaliza marca (uppercase, sem espaços extras) — facilita match com Drive
- Faz UPSERT (insere se não existe, atualiza se existe)
- Reporta estatísticas ao final

A coluna `texto_busca` da planilha já vem concatenada — preservamos
e usamos como base para busca textual rápida (índice GIN trgm).
"""
import argparse
import logging
import sys
from pathlib import Path

import openpyxl
from psycopg2.extras import execute_values

from src.config import DATABASE_URL_ADMIN
from src.db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# Mapeamento das colunas da planilha (ordem fixa)
COLUNAS_ESPERADAS = [
    "Código",
    "ICMS ST",
    "Descrição",
    "NCM",
    "CEST",
    "Marca",
    "Grupo Produto",
    "Peso",
    "Texto para busca",
]


def normalizar_marca(marca: str) -> str:
    """
    Normaliza o nome da marca para facilitar match com as pastas do Drive.
    Mantém em uppercase, tira espaços extras.
    """
    if not marca:
        return ""
    return " ".join(str(marca).strip().upper().split())


def normalizar_icms_st(valor) -> bool | None:
    """Converte 'SIM'/'NAO' para booleano."""
    if valor is None:
        return None
    s = str(valor).strip().upper()
    if s in ("SIM", "S", "TRUE", "1"):
        return True
    if s in ("NAO", "NÃO", "N", "FALSE", "0"):
        return False
    return None


def ler_planilha(caminho: Path) -> list[dict]:
    """Lê a planilha e retorna lista de dicts (uma por produto)."""
    log.info(f"Abrindo planilha: {caminho}")
    wb = openpyxl.load_workbook(caminho, data_only=True, read_only=True)
    ws = wb.active

    # Lê cabeçalho da primeira linha
    primeira_linha = next(ws.iter_rows(values_only=True))
    cabecalho = [str(c).strip() if c else "" for c in primeira_linha]
    log.info(f"Colunas encontradas: {cabecalho}")

    produtos = []
    erros = 0

    for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or not row[0]:
            continue  # linha vazia
        try:
            produto = {
                "codigo":        int(row[0]),
                "icms_st":       normalizar_icms_st(row[1]),
                "descricao":     str(row[2]).strip() if row[2] else "",
                "ncm":           str(row[3]).strip() if row[3] else None,
                "cest":          str(row[4]).strip() if row[4] else None,
                "marca":         normalizar_marca(row[5]),
                "grupo_produto": str(row[6]).strip() if row[6] else None,
                "peso":          float(row[7]) if row[7] is not None else None,
                "texto_busca":   str(row[8]).strip() if row[8] else None,
            }
            produtos.append(produto)
        except (ValueError, TypeError) as e:
            log.warning(f"Linha {idx} ignorada: {e}")
            erros += 1

    log.info(f"Lidos {len(produtos)} produtos válidos ({erros} erros)")
    return produtos


def inserir_produtos(produtos: list[dict]) -> dict:
    """
    Faz UPSERT dos produtos no banco.
    Retorna estatísticas de inseridos vs atualizados.
    """
    sql = """
        INSERT INTO produtos (
            codigo, descricao, marca, grupo_produto,
            ncm, cest, peso, icms_st, texto_busca
        )
        VALUES %s
        ON CONFLICT (codigo) DO UPDATE SET
            descricao     = EXCLUDED.descricao,
            marca         = EXCLUDED.marca,
            grupo_produto = EXCLUDED.grupo_produto,
            ncm           = EXCLUDED.ncm,
            cest          = EXCLUDED.cest,
            peso          = EXCLUDED.peso,
            icms_st       = EXCLUDED.icms_st,
            texto_busca   = EXCLUDED.texto_busca,
            updated_at    = NOW()
        RETURNING (xmax = 0) AS inserido
    """

    valores = [
        (
            p["codigo"], p["descricao"], p["marca"], p["grupo_produto"],
            p["ncm"], p["cest"], p["peso"], p["icms_st"], p["texto_busca"],
        )
        for p in produtos
    ]

    # Ingestao escreve no acervo: conecta como botuser. A role da aplicacao
    # (bot_app) so le -- ver db/provisioning/001b_bot_app_grants.sql.
    with get_connection(DATABASE_URL_ADMIN) as conn:
        with conn.cursor() as cur:
            resultados = execute_values(
                cur, sql, valores, fetch=True, page_size=500
            )

    inseridos = sum(1 for r in resultados if r[0])
    atualizados = len(resultados) - inseridos

    return {
        "total": len(produtos),
        "inseridos": inseridos,
        "atualizados": atualizados,
    }


def main():
    parser = argparse.ArgumentParser(description="Importa produtos do Excel para Postgres")
    parser.add_argument(
        "arquivo",
        nargs="?",
        default="data/Produtos.xlsx",
        help="Caminho do arquivo .xlsx (default: data/Produtos.xlsx)",
    )
    args = parser.parse_args()

    caminho = Path(args.arquivo)
    if not caminho.exists():
        log.error(f"Arquivo não encontrado: {caminho}")
        log.info("Coloque a planilha em data/Produtos.xlsx ou passe o caminho como argumento")
        sys.exit(1)

    produtos = ler_planilha(caminho)
    if not produtos:
        log.error("Nenhum produto válido encontrado.")
        sys.exit(1)

    log.info("Inserindo no banco...")
    stats = inserir_produtos(produtos)

    log.info("=" * 50)
    log.info(f"Total processado:  {stats['total']}")
    log.info(f"Inseridos:         {stats['inseridos']}")
    log.info(f"Atualizados:       {stats['atualizados']}")
    log.info("=" * 50)

    # Resumo extra
    with get_connection(DATABASE_URL_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT marca), COUNT(DISTINCT grupo_produto) FROM produtos")
            marcas, grupos = cur.fetchone()
            log.info(f"Marcas distintas: {marcas}")
            log.info(f"Grupos distintos: {grupos}")


if __name__ == "__main__":
    main()
