"""Analise read-only do residuo da Camada 1 de vinculacao.

Reproduz a logica de candidatos de link_documents.executar_camada1 sem
gravar nada, exportando um CSV com os 5 melhores candidatos de cada
documento nao vinculado. Serve para calibrar limiares com dados reais
em vez de intuicao.

Nao substitui o dry-run: o dry-run diz QUANTOS caem em cada categoria,
este script diz PORQUE cada um caiu.

Uso:
    python -m scripts.analise_residuo_camada1
"""

import csv
import logging
from pathlib import Path

from src.db import get_connection
from src.link_documents import (
    FATOR_DESTAQUE_LINK,
    REGULATORIO_REGEX,
    SCORE_MINIMO_TRIGRAM,
    SQL_DOCS_PARA_CAMADA1,
    _extrair_miolo,
)

logger = logging.getLogger(__name__)

SAIDA = Path("data/analise/residuo_camada1.csv")
TOP_N = 5

# Mesma consulta da Camada 1, com mais candidatos para dar contexto de
# vizinhanca (quantos produtos disputam o mesmo documento).
SQL_CANDIDATOS = """
    SELECT p.codigo,
           p.descricao,
           similarity(unaccent(lower(p.descricao)), unaccent(lower(%(miolo)s))) AS score
    FROM produtos p
    WHERE unaccent(lower(p.marca)) = unaccent(lower(%(marca)s))
    ORDER BY score DESC
    LIMIT %(limite)s
"""


def _classificar(score_top1: float, score_top2: float) -> str:
    """Reproduz a decisao da Camada 1 para o par (top1, top2)."""
    if score_top1 < SCORE_MINIMO_TRIGRAM:
        return "sem_match"
    razao = (score_top1 / score_top2) if score_top2 > 0 else float("inf")
    if razao < FATOR_DESTAQUE_LINK:
        return "ambiguo"
    return "vincularia"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    linhas: list[dict[str, object]] = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_DOCS_PARA_CAMADA1)
            documentos = cur.fetchall()
            logger.info("Documentos elegiveis para Camada 1: %d", len(documentos))

            for _documento_id, nome_arquivo, marca_pasta in documentos:
                if REGULATORIO_REGEX.search(nome_arquivo):
                    continue

                miolo = _extrair_miolo(nome_arquivo)
                cur.execute(
                    SQL_CANDIDATOS,
                    {"miolo": miolo, "marca": marca_pasta, "limite": TOP_N},
                )
                candidatos = cur.fetchall()

                score1 = float(candidatos[0][2]) if candidatos else 0.0
                score2 = float(candidatos[1][2]) if len(candidatos) > 1 else 0.0

                linha: dict[str, object] = {
                    "categoria": _classificar(score1, score2),
                    "marca_pasta": marca_pasta,
                    "nome_arquivo": nome_arquivo,
                    "miolo": miolo,
                    "len_miolo": len(miolo),
                    "razao": round(score1 / score2, 3) if score2 > 0 else None,
                }
                for i in range(TOP_N):
                    if i < len(candidatos):
                        linha[f"cand{i+1}_desc"] = candidatos[i][1]
                        linha[f"cand{i+1}_score"] = round(float(candidatos[i][2]), 3)
                    else:
                        linha[f"cand{i+1}_desc"] = None
                        linha[f"cand{i+1}_score"] = None
                linhas.append(linha)

        conn.rollback()  # nada a gravar; explicito para deixar a intencao clara

    linhas.sort(key=lambda linha: (str(linha["categoria"]), -float(linha["cand1_score"] or 0)))

    with SAIDA.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=list(linhas[0].keys()))
        writer.writeheader()
        writer.writerows(linhas)

    logger.info("CSV gerado: %s (%d linhas)", SAIDA, len(linhas))
    for categoria in ("sem_match", "ambiguo", "vincularia"):
        total = sum(1 for linha in linhas if linha["categoria"] == categoria)
        logger.info("  %-12s %d", categoria, total)


if __name__ == "__main__":
    main()