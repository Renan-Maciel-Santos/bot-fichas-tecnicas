"""Conferencia, pelo cliente, das sugestoes da Camada 2 (LLM).

Por que existe:
    A Camada 2 acerta ~90% no golden, e o bot envia todo documento
    vinculado sem olhar confianca. Os vinculos do LLM so entram no banco
    depois que quem conhece o catalogo confirma, um a um.

Fluxo:
    gerar     roda a Camada 2 sobre o residuo (sem gravar no banco) e monta
              uma planilha com uma linha por sugestao documento -> produto.
              Documentos do golden ficam de fora: o cliente ja respondeu.
              As decisoes ficam em cache; rodar de novo so consulta o LLM
              para documentos novos.
    importar  le a planilha devolvida e grava so as linhas marcadas como
              "Correto", com revisado_humano = TRUE.

Uso:
    python -m scripts.conferencia_llm gerar
    python -m scripts.conferencia_llm importar CAMINHO_DA_PLANILHA [--aplicar]
"""

import argparse
import csv
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from src.config import DATABASE_URL_ADMIN
from src.db import get_connection
from src.link_documents import REGULATORIO_REGEX
from src.link_llm import (
    CONFIANCA_LLM,
    LLM_MODEL_LINK,
    METODO_LLM,
    SQL_DOCS_RESIDUO,
    DecisaoLLM,
    buscar_candidatos_llm,
    decidir_llm,
)

logger = logging.getLogger(__name__)

GOLDEN = Path("tests/golden/links_v1.csv")
CACHE = Path("data/analise/camada2_decisoes.json")
SAIDA = Path("data/analise/CONFERENCIA_SUGESTOES.xlsx")
CHAMADAS_PARALELAS = 8

RESPOSTAS = ["Correto", "Errado", "Nao sei"]
REVISOR = "cliente (conferencia Camada 2)"

COR_CABECALHO = "1F4E79"
COR_RESPOSTA = "FFF2CC"
COR_DOC_ALTERNADO = "F2F2F2"

INSTRUCOES = [
    ("Conferencia de sugestoes: documento x produto", True),
    ("", False),
    ("O que e isto:", True),
    ("O sistema leu cada ficha tecnica / FDS e sugeriu a quais produtos do catalogo", False),
    ("ela pertence. Antes de o bot passar a enviar esses documentos, precisamos que", False),
    ("quem conhece o catalogo confirme cada sugestao.", False),
    ("", False),
    ("Como preencher:", True),
    ("1. Va para a aba 'Sugestoes'. Cada linha e uma sugestao: um documento e um produto.", False),
    ("   Um documento pode ter varias linhas (ex.: a mesma FDS vale para 1 L e 5 L).", False),
    ("2. A coluna 'Trecho do documento' mostra o que o sistema leu para decidir.", False),
    ("   Para ver o documento inteiro, clique em 'Abrir'.", False),
    ("3. Na coluna RESPOSTA, escolha no menu:", False),
    ("   Correto  -> este documento vale para este produto", False),
    ("   Errado   -> nao vale (se souber o produto certo, escreva na observacao)", False),
    ("   Nao sei  -> nao da para ter certeza", False),
    ("", False),
    ("Importante:", True),
    ("So entra no bot o que estiver marcado como 'Correto'. Na duvida, 'Nao sei':", False),
    ("o documento fica fora do bot, e e melhor assim do que mandar a ficha errada.", False),
]


# --- gerar ---

def _arquivos_do_golden() -> set[str]:
    with GOLDEN.open(encoding="utf-8") as arquivo:
        return {linha["arquivo"] for linha in csv.DictReader(arquivo)}


def _carregar_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    return json.loads(CACHE.read_text(encoding="utf-8"))


def _salvar_cache(cache: dict[str, dict]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _coletar_pendentes(cur, cache: dict[str, dict]) -> list[dict]:
    """Documentos do residuo ainda sem decisao em cache, com seus candidatos."""
    golden = _arquivos_do_golden()
    cur.execute(SQL_DOCS_RESIDUO)
    pendentes = []
    for documento_id, nome_arquivo, marca_pasta, texto in cur.fetchall():
        if str(documento_id) in cache or nome_arquivo in golden:
            continue
        if REGULATORIO_REGEX.search(nome_arquivo):
            continue
        pendentes.append({
            "documento_id": str(documento_id),
            "nome_arquivo": nome_arquivo,
            "marca_pasta": marca_pasta,
            "texto": texto,
            "candidatos": buscar_candidatos_llm(cur, nome_arquivo, marca_pasta),
        })
    return pendentes


def _decidir(doc: dict) -> tuple[dict, DecisaoLLM]:
    try:
        return doc, decidir_llm(doc["nome_arquivo"], doc["texto"], doc["candidatos"])
    except Exception as exc:  # erro de API num documento nao derruba o lote
        logger.warning("Falha no LLM para %s: %s", doc["nome_arquivo"], exc)
        return doc, DecisaoLLM("erro", (), str(exc))


def gerar() -> None:
    cache = _carregar_cache()
    with get_connection() as conexao, conexao.cursor() as cur:
        pendentes = _coletar_pendentes(cur, cache)

    logger.info("Modelo %s; %d documentos em cache, %d a decidir",
                LLM_MODEL_LINK, len(cache), len(pendentes))

    with ThreadPoolExecutor(max_workers=CHAMADAS_PARALELAS) as executor:
        futuros = [executor.submit(_decidir, doc) for doc in pendentes]
        for feitos, futuro in enumerate(as_completed(futuros), start=1):
            doc, decisao = futuro.result()
            if decisao.decisao == "erro":
                continue  # fica fora do cache: tenta de novo na proxima execucao
            descricoes = dict(doc["candidatos"])
            cache[doc["documento_id"]] = {
                "nome_arquivo": doc["nome_arquivo"],
                "marca_pasta": doc["marca_pasta"],
                "modelo": LLM_MODEL_LINK,
                **asdict(decisao),
                "descricoes": [descricoes[c] for c in decisao.codigos],
            }
            if feitos % 20 == 0:
                _salvar_cache(cache)
                logger.info("  %d/%d", feitos, len(pendentes))
    _salvar_cache(cache)

    _escrever_planilha(cache)


def _escrever_planilha(cache: dict[str, dict]) -> None:
    with get_connection() as conexao, conexao.cursor() as cur:
        cur.execute(
            "SELECT id::text, url_drive FROM documentos WHERE id::text = ANY(%s)",
            (list(cache),),
        )
        urls = dict(cur.fetchall())
        # Documento pode ter sido vinculado por outra via depois do cache.
        cur.execute("SELECT DISTINCT documento_id::text FROM produto_documento")
        ja_vinculados = {linha[0] for linha in cur.fetchall()}

    sugestoes = []
    for documento_id, item in sorted(cache.items(), key=lambda kv: (kv[1]["marca_pasta"], kv[1]["nome_arquivo"])):
        if item["decisao"] != "vincular" or documento_id in ja_vinculados:
            continue
        for codigo, descricao, trecho in zip(item["codigos"], item["descricoes"], item["trechos"]):
            sugestoes.append((documento_id, item, codigo, descricao, trecho))

    planilha = Workbook()
    aba = planilha.active
    aba.title = "Instrucoes"
    aba.column_dimensions["A"].width = 95
    for indice, (texto, negrito) in enumerate(INSTRUCOES, start=1):
        aba.cell(row=indice, column=1, value=texto).font = Font(bold=negrito, size=13 if indice == 1 else 11)

    aba = planilha.create_sheet("Sugestoes")
    cabecalho = ["Doc", "Marca", "Arquivo", "PDF", "Produto sugerido", "Trecho do documento",
                 "RESPOSTA", "Observação"]
    aba.append(cabecalho)
    for indice in range(1, len(cabecalho) + 1):
        celula = aba.cell(row=1, column=indice)
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor=COR_CABECALHO)
        celula.alignment = Alignment(vertical="center", wrap_text=True)

    gabarito = planilha.create_sheet("_gabarito")
    gabarito.append(["linha", "documento_id", "produto_codigo"])

    numero_doc = 0
    anterior = None
    for linha, (documento_id, item, codigo, descricao, trecho) in enumerate(sugestoes, start=2):
        if documento_id != anterior:
            numero_doc += 1
            anterior = documento_id
        aba.append([numero_doc, item["marca_pasta"], item["nome_arquivo"], "", descricao, trecho, "", ""])
        url = urls.get(documento_id)
        if url:
            celula = aba.cell(row=linha, column=4, value="Abrir")
            celula.hyperlink = url
            celula.font = Font(color="0563C1", underline="single")
        if numero_doc % 2 == 0:
            for coluna in range(1, len(cabecalho) + 1):
                aba.cell(row=linha, column=coluna).fill = PatternFill("solid", fgColor=COR_DOC_ALTERNADO)
        aba.cell(row=linha, column=7).fill = PatternFill("solid", fgColor=COR_RESPOSTA)
        for coluna in (3, 5, 6):
            aba.cell(row=linha, column=coluna).alignment = Alignment(vertical="center", wrap_text=True)
        gabarito.append([linha, documento_id, codigo])

    ultima = len(sugestoes) + 1
    validacao = DataValidation(type="list", formula1='"{}"'.format(",".join(RESPOSTAS)), allow_blank=True)
    validacao.error = "Escolha uma das opcoes do menu."
    aba.add_data_validation(validacao)
    validacao.add(f"G2:G{ultima}")

    for indice, largura in enumerate([6, 14, 44, 8, 46, 46, 12, 34], start=1):
        aba.column_dimensions[get_column_letter(indice)].width = largura
    aba.freeze_panes = "E2"
    aba.auto_filter.ref = f"A1:H{ultima}"
    gabarito.sheet_state = "hidden"

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    planilha.save(SAIDA)

    contagem = {}
    for item in cache.values():
        contagem[item["decisao"]] = contagem.get(item["decisao"], 0) + 1
    logger.info("Decisoes em cache: %s", contagem)
    logger.info("Planilha: %s (%d sugestoes em %d documentos)", SAIDA, len(sugestoes), numero_doc)


# --- importar ---

SQL_INSERIR_REVISADO = """
    INSERT INTO produto_documento
        (produto_codigo, documento_id, confianca_match, metodo_match,
         revisado_humano, revisor, revisado_em)
    VALUES
        (%(produto_codigo)s, %(documento_id)s, %(confianca)s, %(metodo)s,
         TRUE, %(revisor)s, NOW())
    ON CONFLICT (produto_codigo, documento_id) DO UPDATE
        -- Vinculo que outra camada ja tinha feito: o cliente confirmou,
        -- passa a revisado sem perder metodo e confianca de origem.
        -- Ja revisado antes (ex.: pelo golden): mantem quem revisou primeiro.
        SET revisado_humano = TRUE,
            revisor = CASE WHEN produto_documento.revisado_humano
                           THEN produto_documento.revisor ELSE EXCLUDED.revisor END,
            revisado_em = CASE WHEN produto_documento.revisado_humano
                               THEN produto_documento.revisado_em ELSE EXCLUDED.revisado_em END
    RETURNING (xmax = 0) AS inserido
"""

# Documento revogado depois que a planilha foi gerada (ex.: duplicata
# revogada por scripts/revogar_duplicatas): a resposta vale para a copia
# que o substituiu, que tem o mesmo conteudo.
SQL_DOCUMENTO_ATIVO = """
    SELECT id::text, revogado_em IS NOT NULL, substituido_por_id::text
    FROM documentos WHERE id = %(id)s
"""


def documento_ativo(cur, documento_id: str) -> str | None:
    """Segue substituido_por_id ate um documento nao revogado.

    None quando a cadeia termina num revogado sem substituto: a resposta
    nao tem para onde ir.
    """
    for _ in range(10):
        cur.execute(SQL_DOCUMENTO_ATIVO, {"id": documento_id})
        linha = cur.fetchone()
        if linha is None:
            return None
        _, revogado, substituto = linha
        if not revogado:
            return documento_id
        if substituto is None:
            return None
        documento_id = substituto
    return None


def ler_aprovados(
    caminho: Path, pular: frozenset[int] = frozenset(),
) -> tuple[list[tuple[str, int]], dict[str, int]]:
    """Pares (documento_id, produto_codigo) marcados como Correto, e a
    contagem de respostas por tipo.

    `pular`: linhas da aba Sugestoes a ignorar mesmo marcadas -- para
    quando a resposta contradiz uma decisao anterior e a anterior vale.
    """
    planilha = load_workbook(caminho, read_only=True)
    coluna_resposta = 6  # G, base 0
    respostas = {
        linha: str(valores[coluna_resposta] or "").strip() if len(valores) > coluna_resposta else ""
        for linha, valores in enumerate(
            planilha["Sugestoes"].iter_rows(min_row=2, values_only=True), start=2
        )
    }
    aprovados = []
    contagem: dict[str, int] = {}
    for linha, documento_id, codigo in planilha["_gabarito"].iter_rows(min_row=2, values_only=True):
        resposta = "(pulada)" if linha in pular else respostas.get(linha, "")
        contagem[resposta or "(em branco)"] = contagem.get(resposta or "(em branco)", 0) + 1
        if resposta.lower() == "correto":
            aprovados.append((documento_id, int(codigo)))
    return aprovados, contagem


def importar(caminho: Path, aplicar: bool, pular: frozenset[int] = frozenset()) -> None:
    aprovados, contagem = ler_aprovados(caminho, pular)
    logger.info("Respostas: %s", contagem)

    # Grava em produto_documento: roda como botuser. A role da aplicacao
    # (bot_app) so le o acervo -- ver db/provisioning/001b_bot_app_grants.sql.
    # Sem --aplicar roda o mesmo caminho e desfaz no fim: as contagens sao
    # as reais, nao uma estimativa.
    novos = confirmados = redirecionados = 0
    sem_destino = []
    feitos: set[tuple[str, int]] = set()
    with get_connection(DATABASE_URL_ADMIN) as conexao, conexao.cursor() as cur:
        for documento_id, codigo in aprovados:
            ativo = documento_ativo(cur, documento_id)
            if ativo is None:
                sem_destino.append((documento_id, codigo))
                continue
            if ativo != documento_id:
                redirecionados += 1
            if (ativo, codigo) in feitos:
                continue
            feitos.add((ativo, codigo))
            cur.execute(SQL_INSERIR_REVISADO, {
                "produto_codigo": codigo,
                "documento_id": ativo,
                "confianca": CONFIANCA_LLM,
                "metodo": METODO_LLM,
                "revisor": REVISOR,
            })
            if cur.fetchone()[0]:
                novos += 1
            else:
                confirmados += 1
        if not aplicar:
            conexao.rollback()

    modo = "gravados" if aplicar else "a gravar (rode com --aplicar)"
    logger.info("%d vinculos novos e %d existentes marcados como revisados -- %s",
                novos, confirmados, modo)
    if redirecionados:
        logger.info("%d respostas eram de copias revogadas e foram para a copia que ficou.",
                    redirecionados)
    for documento_id, codigo in sem_destino:
        logger.warning("Sem destino (documento revogado sem substituto): %s -> %s", documento_id, codigo)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("gerar")
    p_importar = sub.add_parser("importar")
    p_importar.add_argument("planilha", type=Path)
    p_importar.add_argument("--aplicar", action="store_true")
    p_importar.add_argument(
        "--pular-linhas", default="",
        help="Linhas da aba Sugestoes a ignorar, separadas por virgula (ex.: 17,18,19).",
    )
    args = parser.parse_args()

    if args.comando == "gerar":
        gerar()
    else:
        pular = frozenset(int(n) for n in args.pular_linhas.split(",") if n.strip())
        importar(args.planilha, args.aplicar, pular)


if __name__ == "__main__":
    main()
