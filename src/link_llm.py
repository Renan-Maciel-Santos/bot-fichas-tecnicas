"""
Vinculacao documento <-> produto, Camada 2: LLM sobre o residuo.

O que sobra das Camadas 0 e 1 (ambiguos e sem match) e, em boa parte,
caso em que o NOME do arquivo nao basta: "FDS-CASACLEAN-DESINFETANTE.pdf"
vale para quatro fragrancias, "ALCOOL ALCOLAR 70 INPM" nao diz se e gel
ou liquido. O TEXTO do documento costuma dizer: a secao 1 de uma FDS
nomeia o produto e as variantes que ela cobre.

Decisoes de desenho:
- Lista FECHADA de candidatos: o LLM so escolhe entre codigos do
  catalogo que recebeu (a marca inteira quando ela cabe no prompt).
  Codigo fora da lista e descartado na validacao, como na extracao.
- Tres saidas: vincular (um ou mais codigos), nenhum (o produto do
  documento nao esta no catalogo) e duvida. Na duvida nao vincula.
- Confianca gravada fixa em CONFIANCA_LLM (< 0.80): todo vinculo desta
  camada cai na fila de revisao (v_pendentes_revisao).

Uso:
    python -m src.link_llm --dry-run    # decide e mostra, sem gravar
    python -m src.link_llm              # grava
"""

import argparse
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass

from src import config
from src.db import get_connection
from src.link_documents import (
    PASTAS_SEM_MARCA,
    REGULATORIO_REGEX,
    _extrair_miolo,
    _vincular,
)
from src.openai_client import get_client

logger = logging.getLogger(__name__)

METODO_LLM = "llm"
CONFIANCA_LLM = 0.70
# Medido com scripts/eval_camada2 (10/2026, 34 docs do golden que chegam
# aqui): gpt-4o-mini 74% de precisao, gpt-5.4-mini 63%, gpt-5.5 90-92%.
# Os erros do gpt-5.5 foram casos de fronteira, nao produto inventado.
LLM_MODEL_LINK = os.getenv("LLM_MODEL_LINK", "gpt-5.5")

# A marca vai inteira ao prompt ate este tamanho (~15 tokens por linha).
MAX_PRODUTOS_MARCA = 150
# Pastas sem marca ou marcas maiores: os N mais parecidos pelo nome.
LIMITE_CANDIDATOS_TRIGRAM = 40
# Inicio do texto: identificacao do produto (secao 1 da FDS, cabecalho
# da ficha tecnica). Documento inteiro custaria ~8k caracteres em media.
MAX_CHARS_TEXTO = 4000

SQL_DOCS_RESIDUO = """
    SELECT d.id, d.nome_arquivo, d.marca_pasta, coalesce(d.texto_extraido, '')
    FROM documentos d
    WHERE d.revogado_em IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM produto_documento pd WHERE pd.documento_id = d.id
      )
      AND EXISTS (
          SELECT 1 FROM produtos p
          WHERE unaccent(lower(p.marca)) = unaccent(lower(d.marca_pasta))
      )
    ORDER BY d.marca_pasta, d.nome_arquivo
"""

SQL_TAMANHO_MARCA = """
    SELECT count(*) FROM produtos
    WHERE unaccent(lower(marca)) = unaccent(lower(%(marca)s))
"""

SQL_PRODUTOS_DA_MARCA = """
    SELECT codigo, descricao FROM produtos
    WHERE unaccent(lower(marca)) = unaccent(lower(%(marca)s))
    ORDER BY descricao
"""

SQL_PRODUTOS_POR_TRIGRAM = """
    SELECT codigo, descricao FROM produtos p
    WHERE %(global)s OR unaccent(lower(p.marca)) = unaccent(lower(%(marca)s))
    ORDER BY similarity(unaccent(lower(p.descricao)), unaccent(lower(%(miolo)s))) DESC
    LIMIT %(limite)s
"""

SYSTEM_PROMPT = """\
Voce vincula documentos tecnicos (FDS/FISPQ, fichas tecnicas, laudos) aos
produtos de um catalogo de revenda de produtos de limpeza e higiene.

Recebe: o nome do arquivo, o inicio do texto do documento e a lista de
produtos candidatos (codigo | descricao). O padrao e NAO vincular.

Vincule um produto so quando o documento o identifica sem ambiguidade:
- O nome comercial, a linha e o tipo batem (gel x liquido, espuma x
  liquido, refil x dispenser x saboneteira, wipes x liquido).
- Se os candidatos diferem num atributo (espessura, versao, fragrancia,
  concentracao, linha "PREMIUM"/"EXTRA") e o documento nao diz qual e,
  responda "duvida" -- nao inclua todos para garantir.
- Inclua varios produtos so em dois casos:
  * descricoes iguais que diferem APENAS no volume/peso da embalagem
    (500 ML, 1 L, 5 L), e o documento nao restringe o tamanho;
  * variantes que o documento lista explicitamente (ex.: "FLORAL,
    LAVANDA, TALCO").
- Mesma marca e categoria parecida nao e o mesmo produto. Muitos
  documentos sao de produtos que a revenda nao vende: se o produto do
  documento nao esta na lista, responda "nenhum".
- Vincular errado e grave: a ficha de seguranca do produto errado vai
  para o cliente final.

Para cada produto vinculado, copie do documento (ou do nome do arquivo)
um trecho LITERAL, de 3 a 12 palavras, que o identifica. O trecho e
conferido automaticamente; trecho que nao existe no documento anula a
resposta inteira.

Responda so com JSON:
{"decisao": "vincular" | "nenhum" | "duvida",
 "produtos": [{"codigo": inteiro, "trecho": "copia literal"}],
 "justificativa": "uma frase"}
"""


@dataclass(frozen=True)
class DecisaoLLM:
    decisao: str  # 'vincular', 'nenhum' ou 'duvida'
    codigos: tuple[int, ...]
    justificativa: str
    trechos: tuple[str, ...] = ()  # um por codigo: o que o LLM citou do documento


def _normalizar(texto: str) -> str:
    """Minusculas, sem acento, so letras e digitos separados por espaco:
    o trecho citado tem de sobreviver a quebras de linha e pontuacao do PDF."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", sem_acento.lower()))


def interpretar_resposta(raw: str, codigos_validos: set[int], fonte: str) -> DecisaoLLM:
    """Valida a resposta do LLM.

    Qualquer desvio vira 'duvida': JSON invalido, decisao desconhecida,
    'vincular' sem produto, codigo fora da lista fechada, ou trecho que nao
    aparece em `fonte` (nome do arquivo + texto que o LLM recebeu). Esta
    camada nunca vincula algo que nao conseguiu conferir.
    """
    try:
        data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except json.JSONDecodeError:
        return DecisaoLLM("duvida", (), "resposta nao e JSON valido")

    decisao = data.get("decisao")
    justificativa = str(data.get("justificativa") or "")
    produtos = data.get("produtos") or []

    if decisao in ("nenhum", "duvida"):
        return DecisaoLLM(decisao, (), justificativa)
    if decisao != "vincular" or not isinstance(produtos, list) or not produtos:
        return DecisaoLLM("duvida", (), f"resposta fora do formato: {decisao!r}")

    fonte_normalizada = _normalizar(fonte)
    trechos: dict[int, str] = {}
    for produto in produtos:
        try:
            codigo = int(produto.get("codigo"))
        except (AttributeError, TypeError, ValueError):
            return DecisaoLLM("duvida", (), "codigo nao numerico")
        if codigo not in codigos_validos:
            logger.warning("LLM devolveu codigo fora da lista: %s", codigo)
            return DecisaoLLM("duvida", (), f"codigo fora da lista: {codigo}")
        trecho = _normalizar(str(produto.get("trecho") or ""))
        if len(trecho) < 4 or trecho not in fonte_normalizada:
            return DecisaoLLM("duvida", (), f"trecho nao encontrado no documento: {produto.get('trecho')!r}")
        trechos.setdefault(codigo, str(produto.get("trecho")))

    return DecisaoLLM("vincular", tuple(trechos), justificativa, tuple(trechos.values()))


def buscar_candidatos_llm(cur, nome_arquivo: str, marca_pasta: str) -> list[tuple[int, str]]:
    """Catalogo que o LLM pode escolher: a marca inteira quando cabe no
    prompt; senao, os mais parecidos com o nome do arquivo."""
    sem_marca = marca_pasta.strip().upper() in PASTAS_SEM_MARCA
    if not sem_marca:
        cur.execute(SQL_TAMANHO_MARCA, {"marca": marca_pasta})
        if cur.fetchone()[0] <= MAX_PRODUTOS_MARCA:
            cur.execute(SQL_PRODUTOS_DA_MARCA, {"marca": marca_pasta})
            return cur.fetchall()

    cur.execute(SQL_PRODUTOS_POR_TRIGRAM, {
        "global": sem_marca,
        "marca": marca_pasta,
        "miolo": _extrair_miolo(nome_arquivo),
        "limite": LIMITE_CANDIDATOS_TRIGRAM,
    })
    return cur.fetchall()


def _trecho_do_documento(texto: str) -> str:
    return " ".join(texto.split())[:MAX_CHARS_TEXTO]


def montar_mensagem(nome_arquivo: str, texto: str, candidatos: list[tuple[int, str]]) -> str:
    lista = "\n".join(f"{codigo} | {descricao}" for codigo, descricao in candidatos)
    trecho = _trecho_do_documento(texto) or "(documento sem texto extraido)"
    return (
        f"ARQUIVO: {nome_arquivo}\n\n"
        f"INICIO DO DOCUMENTO:\n{trecho}\n\n"
        f"PRODUTOS CANDIDATOS:\n{lista}"
    )


def decidir_llm(nome_arquivo: str, texto: str, candidatos: list[tuple[int, str]]) -> DecisaoLLM:
    if not candidatos:
        return DecisaoLLM("nenhum", (), "marca sem produtos no catalogo")

    parametros = {}
    # Modelos de raciocinio (gpt-5.x, o-series) so aceitam a temperatura padrao.
    if not LLM_MODEL_LINK.startswith(("gpt-5", "o1", "o3", "o4")):
        parametros["temperature"] = 0

    response = get_client().chat.completions.create(
        model=LLM_MODEL_LINK,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": montar_mensagem(nome_arquivo, texto, candidatos)},
        ],
        **parametros,
    )
    raw = response.choices[0].message.content or ""
    fonte = f"{nome_arquivo} {_trecho_do_documento(texto)}"
    return interpretar_resposta(raw, {codigo for codigo, _ in candidatos}, fonte)


@dataclass
class ResultadoCamada2:
    vinculados: int = 0
    vinculos: int = 0
    nenhum: int = 0
    duvida: int = 0
    regulatorios: int = 0


def executar_camada2(cur, dry_run: bool) -> ResultadoCamada2:
    resultado = ResultadoCamada2()
    cur.execute(SQL_DOCS_RESIDUO)
    documentos = cur.fetchall()

    for documento_id, nome_arquivo, marca_pasta, texto in documentos:
        if REGULATORIO_REGEX.search(nome_arquivo):
            resultado.regulatorios += 1
            continue

        candidatos = buscar_candidatos_llm(cur, nome_arquivo, marca_pasta)
        decisao = decidir_llm(nome_arquivo, texto, candidatos)

        if decisao.decisao != "vincular":
            if decisao.decisao == "nenhum":
                resultado.nenhum += 1
            else:
                resultado.duvida += 1
            logger.info("  [%s] %s -- %s", decisao.decisao, nome_arquivo, decisao.justificativa)
            continue

        descricoes = dict(candidatos)
        for codigo in decisao.codigos:
            _vincular(
                cur,
                produto_codigo=codigo,
                documento_id=documento_id,
                confianca=CONFIANCA_LLM,
                metodo=METODO_LLM,
                dry_run=dry_run,
            )
            logger.info("  [vinculado] %s -> %s", nome_arquivo, descricoes[codigo])
        logger.info("      porque: %s", decisao.justificativa)
        resultado.vinculados += 1
        resultado.vinculos += len(decisao.codigos)

    return resultado


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Vincula o residuo com LLM (Camada 2).")
    parser.add_argument("--dry-run", action="store_true", help="Decide e mostra, sem gravar.")
    args = parser.parse_args()

    # Grava em produto_documento: roda como botuser. A role da aplicacao
    # (bot_app) so le o acervo -- ver db/provisioning/001b_bot_app_grants.sql.
    with get_connection(config.DATABASE_URL_ADMIN) as conn:
        with conn.cursor() as cur:
            resultado = executar_camada2(cur, args.dry_run)
        if args.dry_run:
            conn.rollback()

    prefixo = "[DRY-RUN] " if args.dry_run else ""
    logger.info("%sCamada 2 (LLM): %d documentos vinculados (%d vinculos), "
                "%d nenhum, %d duvida, %d regulatorios pulados",
                prefixo, resultado.vinculados, resultado.vinculos,
                resultado.nenhum, resultado.duvida, resultado.regulatorios)


if __name__ == "__main__":
    main()
