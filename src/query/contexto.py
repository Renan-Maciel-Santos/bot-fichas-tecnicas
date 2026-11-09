"""
Estado de conversa para a desambiguacao de produto (Fase 4).

Problema que resolve: quando o termo do vendedor casa com varios
produtos, o motor responde "Encontrei mais de um produto parecido.
Qual deles? 1... 2... 3..." e pede que ele responda com o numero. Sem
guardar nada, a mensagem seguinte ("2") chega ao classificador sem
contexto e vira `fora_escopo` — a conversa morre exatamente no ponto
em que o vendedor fez o que foi pedido.

Escopo deliberadamente estreito. Isto NAO e historico de conversa nem
memoria de dialogo:

- uma unica pendencia por vendedor (UPSERT sobre a PK);
- validade curta (`TTL_MINUTOS`), porque um "2" que chega horas depois
  quase certamente nao se refere aquela lista;
- consumida na primeira leitura util e descartada quando o vendedor
  muda de assunto.

O historico completo continua em `consultas` (append-only, auditoria).

`interpretar_selecao` e funcao pura: nao toca no banco, e onde mora a
decisao "isto e uma escolha ou uma pergunta nova?", e por isso e a
parte com mais teste.
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass

from src.config import CONTEXTO_TTL_MINUTOS
from src.db import _conn_or_get

logger = logging.getLogger(__name__)

# Janela em que uma pendencia ainda e considerada viva.
TTL_MINUTOS = CONTEXTO_TTL_MINUTOS

# Acima disto a mensagem e tratada como pergunta nova, mesmo contendo
# um numero. "2" e escolha; "qual o pH do produto 9000001" e pergunta
# completa e deve passar pelo classificador normalmente.
MAX_PALAVRAS_SELECAO = 5

SQL_SALVAR = """
    INSERT INTO contexto_conversa
        (vendedor_id, intencao, campo, tipo_documento, pergunta_original,
         candidatos, criado_em)
    VALUES
        (%(vendedor_id)s, %(intencao)s, %(campo)s, %(tipo_documento)s,
         %(pergunta)s, %(candidatos)s::jsonb, now())
    ON CONFLICT (vendedor_id) DO UPDATE SET
        intencao          = EXCLUDED.intencao,
        campo             = EXCLUDED.campo,
        tipo_documento    = EXCLUDED.tipo_documento,
        pergunta_original = EXCLUDED.pergunta_original,
        candidatos        = EXCLUDED.candidatos,
        criado_em         = EXCLUDED.criado_em
"""

# O filtro de TTL vive no SQL para que uma pendencia expirada nunca
# chegue a aplicacao — nao ha caminho em que ela seja usada por engano.
SQL_BUSCAR = """
    SELECT intencao, campo, tipo_documento, pergunta_original, candidatos
    FROM contexto_conversa
    WHERE vendedor_id = %(vendedor_id)s
      AND criado_em > now() - make_interval(mins => %(ttl)s)
"""

SQL_LIMPAR = """
    DELETE FROM contexto_conversa
    WHERE vendedor_id = %(vendedor_id)s
"""


@dataclass(frozen=True)
class CandidatoPendente:
    """Um produto da lista apresentada ao vendedor."""

    ordem: int          # posicao mostrada na mensagem (1, 2, 3...)
    codigo: int
    descricao: str
    marca: str


@dataclass(frozen=True)
class ContextoPendente:
    """Desambiguacao aguardando escolha do vendedor."""

    intencao: str
    campo: str | None
    tipo_documento: str | None
    pergunta_original: str
    candidatos: list[CandidatoPendente]


def _normalizar(texto: str) -> str:
    """Minusculas sem acento, para comparar marca falada com marca do catalogo."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def interpretar_selecao(
    mensagem: str,
    candidatos: list[CandidatoPendente],
) -> int | None:
    """
    Decide se a mensagem e a escolha de um dos candidatos.

    Aceita tres formas, na ordem em que sao especificas:
    1. codigo do produto ("9000001")
    2. ordinal da lista ("2", "o 2", "quero o 2")
    3. marca, quando ela identifica um unico candidato ("limpax")

    Conservador por design: na duvida retorna None e a mensagem segue
    para o classificador como pergunta nova. O custo de errar para
    menos e uma pergunta reclassificada; o custo de errar para mais e
    responder sobre o produto errado — inaceitavel em dado tecnico de
    seguranca quimica.

    Returns:
        O codigo do produto escolhido, ou None se nao for uma escolha.
    """
    texto = (mensagem or "").strip()
    if not texto or not candidatos:
        return None

    limpo = _normalizar(texto).strip(" .!?,")
    if len(limpo.split()) > MAX_PALAVRAS_SELECAO:
        return None

    numeros = re.findall(r"\d+", limpo)
    if len(numeros) == 1:
        n = int(numeros[0])

        # Codigo exato antes de ordinal: se o vendedor digitou o codigo,
        # e o codigo, mesmo que por acaso caiba na faixa dos ordinais.
        for c in candidatos:
            if c.codigo == n:
                return c.codigo

        if 1 <= n <= len(candidatos):
            return candidatos[n - 1].codigo

        # Numero que nao e nem codigo da lista nem posicao valida:
        # provavelmente outro produto. Deixa o classificador cuidar.
        return None

    if numeros:
        # Mais de um numero numa mensagem curta e ambiguo demais.
        return None

    # Sem numero: tenta a marca, e so aceita se ela isola um candidato.
    marcas = {
        c.codigo
        for c in candidatos
        if _normalizar(c.marca) and _normalizar(c.marca) in limpo
    }
    if len(marcas) == 1:
        return marcas.pop()

    return None


def salvar_contexto(
    vendedor_id: str,
    intencao: str,
    campo: str | None,
    tipo_documento: str | None,
    pergunta_original: str,
    candidatos: list[CandidatoPendente],
    conn=None,
) -> None:
    """
    Grava a pendencia de desambiguacao. Best-effort.

    Falhar aqui nao pode derrubar a resposta que ja foi montada: o
    vendedor recebe a lista mesmo que a pendencia nao persista (ele so
    perde a possibilidade de responder "2" e precisa repetir a pergunta
    com o produto completo).
    """
    payload = json.dumps(
        [
            {
                "ordem": c.ordem,
                "codigo": c.codigo,
                "descricao": c.descricao,
                "marca": c.marca,
            }
            for c in candidatos
        ]
    )
    try:
        with _conn_or_get(conn) as c:
            with c.cursor() as cur:
                cur.execute(
                    SQL_SALVAR,
                    {
                        "vendedor_id": vendedor_id,
                        "intencao": intencao,
                        "campo": campo,
                        "tipo_documento": tipo_documento,
                        "pergunta": pergunta_original,
                        "candidatos": payload,
                    },
                )
    except Exception:
        logger.exception(
            "Falha ao salvar contexto de conversa (resposta nao afetada)"
        )


def buscar_contexto(vendedor_id: str, conn=None) -> ContextoPendente | None:
    """
    Retorna a pendencia viva do vendedor, ou None.

    Best-effort pelo mesmo motivo de `salvar_contexto`: se a leitura
    falha, a mensagem segue o fluxo normal de classificacao.
    """
    if not vendedor_id:
        return None

    try:
        with _conn_or_get(conn) as c:
            with c.cursor() as cur:
                cur.execute(
                    SQL_BUSCAR,
                    {"vendedor_id": vendedor_id, "ttl": TTL_MINUTOS},
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("Falha ao ler contexto de conversa; seguindo sem ele")
        return None

    if not row:
        return None

    intencao, campo, tipo_documento, pergunta_original, candidatos_raw = row
    return ContextoPendente(
        intencao=intencao,
        campo=campo,
        tipo_documento=tipo_documento,
        pergunta_original=pergunta_original,
        candidatos=[
            CandidatoPendente(
                ordem=item["ordem"],
                codigo=item["codigo"],
                descricao=item["descricao"],
                marca=item["marca"],
            )
            for item in candidatos_raw
        ],
    )


def limpar_contexto(vendedor_id: str, conn=None) -> None:
    """Descarta a pendencia do vendedor. Best-effort."""
    if not vendedor_id:
        return

    try:
        with _conn_or_get(conn) as c:
            with c.cursor() as cur:
                cur.execute(SQL_LIMPAR, {"vendedor_id": vendedor_id})
    except Exception:
        logger.exception("Falha ao limpar contexto de conversa")
