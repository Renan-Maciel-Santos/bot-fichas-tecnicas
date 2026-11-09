"""Importa a planilha de conferencia preenchida pelo cliente para o golden set.

Por que existe:
    `gerar_planilha_cliente` entrega ao cliente letras embaralhadas (A-E) e
    guarda numa aba oculta, `_gabarito`, qual codigo de produto cada letra
    representa. Este script faz o caminho de volta: cruza as respostas com o
    gabarito e grava o resultado em tests/golden/, onde vira a verdade contra
    a qual a Camada 1 de vinculacao e medida.

Formatos de resposta aceitos (coluna RESPOSTA):
    A..E          um produto
    MAIS DE UM    letras na coluna seguinte ("D e E", "A, B, C e D")
    NENHUM        nenhuma opcao serve; se o cliente escreveu o nome do produto
                  certo (em qualquer das duas colunas livres), o nome e
                  procurado no catalogo pela descricao exata
    NAO SEI       vira DUVIDA

Linhas cujo nome escrito pelo cliente nao existe no catalogo saem com
pendente=sim: o golden so deve ser congelado depois de resolve-las a mao.

Pre-requisito:
    planilha devolvida pelo cliente, com a aba `_gabarito` intacta

Uso:
    python -m scripts.importar_conferencia CAMINHO_DA_PLANILHA
"""

import argparse
import csv
import logging
import re
import unicodedata
from pathlib import Path

from openpyxl import load_workbook

from src.db import get_connection

logger = logging.getLogger(__name__)

SAIDA = Path("tests/golden/links_v1.csv")
LETRAS_OPCAO = ["A", "B", "C", "D", "E"]

# Decisoes tomadas a mao sobre respostas do cliente, aplicadas por cima da
# planilha para que reimportar nao as desfaca. Criterio: na duvida, vale o
# nome literal do arquivo.
CORRECOES_MANUAIS = {
    # Cliente indicou as versoes PERFUMADO (uma delas, 1 L, nem existe no
    # catalogo); o arquivo diz Tradicional e o catalogo tem o Tradicional.
    18: ("9000005", "QUEROSENE TRADICIONAL 500 ML FORTEX, pelo nome do arquivo"),
    19: ("9000006", "REMOVEDOR TRADICIONAL 500 ML FORTEX, pelo nome do arquivo"),
}

COLUNAS = [
    "numero", "estrato", "arquivo", "codigo_vinculo_atual",
    "codigo_correto", "resposta_cliente", "observacao_cliente", "pendente",
]


def _normalizar(texto: str) -> str:
    """Maiusculas, sem acento e sem espacos: '1 L' e '1L' comparam igual."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", "", sem_acento.upper())


def _separar_nomes(texto: str) -> list[str]:
    """'PRODUTO X e PRODUTO Y' -> ['PRODUTO X', 'PRODUTO Y']."""
    return [parte.strip() for parte in re.split(r"\s+e\s+|;|\n", texto) if parte.strip()]


def interpretar_resposta(
    resposta: str | None,
    quais: str | None,
    observacao: str | None,
    opcoes: dict[str, int],
    catalogo: dict[str, int],
) -> tuple[str, bool]:
    """Devolve (codigo_correto, pendente).

    codigo_correto segue o formato do golden: codigos separados por ';',
    NENHUM ou DUVIDA.
    """
    resposta = _normalizar(resposta or "")

    if resposta in LETRAS_OPCAO:
        return str(opcoes[resposta]), False

    if resposta == "MAISDEUM":
        # Separar pelo conectivo antes de maiusculizar: em "D e E" o "e"
        # e conjuncao, nao a opcao E.
        partes = re.split(r",|;|/|\s+e\s+|\s+", (quais or "").strip())
        letras = [p.upper() for p in partes if p.upper() in LETRAS_OPCAO]
        if not letras:
            return "DUVIDA", True
        return ";".join(str(opcoes[letra]) for letra in dict.fromkeys(letras)), False

    if resposta == "NENHUM":
        # O cliente nem sempre usa a coluna certa para o nome do produto.
        texto_livre = " e ".join(t for t in (quais, observacao) if t)
        if not texto_livre:
            return "NENHUM", False
        nomes = _separar_nomes(texto_livre)
        codigos = [catalogo.get(_normalizar(nome)) for nome in nomes]
        encontrados = [str(c) for c in codigos if c is not None]
        pendente = len(encontrados) < len(nomes)
        return (";".join(encontrados) or "NENHUM"), pendente

    if resposta == "NAOSEI":
        return "DUVIDA", False

    return "DUVIDA", True


def _carregar_catalogo() -> dict[str, int]:
    with get_connection() as conexao, conexao.cursor() as cur:
        cur.execute("SELECT codigo, descricao FROM produtos")
        return {_normalizar(descricao): codigo for codigo, descricao in cur.fetchall()}


def importar(caminho: Path) -> list[dict[str, object]]:
    planilha = load_workbook(caminho, read_only=True)
    documentos = list(planilha["Documentos"].iter_rows(min_row=2, values_only=True))
    gabarito = list(planilha["_gabarito"].iter_rows(min_row=2, values_only=True))
    catalogo = _carregar_catalogo()

    linhas = []
    for doc, gab in zip(documentos, gabarito, strict=True):
        numero, estrato, arquivo, vinculo_atual, *codigos_opcao = gab
        if doc[0] != numero:
            raise ValueError(f"linha {numero}: aba Documentos fora de ordem com o gabarito")

        resposta, quais, observacao = doc[8], doc[9], doc[10]
        opcoes = {letra: codigo for letra, codigo in zip(LETRAS_OPCAO, codigos_opcao) if codigo}
        codigo_correto, pendente = interpretar_resposta(
            resposta, quais, observacao, opcoes, catalogo,
        )
        observacao_cliente = " | ".join(t for t in (quais, observacao) if t)
        if numero in CORRECOES_MANUAIS:
            codigo_correto, motivo = CORRECOES_MANUAIS[numero]
            pendente = False
            observacao_cliente = f"{observacao_cliente} | corrigido: {motivo}"
        linhas.append({
            "numero": numero,
            "estrato": estrato,
            "arquivo": arquivo,
            "codigo_vinculo_atual": vinculo_atual or "",
            "codigo_correto": codigo_correto,
            "resposta_cliente": resposta or "",
            "observacao_cliente": observacao_cliente,
            "pendente": "sim" if pendente else "",
        })
    return linhas


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("planilha", type=Path)
    args = parser.parse_args()

    linhas = importar(args.planilha)
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    with SAIDA.open("w", newline="", encoding="utf-8") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=COLUNAS)
        writer.writeheader()
        writer.writerows(linhas)

    pendentes = [linha["numero"] for linha in linhas if linha["pendente"]]
    logger.info("%d linhas gravadas em %s", len(linhas), SAIDA)
    if pendentes:
        logger.info("Pendentes de resolucao manual: %s", pendentes)


if __name__ == "__main__":
    main()
