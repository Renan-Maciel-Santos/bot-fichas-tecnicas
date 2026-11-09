"""Segunda rodada de conferencia com o cliente: uma planilha, tres perguntas.

Por que existe:
    1. Vinculos que o bot ja envia e ninguem conferiu (Camadas 0 e 1).
       Vinculo errado = ficha de seguranca do produto errado no WhatsApp.
    2. Documentos em que a Camada 2 (LLM) ficou em duvida: quase sempre
       "qual embalagem?" ou "qual linha?" -- pergunta de quem conhece o
       catalogo.
    3. Pastas do Drive cuja marca nao existe no catalogo: as fichas delas
       nunca vinculam. Ou a revenda nao vende a marca, ou ela esta escrita
       de outro jeito no catalogo (Start x QUIMICA ALFA).

Fluxo:
    gerar     monta data/analise/CONFERENCIA_RODADA2.xlsx (le o banco, nao grava)
    importar  le a planilha devolvida; sem --aplicar so mostra o que faria
              - vinculo "Correto": marcado como revisado
              - vinculo "Errado": removido (copia em data/analise/ para desfazer)
              - duvida respondida: vinculo novo, ja revisado
              - marcas: respostas em data/analise/marcas_respostas.csv

    A resposta vale para todas as copias do mesmo PDF (mesmo hash), como
    no gravar_golden.

Uso:
    python -m scripts.conferencia_rodada2 gerar
    python -m scripts.conferencia_rodada2 importar CAMINHO [--aplicar]
"""

import argparse
import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from scripts.importar_conferencia import LETRAS_OPCAO, _carregar_catalogo, interpretar_resposta
from src.config import DATABASE_URL_ADMIN
from src.db import get_connection
from src.link_documents import _extrair_miolo, buscar_candidatos

logger = logging.getLogger(__name__)

SAIDA = Path("data/analise/CONFERENCIA_RODADA2.xlsx")
CACHE_LLM = Path("data/analise/camada2_decisoes.json")
REMOVIDOS = Path("data/analise/vinculos_removidos_rodada2.json")
MARCAS_RESPOSTAS = Path("data/analise/marcas_respostas.csv")
REVISOR = "cliente (conferencia rodada 2)"

ABA_VINCULOS = "1 Vinculos"
ABA_DUVIDAS = "2 Duvidas"
ABA_MARCAS = "3 Marcas"

RESPOSTAS_VINCULO = ["Correto", "Errado", "Nao sei"]
RESPOSTAS_DUVIDA = LETRAS_OPCAO + ["Mais de um", "Nenhum", "Nao sei"]
RESPOSTAS_MARCA = ["E a sugerida", "E outra marca", "Nao revendemos", "Nao sei"]

COR_CABECALHO = "1F4E79"
COR_RESPOSTA = "FFF2CC"

COMO_FOI_LIGADO = {
    "codigo_prefixo": "codigo do produto no nome do arquivo",
    "marca_trigram": "nome do arquivo parecido com o do produto",
    "trigram_global": "nome parecido (pasta sem marca)",
}

INSTRUCOES = [
    ("Conferencia de documentos - rodada 2", True),
    ("", False),
    ("Sao tres abas, independentes. Pode fazer uma de cada vez.", False),
    ("Para ver qualquer documento, clique em 'Abrir'.", False),
    ("", False),
    ("Aba 1 - Vinculos", True),
    ("O bot ja envia estes documentos, mas ninguem conferiu. Em cada linha, um documento", False),
    ("e o produto a que ele foi ligado. Marque Correto, Errado ou Nao sei.", False),
    ("O que for marcado como Errado deixa de ser enviado.", False),
    ("", False),
    ("Aba 2 - Duvidas", True),
    ("O sistema leu estes documentos mas nao conseguiu decidir o produto (quase sempre:", False),
    ("qual tamanho ou qual linha). Escolha a opcao certa (A a E), 'Mais de um' (e diga", False),
    ("quais), 'Nenhum' (se souber o produto, escreva na observacao) ou 'Nao sei'.", False),
    ("", False),
    ("Aba 3 - Marcas", True),
    ("Estas pastas do Drive tem fichas de marcas que nao aparecem no catalogo, entao", False),
    ("nenhuma delas e enviada. Quando houver, mostramos uma marca parecida do catalogo.", False),
    ("Diga se e a mesma marca, se e outra (escreva qual) ou se voces nao revendem.", False),
    ("", False),
    ("Importante:", True),
    ("Na duvida, 'Nao sei'. Resposta errada atrapalha mais do que resposta em branco.", False),
]


# --- consultas ---

SQL_VINCULOS_SEM_REVISAO = """
    SELECT coalesce(d.hash_conteudo, d.id::text) AS chave,
           pd.produto_codigo,
           array_agg(pd.id::text ORDER BY d.marca_pasta = 'local_amostra') AS ids,
           -- a copia que nao esta em local_amostra representa o grupo
           (array_agg(d.marca_pasta ORDER BY d.marca_pasta = 'local_amostra'))[1],
           (array_agg(d.nome_arquivo ORDER BY d.marca_pasta = 'local_amostra'))[1],
           (array_agg(d.url_drive ORDER BY d.marca_pasta = 'local_amostra'))[1],
           p.descricao,
           min(pd.metodo_match),
           min(pd.confianca_match)
    FROM produto_documento pd
    JOIN documentos d ON d.id = pd.documento_id
    JOIN produtos p ON p.codigo = pd.produto_codigo
    WHERE NOT pd.revisado_humano AND d.revogado_em IS NULL
    GROUP BY 1, 2, p.descricao
    ORDER BY min(pd.confianca_match), 5
"""

SQL_DOC = """
    SELECT d.marca_pasta, d.nome_arquivo, d.url_drive,
           EXISTS (SELECT 1 FROM produto_documento pd WHERE pd.documento_id = d.id)
    FROM documentos d WHERE d.id = %(id)s
"""

SQL_MARCAS_ORFAS = """
    WITH orfas AS (
        SELECT d.marca_pasta, count(*) AS n,
               (array_agg(d.nome_arquivo ORDER BY d.nome_arquivo))[1:3] AS exemplos
        FROM documentos d
        WHERE d.revogado_em IS NULL AND d.marca_pasta <> 'local_amostra'
          AND NOT EXISTS (
              SELECT 1 FROM produtos p
              WHERE unaccent(lower(p.marca)) = unaccent(lower(d.marca_pasta)))
        GROUP BY 1
    ), marcas AS (SELECT DISTINCT marca FROM produtos WHERE marca IS NOT NULL)
    SELECT o.marca_pasta, o.n, o.exemplos,
           -- 1) marca com nome parecido (Start -> QUIMICA ALFA)
           (SELECT m.marca FROM marcas m
            WHERE similarity(unaccent(lower(m.marca)), unaccent(lower(o.marca_pasta))) >= 0.4
            ORDER BY similarity(unaccent(lower(m.marca)), unaccent(lower(o.marca_pasta))) DESC
            LIMIT 1),
           -- 2) marca cujos produtos citam a pasta como linha (Maciez -> SULCLEAN)
           (SELECT p.marca FROM produtos p
            WHERE p.marca <> 'GENERICO'
              AND unaccent(lower(p.descricao))
                  ~ ('\\m' || unaccent(lower(split_part(o.marca_pasta, ' ', 1))) || '\\M')
            GROUP BY p.marca ORDER BY count(*) DESC LIMIT 1)
    FROM orfas o
    ORDER BY o.n DESC, o.marca_pasta
"""

SQL_COPIAS = """
    SELECT d2.id::text FROM documentos d1
    JOIN documentos d2 ON coalesce(d2.hash_conteudo, d2.id::text) = coalesce(d1.hash_conteudo, d1.id::text)
    WHERE d1.id = %(id)s AND d2.revogado_em IS NULL
"""

SQL_INSERIR_REVISADO = """
    INSERT INTO produto_documento
        (produto_codigo, documento_id, confianca_match, metodo_match,
         revisado_humano, revisor, revisado_em)
    VALUES (%(codigo)s, %(documento_id)s, 1.00, 'manual', TRUE, %(revisor)s, NOW())
    ON CONFLICT (produto_codigo, documento_id) DO UPDATE
        SET revisado_humano = TRUE, revisor = EXCLUDED.revisor, revisado_em = EXCLUDED.revisado_em
"""


# --- planilha ---

def _cabecalho(aba, titulos: list[str], larguras: list[int]) -> None:
    aba.append(titulos)
    for indice, largura in enumerate(larguras, start=1):
        celula = aba.cell(row=1, column=indice)
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor=COR_CABECALHO)
        celula.alignment = Alignment(vertical="center", wrap_text=True)
        aba.column_dimensions[get_column_letter(indice)].width = largura
    aba.freeze_panes = "D2"


def _link(aba, linha: int, coluna: int, url: str | None) -> None:
    if url:
        celula = aba.cell(row=linha, column=coluna, value="Abrir")
        celula.hyperlink = url
        celula.font = Font(color="0563C1", underline="single")


def _menu(aba, coluna: int, opcoes: list[str], ultima: int) -> None:
    letra = get_column_letter(coluna)
    validacao = DataValidation(type="list", formula1='"{}"'.format(",".join(opcoes)), allow_blank=True)
    validacao.error = "Escolha uma das opcoes do menu."
    aba.add_data_validation(validacao)
    validacao.add(f"{letra}2:{letra}{max(ultima, 2)}")
    for linha in range(2, ultima + 1):
        aba.cell(row=linha, column=coluna).fill = PatternFill("solid", fgColor=COR_RESPOSTA)


def _quebrar(aba, colunas: tuple[int, ...]) -> None:
    for linha in range(2, aba.max_row + 1):
        for coluna in colunas:
            aba.cell(row=linha, column=coluna).alignment = Alignment(vertical="center", wrap_text=True)


def _aba_vinculos(planilha, gabarito, cur) -> int:
    aba = planilha.create_sheet(ABA_VINCULOS)
    _cabecalho(aba, ["Nº", "Pasta", "Arquivo", "PDF", "Produto vinculado", "Como foi ligado",
                     "RESPOSTA", "Observação"], [5, 14, 46, 8, 46, 30, 12, 30])
    cur.execute(SQL_VINCULOS_SEM_REVISAO)
    for numero, (_chave, _codigo, ids, pasta, arquivo, url, descricao, metodo, _conf) in enumerate(cur.fetchall(), start=1):
        aba.append([numero, pasta, arquivo, "", descricao, COMO_FOI_LIGADO.get(metodo, metodo), "", ""])
        _link(aba, numero + 1, 4, url)
        gabarito.append([ABA_VINCULOS, numero + 1, ",".join(ids), ""])
    _menu(aba, 7, RESPOSTAS_VINCULO, aba.max_row)
    _quebrar(aba, (3, 5, 6))
    return aba.max_row - 1


def _aba_duvidas(planilha, gabarito, cur) -> int:
    aba = planilha.create_sheet(ABA_DUVIDAS)
    titulos = (["Nº", "Pasta", "Arquivo", "PDF", "Por que o sistema ficou em dúvida"]
               + [f"Opção {letra}" for letra in LETRAS_OPCAO]
               + ["RESPOSTA", "Se 'Mais de um', quais?", "Observação"])
    _cabecalho(aba, titulos, [5, 14, 40, 8, 44] + [32] * len(LETRAS_OPCAO) + [14, 22, 30])

    decisoes = json.loads(CACHE_LLM.read_text(encoding="utf-8")) if CACHE_LLM.exists() else {}
    numero = 0
    for documento_id, item in sorted(decisoes.items(), key=lambda kv: kv[1]["nome_arquivo"]):
        if item["decisao"] != "duvida":
            continue
        cur.execute(SQL_DOC, {"id": documento_id})
        linha_doc = cur.fetchone()
        if linha_doc is None or linha_doc[3]:
            continue  # sumiu do banco ou ja foi vinculado por outra via
        pasta, arquivo, url, _ = linha_doc
        miolo = _extrair_miolo(arquivo)
        opcoes = buscar_candidatos(cur, miolo, pasta)[: len(LETRAS_OPCAO)]
        numero += 1
        aba.append([numero, pasta, arquivo, "", item["justificativa"]]
                   + [c.descricao for c in opcoes] + [""] * (len(LETRAS_OPCAO) - len(opcoes))
                   + ["", "", ""])
        _link(aba, numero + 1, 4, url)
        gabarito.append([ABA_DUVIDAS, numero + 1, documento_id, ",".join(str(c.codigo) for c in opcoes)])
    _menu(aba, 6 + len(LETRAS_OPCAO), RESPOSTAS_DUVIDA, aba.max_row)
    _quebrar(aba, tuple(range(3, 6 + len(LETRAS_OPCAO))))
    return numero


def _aba_marcas(planilha, cur) -> int:
    aba = planilha.create_sheet(ABA_MARCAS)
    _cabecalho(aba, ["Pasta no Drive", "Nº de fichas", "Exemplos de arquivos", "Marca parecida no catálogo",
                     "RESPOSTA", "Se outra marca, qual?"], [18, 10, 60, 30, 18, 30])
    aba.freeze_panes = "B2"
    cur.execute(SQL_MARCAS_ORFAS)
    for pasta, n, exemplos, por_nome, por_linha in cur.fetchall():
        if por_nome:
            sugestao = por_nome
        elif por_linha:
            sugestao = f"{por_linha} (tem produtos da linha {pasta})"
        else:
            sugestao = ""
        aba.append([pasta, n, "\n".join(exemplos), sugestao, "", ""])
    _menu(aba, 5, RESPOSTAS_MARCA, aba.max_row)
    _quebrar(aba, (3, 4))
    return aba.max_row - 1


def gerar() -> None:
    planilha = Workbook()
    aba = planilha.active
    aba.title = "Instrucoes"
    aba.column_dimensions["A"].width = 95
    for indice, (texto, negrito) in enumerate(INSTRUCOES, start=1):
        aba.cell(row=indice, column=1, value=texto).font = Font(bold=negrito, size=13 if indice == 1 else 11)

    gabarito = planilha.create_sheet("_gabarito")
    gabarito.append(["aba", "linha", "ids", "opcoes"])
    with get_connection() as conexao, conexao.cursor() as cur:
        n_vinculos = _aba_vinculos(planilha, gabarito, cur)
        n_duvidas = _aba_duvidas(planilha, gabarito, cur)
        n_marcas = _aba_marcas(planilha, cur)
    planilha.move_sheet("_gabarito", offset=len(planilha.sheetnames))
    gabarito.sheet_state = "hidden"

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    planilha.save(SAIDA)
    logger.info("Planilha: %s", SAIDA)
    logger.info("  %d vinculos, %d duvidas, %d marcas", n_vinculos, n_duvidas, n_marcas)


# --- importar ---

def ler_respostas(caminho: Path) -> dict[str, list[dict]]:
    """Respostas por aba, ja casadas com o gabarito oculto pela linha."""
    planilha = load_workbook(caminho, read_only=True)
    abas = {nome: list(planilha[nome].iter_rows(min_row=1, values_only=True))
            for nome in (ABA_VINCULOS, ABA_DUVIDAS, ABA_MARCAS)}

    def celula(nome: str, linha: int, coluna: int):
        valores = abas[nome][linha - 1] if linha - 1 < len(abas[nome]) else ()
        return valores[coluna - 1] if coluna - 1 < len(valores) else None

    resultado: dict[str, list[dict]] = {"vinculos": [], "duvidas": [], "marcas": []}
    for aba, linha, ids, opcoes in planilha["_gabarito"].iter_rows(min_row=2, values_only=True):
        if aba == ABA_VINCULOS:
            resultado["vinculos"].append({
                "linha": linha,
                "ids": ids.split(","),
                "resposta": str(celula(aba, linha, 7) or "").strip(),
            })
        elif aba == ABA_DUVIDAS:
            col = 6 + len(LETRAS_OPCAO)
            codigos = [int(c) for c in (opcoes or "").split(",") if c]
            resultado["duvidas"].append({
                "linha": linha,
                "documento_id": ids,
                "opcoes": dict(zip(LETRAS_OPCAO, codigos)),
                "resposta": celula(aba, linha, col),
                "quais": celula(aba, linha, col + 1),
                "observacao": celula(aba, linha, col + 2),
            })
    for valores in abas[ABA_MARCAS][1:]:
        if valores and valores[0]:
            resultado["marcas"].append({
                "pasta": valores[0],
                "sugestao": valores[3] or "",
                "resposta": str(valores[4] or "").strip(),
                "outra": str(valores[5] or "").strip() if len(valores) > 5 else "",
            })
    return resultado


def importar(caminho: Path, aplicar: bool) -> None:
    respostas = ler_respostas(caminho)
    catalogo = _carregar_catalogo()

    confirmar = [r for r in respostas["vinculos"] if r["resposta"].lower() == "correto"]
    remover = [r for r in respostas["vinculos"] if r["resposta"].lower() == "errado"]
    logger.info("Vinculos: %d corretos, %d errados, %d sem resposta ou 'Nao sei'",
                len(confirmar), len(remover), len(respostas["vinculos"]) - len(confirmar) - len(remover))

    novos, pendentes = [], []
    for r in respostas["duvidas"]:
        if not r["resposta"]:
            continue
        codigo_correto, pendente = interpretar_resposta(
            r["resposta"], r["quais"], r["observacao"], r["opcoes"], catalogo)
        if pendente:
            pendentes.append((r["linha"], r["resposta"], r["quais"], r["observacao"]))
        if codigo_correto not in ("NENHUM", "DUVIDA"):
            novos.append((r["documento_id"], [int(c) for c in codigo_correto.split(";")]))
    logger.info("Duvidas: %d documentos com produto escolhido, %d pendentes de resolucao manual",
                len(novos), len(pendentes))
    for linha, *resto in pendentes:
        logger.info("  pendente: aba 2, linha %s: %s", linha, resto)

    marcas_respondidas = [m for m in respostas["marcas"] if m["resposta"]]
    logger.info("Marcas: %d respondidas", len(marcas_respondidas))

    if not aplicar:
        logger.info("Nada gravado. Rode com --aplicar para gravar.")
        return

    # Grava em produto_documento: roda como botuser. A role da aplicacao
    # (bot_app) so le o acervo -- ver db/provisioning/001b_bot_app_grants.sql.
    with get_connection(DATABASE_URL_ADMIN) as conexao, conexao.cursor() as cur:
        for r in confirmar:
            cur.execute(
                "UPDATE produto_documento SET revisado_humano = TRUE, revisor = %(revisor)s, "
                "revisado_em = NOW() WHERE id::text = ANY(%(ids)s)",
                {"revisor": REVISOR, "ids": r["ids"]},
            )

        ids_remover = [i for r in remover for i in r["ids"]]
        if ids_remover:
            cur.execute(
                "SELECT row_to_json(pd) FROM produto_documento pd WHERE id::text = ANY(%s)", (ids_remover,))
            copia = [linha[0] for linha in cur.fetchall()]
            REMOVIDOS.write_text(json.dumps(
                {"removido_em": datetime.now().isoformat(), "vinculos": copia},
                ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            cur.execute("DELETE FROM produto_documento WHERE id::text = ANY(%s)", (ids_remover,))

        for documento_id, codigos in novos:
            cur.execute(SQL_COPIAS, {"id": documento_id})
            for (copia_id,) in cur.fetchall():
                for codigo in codigos:
                    cur.execute(SQL_INSERIR_REVISADO,
                                {"codigo": codigo, "documento_id": copia_id, "revisor": REVISOR})

    with MARCAS_RESPOSTAS.open("w", newline="", encoding="utf-8") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=["pasta", "sugestao", "resposta", "outra"])
        writer.writeheader()
        writer.writerows(marcas_respondidas)

    logger.info("Gravado: %d confirmados, %d removidos (copia em %s), %d documentos vinculados.",
                len(confirmar), len(remover), REMOVIDOS, len(novos))
    logger.info("Marcas em %s -- o mapeamento pasta -> marca ainda nao e aplicado na vinculacao.",
                MARCAS_RESPOSTAS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("gerar")
    p_importar = sub.add_parser("importar")
    p_importar.add_argument("planilha", type=Path)
    p_importar.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    if args.comando == "gerar":
        gerar()
    else:
        importar(args.planilha, args.aplicar)


if __name__ == "__main__":
    main()
