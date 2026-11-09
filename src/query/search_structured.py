"""
Busca estruturada para perguntas factuais (Fase 4).

Templates SQL parametrizados e pre-definidos — o LLM classifica a
intencao e extrai parametros, mas NUNCA monta SQL. Toda consulta aqui
e fixa, com parametros via psycopg2 (%s), imune a SQL injection.

Funcoes:
- resolver_produto: termo falado -> candidatos do catalogo (trigram)
- produtos_por_propriedade: "quais produtos contem X?"
- propriedades_de_produto: "qual o pH do produto Y?"
- documentos_de_produto: "me manda a ficha do produto Y"

Toda resposta carrega procedencia (documento fonte, tipo e confianca)
para a camada de resposta citar corretamente.

Injecao de conexao: todas as funcoes aceitam um parametro opcional
`conn`. Producao chama sem argumento e cada funcao abre a propria
conexao. Testes de integracao passam a conexao da transacao para
compartilhar a mesma transacao (rollback no teardown).
"""

import logging
from dataclasses import dataclass

from src.db import _conn_or_get
from src.extraction import CAMPOS_VALIDOS

logger = logging.getLogger(__name__)

# Similaridade trigram minima para considerar um produto candidato.
# Abaixo disso, o termo provavelmente nao se refere a nada do catalogo.
MIN_SIMILARIDADE_PRODUTO = 0.15

# Maximo de candidatos retornados na resolucao de produto.
MAX_CANDIDATOS = 5

SQL_PRODUTO_POR_CODIGO = """
    SELECT codigo, descricao, marca
    FROM produtos
    WHERE codigo = %(codigo)s AND ativo = TRUE
"""

SQL_PRODUTO_POR_NOME = """
    SELECT codigo, descricao, marca,
           similarity(texto_busca, %(termo)s) AS score
    FROM produtos
    WHERE ativo = TRUE
      AND similarity(texto_busca, %(termo)s) >= %(min_score)s
    ORDER BY score DESC
    LIMIT %(limite)s
"""

SQL_PRODUTOS_POR_PROPRIEDADE = """
    SELECT DISTINCT
           p.codigo,
           p.descricao,
           p.marca,
           pp.campo,
           pp.valor,
           pp.confianca,
           d.nome_arquivo,
           d.tipo
    FROM propriedades_produto pp
    JOIN produtos p ON p.codigo = pp.produto_codigo
    LEFT JOIN documentos d ON d.id = pp.documento_id
    WHERE pp.campo = ANY(%(campos)s)
      AND unaccent(pp.valor) ILIKE unaccent(%(padrao)s)
      -- Propriedade sem documento de origem fica; de documento revogado, nao
      -- (migrations/004_soft_delete.sql, principio 4).
      AND (d.id IS NULL OR d.revogado_em IS NULL)
    ORDER BY pp.confianca DESC, p.descricao
"""

SQL_PROPRIEDADES_DE_PRODUTO = """
    SELECT pp.campo,
           pp.valor,
           pp.valor_numerico,
           pp.unidade,
           pp.confianca,
           d.nome_arquivo,
           d.tipo
    FROM propriedades_produto pp
    LEFT JOIN documentos d ON d.id = pp.documento_id
    WHERE pp.produto_codigo = %(codigo)s
      AND (d.id IS NULL OR d.revogado_em IS NULL)
    ORDER BY pp.campo, pp.confianca DESC
"""

# O mesmo PDF pode estar em duas pastas do Drive (ex.: a da marca e a
# local_amostra), virando dois documentos vinculados ao mesmo produto.
# Agrupa pelo hash do conteudo e manda uma copia so, preferindo a que
# nao esta em local_amostra. Sem hash, o documento nunca e agrupado.
# Revogados ficam de fora (migrations/004_soft_delete.sql, principio 4).
SQL_DOCUMENTOS_DE_PRODUTO = """
    SELECT id, nome_arquivo, tipo, url_drive
    FROM (
        SELECT DISTINCT ON (coalesce(d.hash_conteudo, d.id::text))
               d.id, d.nome_arquivo, d.tipo, d.url_drive
        FROM documentos d
        JOIN produto_documento pd ON pd.documento_id = d.id
        WHERE pd.produto_codigo = %(codigo)s
          AND d.revogado_em IS NULL
        ORDER BY coalesce(d.hash_conteudo, d.id::text),
                 d.marca_pasta = 'local_amostra',
                 d.nome_arquivo
    ) unicos
    ORDER BY nome_arquivo
"""


@dataclass(frozen=True)
class ProdutoCandidato:
    """Produto candidato na resolucao de um termo falado."""

    codigo: int
    descricao: str
    marca: str
    score: float  # 1.0 para lookup por codigo (match exato)


@dataclass(frozen=True)
class ResultadoPropriedade:
    """Uma propriedade encontrada, com procedencia."""

    produto_codigo: int
    produto_descricao: str
    marca: str
    campo: str
    valor: str
    valor_numerico: float | None
    unidade: str | None
    confianca: float
    documento_fonte: str | None
    tipo_documento: str | None  # "FT" | "FDS" | "LAUDO" | None


@dataclass(frozen=True)
class DocumentoProduto:
    """Documento vinculado a um produto (para citacao/anexo)."""

    documento_id: str
    nome_arquivo: str
    tipo: str | None
    url_drive: str | None


def resolver_produto(termo: str, conn=None) -> list[ProdutoCandidato]:
    """
    Resolve um termo falado pelo vendedor em candidatos do catalogo.

    Estagio 1: termo numerico -> lookup direto por codigo.
    Estagio 2: busca trigram sobre texto_busca, ranqueada por score.

    Returns:
        Lista de candidatos ordenada por score (vazia se nada plausivel).
        Mais de um candidato = ambiguidade: a camada de resposta deve
        pedir esclarecimento, nao escolher sozinha.
    """
    termo = (termo or "").strip()
    if not termo:
        return []

    with _conn_or_get(conn) as conn:
        with conn.cursor() as cur:
            if termo.isdigit():
                cur.execute(SQL_PRODUTO_POR_CODIGO, {"codigo": int(termo)})
                row = cur.fetchone()
                if row:
                    return [
                        ProdutoCandidato(
                            codigo=row[0], descricao=row[1], marca=row[2], score=1.0
                        )
                    ]
                return []

            cur.execute(
                SQL_PRODUTO_POR_NOME,
                {
                    "termo": termo,
                    "min_score": MIN_SIMILARIDADE_PRODUTO,
                    "limite": MAX_CANDIDATOS,
                },
            )
            return [
                ProdutoCandidato(
                    codigo=row[0],
                    descricao=row[1],
                    marca=row[2],
                    score=round(float(row[3]), 3),
                )
                for row in cur.fetchall()
            ]


def produtos_por_propriedade(
    campo: str,
    valor_busca: str,
    conn=None,
) -> list[ResultadoPropriedade]:
    """
    "Quais produtos contem/tem <valor> em <campo>?"

    Para campo = 'ingrediente_ativo', a busca inclui tambem
    'concentracao_ativo': nomes de ativos podem estar embutidos no valor
    da concentracao (limitacao conhecida da extracao, ver backlog).

    Raises:
        ValueError: se o campo nao pertence ao vocabulario fechado
            (defesa contra classifier alucinado).
    """
    if campo not in CAMPOS_VALIDOS:
        raise ValueError(f"Campo fora do vocabulario: {campo!r}")

    campos = [campo]
    if campo == "ingrediente_ativo":
        campos.append("concentracao_ativo")

    padrao = f"%{valor_busca.strip()}%"

    with _conn_or_get(conn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                SQL_PRODUTOS_POR_PROPRIEDADE,
                {"campos": campos, "padrao": padrao},
            )
            return [
                ResultadoPropriedade(
                    produto_codigo=row[0],
                    produto_descricao=row[1],
                    marca=row[2],
                    campo=row[3],
                    valor=row[4],
                    valor_numerico=None,
                    unidade=None,
                    confianca=float(row[5]),
                    documento_fonte=row[6],
                    tipo_documento=row[7],
                )
                for row in cur.fetchall()
            ]


def propriedades_de_produto(
    codigo: int,
    conn=None,
) -> list[ResultadoPropriedade]:
    """"Qual o pH / composicao / validade do produto <codigo>?" — todas
    as propriedades do produto, com confianca e fonte."""
    with _conn_or_get(conn) as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_PROPRIEDADES_DE_PRODUTO, {"codigo": codigo})
            rows = cur.fetchall()

        if not rows:
            return []

        # Busca descricao/marca uma unica vez para compor a procedencia.
        # Propaga conn para que a chamada aninhada compartilhe a mesma
        # transacao (essencial em testes com rollback).
        candidatos = resolver_produto(str(codigo), conn=conn)
        descricao = candidatos[0].descricao if candidatos else ""
        marca = candidatos[0].marca if candidatos else ""

    return [
        ResultadoPropriedade(
            produto_codigo=codigo,
            produto_descricao=descricao,
            marca=marca,
            campo=row[0],
            valor=row[1],
            valor_numerico=float(row[2]) if row[2] is not None else None,
            unidade=row[3],
            confianca=float(row[4]),
            documento_fonte=row[5],
            tipo_documento=row[6],
        )
        for row in rows
    ]


def documentos_de_produto(codigo: int, conn=None) -> list[DocumentoProduto]:
    """"Me manda a ficha/FISPQ do produto <codigo>" — documentos
    vinculados, com URL do Drive para anexo."""
    with _conn_or_get(conn) as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_DOCUMENTOS_DE_PRODUTO, {"codigo": codigo})
            return [
                DocumentoProduto(
                    documento_id=str(row[0]),
                    nome_arquivo=row[1],
                    tipo=row[2],
                    url_drive=row[3],
                )
                for row in cur.fetchall()
            ]