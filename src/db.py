"""
Helpers para conexão com o Postgres.
Wrap simples sobre psycopg2 com context manager.
"""
import logging
from contextlib import contextmanager
import psycopg2
from psycopg2.extras import RealDictCursor

from src import config

log = logging.getLogger(__name__)


@contextmanager
def get_connection(url: str | None = None):
    """
    Context manager para conexão com o banco.
    Por padrão conecta como a role da aplicação (somente leitura, salvo o
    log de consultas). Scripts de manutenção que escrevem no acervo passam
    config.DATABASE_URL_ADMIN explicitamente.
    Faz commit em caso de sucesso, rollback em caso de erro,
    e sempre fecha a conexão.

    Exemplo:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM produtos LIMIT 5")
                for row in cur.fetchall():
                    print(row)
    """
    conn = psycopg2.connect(url or config.DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("Erro na transação, rollback executado")
        raise
    finally:
        conn.close()


@contextmanager
def get_dict_cursor():
    """
    Atalho para conseguir cursor que retorna dicts (em vez de tuplas).
    Útil para leitura de dados.

    Exemplo:
        with get_dict_cursor() as cur:
            cur.execute("SELECT codigo, descricao FROM produtos LIMIT 5")
            for row in cur.fetchall():
                print(row["descricao"])
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur


def test_connection() -> bool:
    """Verifica se o banco está acessível. Retorna True se ok."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:
        log.error(f"Conexão falhou: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if test_connection():
        print("✓ Conexão com banco funcionando")
    else:
        print("✗ Falha ao conectar")

@contextmanager
def _conn_or_get(conn):
    """
    Helper interno: se `conn` for None, abre uma nova via get_connection()
    (com o ciclo de commit/rollback/close do get_connection). Se `conn`
    for passado, apenas o repassa — QUEM controla a transacao e o
    chamador (util para testes com rollback e para propagar a conexao
    entre chamadas aninhadas na mesma transacao).
    """
    if conn is not None:
        yield conn
    else:
        with get_connection() as new_conn:
            yield new_conn