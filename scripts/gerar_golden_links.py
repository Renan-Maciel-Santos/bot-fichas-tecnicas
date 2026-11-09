"""Gera planilha de rotulagem manual para calibrar a vinculacao documento->produto.

Por que existe:
    Trocar o algoritmo de similaridade ou mexer nos limiares da Camada 1 exige
    medir precisao e cobertura contra verdade conhecida. Sem baseline rotulado,
    qualquer ajuste e chute com numero. Este script produz o instrumento; depois
    de preenchido, o CSV vira golden set congelado em tests/golden/.

Amostra (seed fixa -> reproduzivel entre execucoes):
    sem_match_alto  score >= 0.25  onde devem estar os falsos negativos
    sem_match_baixo score <  0.25  confirma se o corte inferior esta certo
    ambiguo                        distingue familia 1:N de ambiguidade real
    vinculado_atual                mede a PRECISAO atual (nao pode regredir)

Como preencher a coluna codigo_correto:
    12345              um produto especifico
    12345;12346        familia: o documento cobre varios produtos
    NENHUM             nao ha produto correspondente no catalogo
    DUVIDA             nao da para decidir so pelo nome do arquivo

Uso:
    python -m scripts.gerar_golden_links
"""

import csv
import logging
import random
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

SAIDA = Path("data/analise/golden_links_para_rotular.csv")
SEED = 42
TOP_N = 5
CORTE_SCORE_ALTO = 0.25

# Tamanhos por estrato. Somam 50 -- volume rotulavel numa sessao, suficiente
# para distinguir efeitos grandes (que e o que estamos procurando).
TAMANHOS = {
    "sem_match_alto": 15,
    "sem_match_baixo": 8,
    "ambiguo": 15,
    "vinculado_atual": 12,
}

SQL_CANDIDATOS = """
    SELECT p.codigo,
           p.descricao,
           similarity(unaccent(lower(p.descricao)), unaccent(lower(%(miolo)s))) AS score
    FROM produtos p
    WHERE unaccent(lower(p.marca)) = unaccent(lower(%(marca)s))
    ORDER BY score DESC
    LIMIT %(limite)s
"""

SQL_VINCULOS_ATUAIS = """
    SELECT d.nome_arquivo,
           d.marca_pasta,
           pd.produto_codigo,
           p.descricao,
           pd.metodo_match,
           pd.confianca_match
    FROM produto_documento pd
    JOIN documentos d ON d.id = pd.documento_id
    JOIN produtos p ON p.codigo = pd.produto_codigo
    WHERE d.revogado_em IS NULL
      AND NOT pd.revisado_humano
    ORDER BY d.nome_arquivo
"""

COLUNAS = (
    ["estrato", "marca_pasta", "nome_arquivo", "miolo", "len_miolo",
     "vinculo_atual_codigo", "vinculo_atual_desc", "metodo_atual", "confianca_atual"]
    + [f"cand{i}_{campo}" for i in range(1, TOP_N + 1) for campo in ("codigo", "desc", "score")]
    + ["razao", "codigo_correto", "observacao"]
)


def _classificar(score_top1: float, score_top2: float) -> str:
    """Reproduz a decisao da Camada 1 para o par (top1, top2)."""
    if score_top1 < SCORE_MINIMO_TRIGRAM:
        return "sem_match"
    razao = (score_top1 / score_top2) if score_top2 > 0 else float("inf")
    return "ambiguo" if razao < FATOR_DESTAQUE_LINK else "vincularia"


def _linha_base(estrato: str) -> dict[str, object]:
    """Linha com todas as colunas vazias, para preenchimento parcial."""
    linha: dict[str, object] = {coluna: None for coluna in COLUNAS}
    linha["estrato"] = estrato
    return linha


def _coletar_residuo(cur) -> list[dict[str, object]]:
    """Percorre o residuo da Camada 1 anexando os TOP_N candidatos de cada doc."""
    cur.execute(SQL_DOCS_PARA_CAMADA1)
    documentos = cur.fetchall()

    linhas: list[dict[str, object]] = []
    for _documento_id, nome_arquivo, marca_pasta in documentos:
        if REGULATORIO_REGEX.search(nome_arquivo):
            continue

        miolo = _extrair_miolo(nome_arquivo)
        cur.execute(SQL_CANDIDATOS, {"miolo": miolo, "marca": marca_pasta, "limite": TOP_N})
        candidatos = cur.fetchall()

        score1 = float(candidatos[0][2]) if candidatos else 0.0
        score2 = float(candidatos[1][2]) if len(candidatos) > 1 else 0.0
        categoria = _classificar(score1, score2)

        if categoria == "ambiguo":
            estrato = "ambiguo"
        elif categoria == "sem_match":
            estrato = "sem_match_alto" if score1 >= CORTE_SCORE_ALTO else "sem_match_baixo"
        else:
            continue  # 'vincularia' nao e residuo

        linha = _linha_base(estrato)
        linha.update({
            "marca_pasta": marca_pasta,
            "nome_arquivo": nome_arquivo,
            "miolo": miolo,
            "len_miolo": len(miolo),
            "razao": round(score1 / score2, 3) if score2 > 0 else None,
        })
        for indice, (codigo, descricao, score) in enumerate(candidatos, start=1):
            linha[f"cand{indice}_codigo"] = codigo
            linha[f"cand{indice}_desc"] = descricao
            linha[f"cand{indice}_score"] = round(float(score), 3)
        linhas.append(linha)

    return linhas


def _coletar_vinculos_atuais(cur) -> list[dict[str, object]]:
    """Vinculos ja gravados (exceto os revisados) -- baseline de precisao."""
    cur.execute(SQL_VINCULOS_ATUAIS)

    linhas: list[dict[str, object]] = []
    for nome_arquivo, marca_pasta, codigo, descricao, metodo, confianca in cur.fetchall():
        linha = _linha_base("vinculado_atual")
        linha.update({
            "marca_pasta": marca_pasta,
            "nome_arquivo": nome_arquivo,
            "miolo": _extrair_miolo(nome_arquivo),
            "vinculo_atual_codigo": codigo,
            "vinculo_atual_desc": descricao,
            "metodo_atual": metodo,
            "confianca_atual": float(confianca),
        })
        linhas.append(linha)

    return linhas


def _amostrar(linhas: list[dict[str, object]]) -> list[dict[str, object]]:
    """Amostra estratificada com seed fixa; estrato menor que a cota entra inteiro."""
    aleatorio = random.Random(SEED)
    amostra: list[dict[str, object]] = []

    for estrato, cota in TAMANHOS.items():
        populacao = [linha for linha in linhas if linha["estrato"] == estrato]
        populacao.sort(key=lambda linha: str(linha["nome_arquivo"]))  # ordem estavel antes do sorteio
        selecionadas = (
            populacao if len(populacao) <= cota else aleatorio.sample(populacao, cota)
        )
        logger.info("  %-16s %3d de %3d", estrato, len(selecionadas), len(populacao))
        amostra.extend(selecionadas)

    return amostra


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    SAIDA.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        with conn.cursor() as cur:
            linhas = _coletar_residuo(cur) + _coletar_vinculos_atuais(cur)
        conn.rollback()  # script e read-only; explicito para deixar a intencao clara

    logger.info("Amostra estratificada (seed=%d):", SEED)
    amostra = _amostrar(linhas)
    amostra.sort(key=lambda linha: (str(linha["estrato"]), str(linha["marca_pasta"])))

    with SAIDA.open("w", newline="", encoding="utf-8-sig") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=COLUNAS)
        writer.writeheader()
        writer.writerows(amostra)

    logger.info("Planilha gerada: %s (%d linhas para rotular)", SAIDA, len(amostra))


if __name__ == "__main__":
    main()