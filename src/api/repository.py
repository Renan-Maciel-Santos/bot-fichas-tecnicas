"""
Acesso a dados de controle de acesso e idempotencia.

Isola as consultas SQL da camada HTTP: as rotas nao conhecem SQL, e
este modulo nao conhece FastAPI. Segue o padrao de injecao ja usado
em src/query/ (`conn=None` + `_conn_or_get`).
"""
import logging

from src.db import _conn_or_get

log = logging.getLogger(__name__)

SQL_VENDEDOR_AUTORIZADO = """
    SELECT nome
    FROM vendedores_autorizados
    WHERE telefone = %(telefone)s
      AND ativo
"""

SQL_REGISTRAR_MENSAGEM = """
    INSERT INTO mensagens_processadas (message_id, remetente)
    VALUES (%(message_id)s, %(remetente)s)
    ON CONFLICT (message_id) DO NOTHING
"""

SQL_LIBERAR_MENSAGEM = """
    DELETE FROM mensagens_processadas
    WHERE message_id = %(message_id)s
"""


def buscar_vendedor_autorizado(telefone: str, conn=None) -> str | None:
    """
    Retorna o nome do vendedor se ele estiver autorizado e ativo.

    Args:
        telefone: numero em E.164 sem "+" (ex.: 5511999998888).
        conn: conexao opcional para propagar transacao (testes).

    Returns:
        Nome do vendedor, ou None se nao autorizado.
    """
    with _conn_or_get(conn) as c:
        with c.cursor() as cur:
            cur.execute(SQL_VENDEDOR_AUTORIZADO, {"telefone": telefone})
            row = cur.fetchone()
            return row[0] if row else None


def registrar_mensagem(message_id: str, remetente: str, conn=None) -> bool:
    """
    Registra a mensagem para dedupe de webhooks reenviados.

    O ON CONFLICT DO NOTHING deixa a decisao de duplicidade com o
    Postgres, de forma atomica: dois webhooks simultaneos com o mesmo
    message_id resultam em exatamente um True.

    Args:
        message_id: id da mensagem na Evolution API.
        remetente: telefone de origem, para auditoria.
        conn: conexao opcional para propagar transacao (testes).

    Returns:
        True se a mensagem e nova (deve ser processada).
        False se ja havia sido processada (reenvio da Evolution).
    """
    with _conn_or_get(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                SQL_REGISTRAR_MENSAGEM,
                {"message_id": message_id, "remetente": remetente},
            )
            return cur.rowcount == 1


def liberar_mensagem(message_id: str, conn=None) -> None:
    """
    Desfaz o registro de dedupe de uma mensagem que falhou.

    O registro acontece ANTES do motor rodar, para que dois webhooks
    simultaneos nao produzam duas respostas. O preco e que uma falha do
    motor deixa a mensagem marcada como processada sem nunca ter sido
    respondida: o reenvio da Evolution seria descartado como duplicata
    e o vendedor ficaria sem resposta nenhuma.

    Liberar o message_id devolve a mensagem ao fluxo, tornando o
    reenvio uma nova tentativa real.

    Best-effort: se o DELETE falhar, o pior caso e voltar ao
    comportamento anterior (uma mensagem perdida), nunca uma resposta
    duplicada.
    """
    try:
        with _conn_or_get(conn) as c:
            with c.cursor() as cur:
                cur.execute(SQL_LIBERAR_MENSAGEM, {"message_id": message_id})
    except Exception:
        log.exception("Falha ao liberar message_id apos erro do motor")