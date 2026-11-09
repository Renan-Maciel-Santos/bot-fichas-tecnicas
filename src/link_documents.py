"""
Vinculacao documento <-> produto (popula produto_documento).

Camada 0 — codigo no prefixo do nome do arquivo:
    Arquivos como "9000004 - P. HIG ROLAO ... .pdf" comecam com o
    proprio codigo do produto no catalogo. Extraimos o numero e
    casamos direto com produtos.codigo (match deterministico,
    confianca 1.00).

Camada 1 — marca + trigram no nome do arquivo:
    Para documentos cuja marca_pasta existe no catalogo, extrai o
    "miolo" do nome (sem prefixo de tipo nem extensao) e casa por
    similaridade trigram contra produtos.descricao DENTRO da marca.
    Limiar duplo (score minimo + fator de destaque) prioriza PRECISAO:
    na duvida, o documento vai para revisao em vez de vincular errado.
    Documentos regulatorios (alvara, ANVISA, CRQ...) sao pulados.

Camadas futuras (nao implementadas aqui):
    - llm: desambiguar residuo (ambiguos, marcas orfas) com gpt-4o-mini.

Uso:
    python -m src.link_documents            # executa a vinculacao
    python -m src.link_documents --dry-run  # mostra o que faria, sem gravar
"""

import argparse
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field

from src import config
from src.db import get_connection

logger = logging.getLogger(__name__)

# --- Camada 0: codigo no prefixo ---

METODO_CODIGO_PREFIXO = "codigo_prefixo"
CONFIANCA_DETERMINISTICA = 1.00

# Numero de 5+ digitos no inicio do nome do arquivo.
# 5 digitos evita capturar medidas/quantidades curtas (ex.: "4401 Interfolha").
CODIGO_PREFIXO_REGEX = re.compile(r"^\s*(\d{5,})")

# --- Camada 1: marca + trigram no nome do arquivo ---

METODO_MARCA_TRIGRAM = "marca_trigram"

# Limiares calibrados contra tests/golden/links_v1.csv (50 documentos
# rotulados pelo cliente; medir com `python -m scripts.eval_camada1`).
# Escolha por PRECISAO sobre cobertura: vinculo errado = FDS do produto
# errado = risco de seguranca quimica. Na duvida, revisao humana.
#
# Em 10/2026 o golden aceitava ate 0.25/1.25 sem erro, mas o dry-run no
# banco inteiro mostrou, fora da amostra, vinculos errados abaixo de 0.35
# ("LIMPADOR PERFUMADO" -> ALCOOL PERFUMADO, "ALCOOL LIQUIDO 70" -> ALCOOL
# GEL, "Pano Seco" -> PQM). O trigram nao distingue gel de liquido; abaixo
# de 0.35 a razao top1/top2 nao separa certo de errado. O score minimo
# fica; o fator cai de 1.5 para 1.3 (os dois erros restantes, ALCOLAR
# 70/80 INPM -> ALCOOL GEL 5 L, tinham razao 1.26).
SCORE_MINIMO_TRIGRAM = 0.35
FATOR_DESTAQUE_LINK = 1.3

# Documentos regulatorios/institucionais nao vinculam a produto por
# natureza (alvara, ANVISA, CRQ, certificados). Filtrados antes do
# trigram para nao poluir o match.
REGULATORIO_REGEX = re.compile(
    r"(alvara|anvisa|certificad|crq|notifica|registro|licenc|"
    r"\bafe\b|comprovante|atestado|laudo\s+de\s+analise)",
    re.IGNORECASE,
)

# Pastas do Drive que nao correspondem a uma marca: a ficha pode ser de
# qualquer fabricante, entao a busca vai ao catalogo inteiro. Os limiares
# sao os mesmos -- e o destaque que protege contra o produto errado.
PASTAS_SEM_MARCA = {"GENERICO"}
METODO_TRIGRAM_GLOBAL = "trigram_global"

# Volume ou peso de embalagem: "500 ML", "1L", "10 KG", "6 X 1L",
# "4X3.2L". Separa variantes de tamanho do mesmo produto. Nao pega
# medidas que definem o produto (70X50CM, 0,025MM) nem contagem de
# unidades: em guardanapo, "500 UND" e "1.000 UND" sao produtos distintos.
EMBALAGEM_REGEX = re.compile(
    r"\b(\d+\s*X\s*)?\d+([.,]\d+)?\s*(ML|L|LT|LTS|KG|G|GR|GRS)\b"
)

# Prefixos de tipo de documento a remover do nome antes do trigram.
# Ex.: "FISPQ - FORNO EXPRESS.pdf" -> "FORNO EXPRESS".
PREFIXO_TIPO_REGEX = re.compile(
    r"^\s*(fispq|fds|ft|bt|laudo|ficha\s+t[eé]cnica|boletim\s+t[eé]cnico)"
    r"\s*[-–:]?\s*",
    re.IGNORECASE,
)

# Extensao .pdf ao final.
EXTENSAO_REGEX = re.compile(r"\.pdf\s*$", re.IGNORECASE)

# --- SQL ---

SQL_DOCUMENTOS_SEM_VINCULO = """
    SELECT d.id, d.nome_arquivo
    FROM documentos d
    WHERE d.revogado_em IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM produto_documento pd WHERE pd.documento_id = d.id
    )
    ORDER BY d.nome_arquivo
"""

SQL_PRODUTO_EXISTE = "SELECT 1 FROM produtos WHERE codigo = %(codigo)s"

SQL_INSERT_VINCULO = """
    INSERT INTO produto_documento
        (produto_codigo, documento_id, confianca_match, metodo_match)
    VALUES
        (%(produto_codigo)s, %(documento_id)s, %(confianca)s, %(metodo)s)
    ON CONFLICT (produto_codigo, documento_id) DO NOTHING
"""

# Documentos sem vinculo, restritos as marcas que existem no catalogo.
# O EXISTS filtra marcas orfas (documentacao de marca que nao esta no
# catalogo de produtos) — elas ficam para a Camada 2 / revisao manual.
SQL_DOCS_PARA_CAMADA1 = """
    SELECT d.id, d.nome_arquivo, d.marca_pasta
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

# Trigram do miolo do nome contra produtos.descricao, dentro da marca.
# Traz uma folga de candidatos alem do top2: variantes de embalagem do
# mesmo produto (500 ML / 1 L) sao agrupadas antes da checagem de destaque.
SQL_TRIGRAM_NA_MARCA = """
    SELECT p.codigo,
           p.descricao,
           similarity(unaccent(lower(p.descricao)), unaccent(lower(%(miolo)s))) AS score
    FROM produtos p
    WHERE unaccent(lower(p.marca)) = unaccent(lower(%(marca)s))
    ORDER BY score DESC
    LIMIT %(limite)s
"""

# Mesma busca no catalogo inteiro, para pastas que nao sao marca.
SQL_TRIGRAM_GLOBAL = """
    SELECT p.codigo,
           p.descricao,
           similarity(unaccent(lower(p.descricao)), unaccent(lower(%(miolo)s))) AS score
    FROM produtos p
    ORDER BY score DESC
    LIMIT %(limite)s
"""

LIMITE_CANDIDATOS = 10


@dataclass
class Contadores:
    """Resumo da execucao da Camada 0."""

    vinculados: int = 0
    sem_codigo: int = 0
    codigo_inexistente: int = 0


@dataclass
class ResultadoCamada1:
    """Resumo da execucao da Camada 1, com residuo categorizado."""

    vinculados: int = 0
    regulatorios: int = 0
    ambiguos: list[str] = field(default_factory=list)
    sem_match: list[str] = field(default_factory=list)

    @property
    def total_residuo(self) -> int:
        return self.regulatorios + len(self.ambiguos) + len(self.sem_match)


def _vincular(
    cur,
    produto_codigo: int,
    documento_id: str,
    confianca: float,
    metodo: str,
    dry_run: bool,
) -> None:
    """Insere o vinculo em produto_documento (idempotente via ON CONFLICT)."""
    if dry_run:
        return
    cur.execute(
        SQL_INSERT_VINCULO,
        {
            "produto_codigo": produto_codigo,
            "documento_id": documento_id,
            "confianca": confianca,
            "metodo": metodo,
        },
    )

def extract_codigo_prefixo(nome_arquivo: str) -> int | None:
    """Extrai o codigo numerico do prefixo do nome, se houver 5+ digitos."""
    match = CODIGO_PREFIXO_REGEX.match(nome_arquivo or "")
    if not match:
        return None
    return int(match.group(1))


def executar_camada0(cur, dry_run: bool) -> Contadores:
    """Vincula documentos cujo nome comeca com o codigo do produto."""
    contadores = Contadores()

    cur.execute(SQL_DOCUMENTOS_SEM_VINCULO)
    documentos = cur.fetchall()

    for documento_id, nome_arquivo in documentos:
        codigo = extract_codigo_prefixo(nome_arquivo)
        if codigo is None:
            contadores.sem_codigo += 1
            continue

        cur.execute(SQL_PRODUTO_EXISTE, {"codigo": codigo})
        if cur.fetchone() is None:
            contadores.codigo_inexistente += 1
            logger.debug(
                "  [codigo_inexistente] %s -> codigo %d nao existe",
                nome_arquivo, codigo,
            )
            continue

        _vincular(
            cur,
            produto_codigo=codigo,
            documento_id=documento_id,
            confianca=CONFIANCA_DETERMINISTICA,
            metodo=METODO_CODIGO_PREFIXO,
            dry_run=dry_run,
        )
        contadores.vinculados += 1
        logger.info("  [vinculado] %s -> codigo %d", nome_arquivo, codigo)

    return contadores


def _extrair_miolo(nome_arquivo: str) -> str:
    """Reduz o nome do arquivo ao 'miolo' comparavel com a descricao.

    Remove extensao, prefixo de tipo (FISPQ/FDS/FT/BT/LAUDO/FICHA
    TECNICA) e colapsa espacos. NAO remove sufixo de marca — a marca ja
    e usada como filtro no SQL, e remover pode comer parte do nome do
    produto.

    Ex.:
        "FISPQ - FORNO EXPRESS.pdf"        -> "FORNO EXPRESS"
        "FICHA TÉCNICA - BACTERIX A.pdf"     -> "BACTERIX A"
        "BT Ceramax RC Acrilica.pdf"       -> "Ceramax RC Acrilica"
    """
    sem_ext = EXTENSAO_REGEX.sub("", nome_arquivo or "")
    sem_prefixo = PREFIXO_TIPO_REGEX.sub("", sem_ext)
    return " ".join(sem_prefixo.split()).strip()


@dataclass(frozen=True)
class Candidato:
    codigo: int
    descricao: str
    score: float


@dataclass(frozen=True)
class DecisaoCamada1:
    """Resultado da Camada 1 para um documento.

    categoria: 'vinculado', 'ambiguo' ou 'sem_match'. Em 'vinculado',
    `vinculos` traz um ou mais candidatos (variantes de embalagem do mesmo
    produto saem juntas). Em 'ambiguo', `top1`/`top2` dizem quem empatou.
    """

    categoria: str
    vinculos: tuple[Candidato, ...] = ()
    top1: Candidato | None = None
    top2: Candidato | None = None


def _sem_embalagem(texto: str) -> str:
    """Descricao sem volume/peso/quantidade, para agrupar variantes.

    "ALCOOL GEL ANTISSEPTICO 1 L HIGIMAX" e "... 500 ML HIGIMAX" viram
    a mesma chave. Medidas que definem o produto (70X50CM, 0,025MM, 70º)
    nao sao embalagem e ficam.
    """
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    sem_volume = EMBALAGEM_REGEX.sub(" ", sem_acento.upper())
    return " ".join(sem_volume.split())


def decidir_camada1(
    miolo: str,
    candidatos: list[Candidato],
    score_minimo: float = SCORE_MINIMO_TRIGRAM,
    fator_destaque: float = FATOR_DESTAQUE_LINK,
    agrupar_embalagem: bool = True,
) -> DecisaoCamada1:
    """Aplica o limiar duplo (score minimo + destaque) aos candidatos.

    Quando o nome do arquivo nao cita embalagem, a ficha vale para o
    produto em qualquer tamanho: candidatos que so diferem no volume
    formam um grupo, o destaque e medido grupo contra grupo e o grupo
    vencedor e vinculado inteiro. Quando o nome cita embalagem ("200L",
    "1000 ML"), o tamanho faz parte do que foi pedido e nao ha agrupamento.

    Args:
        miolo: nome do arquivo ja reduzido por _extrair_miolo.
        candidatos: em ordem decrescente de score.
        score_minimo, fator_destaque, agrupar_embalagem: parametrizados
            para o eval comparar alternativas; a execucao real usa os padroes.
    """
    if not candidatos:
        return DecisaoCamada1("sem_match")

    agrupar = agrupar_embalagem and EMBALAGEM_REGEX.search(miolo.upper()) is None
    grupos: dict[str, list[Candidato]] = {}
    for candidato in candidatos:
        chave = _sem_embalagem(candidato.descricao) if agrupar else str(candidato.codigo)
        grupos.setdefault(chave, []).append(candidato)
    ordenados = list(grupos.values())  # dict preserva a ordem de score

    top1 = ordenados[0][0]
    top2 = ordenados[1][0] if len(ordenados) > 1 else None

    if top1.score < score_minimo:
        return DecisaoCamada1("sem_match", top1=top1, top2=top2)

    # top2 ausente ou com score 0 passa direto (razao infinita).
    if top2 is not None and top2.score > 0 and top1.score / top2.score < fator_destaque:
        return DecisaoCamada1("ambiguo", top1=top1, top2=top2)

    return DecisaoCamada1("vinculado", vinculos=tuple(ordenados[0]), top1=top1, top2=top2)


def buscar_candidatos(
    cur, miolo: str, marca_pasta: str, busca_global: bool = True,
) -> list[Candidato]:
    """Candidatos por trigram: dentro da marca, ou no catalogo inteiro
    quando a pasta nao e uma marca (GENERICO)."""
    if busca_global and marca_pasta.strip().upper() in PASTAS_SEM_MARCA:
        cur.execute(SQL_TRIGRAM_GLOBAL, {"miolo": miolo, "limite": LIMITE_CANDIDATOS})
    else:
        cur.execute(
            SQL_TRIGRAM_NA_MARCA,
            {"miolo": miolo, "marca": marca_pasta, "limite": LIMITE_CANDIDATOS},
        )
    return [Candidato(codigo, descricao, float(score)) for codigo, descricao, score in cur.fetchall()]


def executar_camada1(cur, dry_run: bool) -> ResultadoCamada1:
    """Vincula documentos por marca + trigram no nome do arquivo.

    So processa documentos cuja marca_pasta existe no catalogo (o SQL ja
    filtra marcas orfas). Aplica limiar duplo (score minimo + fator de
    destaque) — na duvida, o documento vai para revisao em vez de
    vincular. Documentos regulatorios sao pulados por regex.

    Args:
        cur: cursor ativo (mesma transacao do chamador).
        dry_run: se True, nao grava; apenas contabiliza o que faria.

    Returns:
        ResultadoCamada1 com contagem de vinculos e residuo categorizado
        (regulatorios, ambiguos, sem_match) para triagem posterior.
    """
    resultado = ResultadoCamada1()

    cur.execute(SQL_DOCS_PARA_CAMADA1)
    documentos = cur.fetchall()

    for documento_id, nome_arquivo, marca_pasta in documentos:
        if REGULATORIO_REGEX.search(nome_arquivo):
            resultado.regulatorios += 1
            logger.debug("  [regulatorio] %s", nome_arquivo)
            continue

        miolo = _extrair_miolo(nome_arquivo)
        if not miolo:
            resultado.sem_match.append(nome_arquivo)
            continue

        decisao = decidir_camada1(miolo, buscar_candidatos(cur, miolo, marca_pasta))

        if decisao.categoria == "sem_match":
            resultado.sem_match.append(nome_arquivo)
            if decisao.top1 is not None:
                logger.debug(
                    "  [sem_match] %s -> melhor %.3f (%s)",
                    nome_arquivo, decisao.top1.score, decisao.top1.descricao,
                )
            continue

        if decisao.categoria == "ambiguo":
            resultado.ambiguos.append(
                f"{nome_arquivo} -> {decisao.top1.descricao} ({decisao.top1.score:.3f}) "
                f"vs {decisao.top2.descricao} ({decisao.top2.score:.3f})"
            )
            logger.debug("  [ambiguo] %s", nome_arquivo)
            continue

        # Passou nos dois limiares: vinculo confiavel (um ou mais tamanhos).
        metodo = (
            METODO_TRIGRAM_GLOBAL
            if marca_pasta.strip().upper() in PASTAS_SEM_MARCA
            else METODO_MARCA_TRIGRAM
        )
        for candidato in decisao.vinculos:
            _vincular(
                cur,
                produto_codigo=candidato.codigo,
                documento_id=documento_id,
                confianca=round(candidato.score, 4),
                metodo=metodo,
                dry_run=dry_run,
            )
            logger.info(
                "  [vinculado] %s -> %s (%.3f)",
                nome_arquivo, candidato.descricao, candidato.score,
            )
        resultado.vinculados += 1

    return resultado


def _imprimir_resumo(
    contadores: Contadores,
    resultado1: ResultadoCamada1,
    dry_run: bool,
) -> None:
    """Imprime o resumo consolidado das duas camadas."""
    prefixo = "[DRY-RUN] " if dry_run else ""
    logger.info("=" * 70)
    logger.info("%sRESUMO DA VINCULACAO", prefixo)
    logger.info("=" * 70)
    logger.info("Camada 0 (codigo no prefixo):")
    logger.info("  Vinculados:          %d", contadores.vinculados)
    logger.info("  Sem codigo:          %d", contadores.sem_codigo)
    logger.info("  Codigo inexistente:  %d", contadores.codigo_inexistente)
    logger.info("-" * 70)
    logger.info("Camada 1 (marca + trigram):")
    logger.info("  Vinculados:          %d", resultado1.vinculados)
    logger.info("  Regulatorios (pulados): %d", resultado1.regulatorios)
    logger.info("  Ambiguos (revisao):  %d", len(resultado1.ambiguos))
    logger.info("  Sem match:           %d", len(resultado1.sem_match))
    logger.info("=" * 70)

    if resultado1.ambiguos:
        logger.info("AMBIGUOS (top1 nao se destacou o suficiente):")
        for linha in resultado1.ambiguos[:30]:
            logger.info("  %s", linha)
        if len(resultado1.ambiguos) > 30:
            logger.info("  ... e mais %d", len(resultado1.ambiguos) - 30)
        logger.info("=" * 70)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Vincula documentos a produtos (Camadas 0 e 1)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra o que faria, sem gravar no banco.",
    )
    args = parser.parse_args()

    # Grava em produto_documento: roda como botuser. A role da aplicacao
    # (bot_app) so le o acervo -- ver db/provisioning/001b_bot_app_grants.sql.
    with get_connection(config.DATABASE_URL_ADMIN) as conn:
        with conn.cursor() as cur:
            logger.info("--- Camada 0: codigo no prefixo ---")
            contadores = executar_camada0(cur, args.dry_run)

            logger.info("--- Camada 1: marca + trigram ---")
            resultado1 = executar_camada1(cur, args.dry_run)

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

    _imprimir_resumo(contadores, resultado1, args.dry_run)


if __name__ == "__main__":
    main()