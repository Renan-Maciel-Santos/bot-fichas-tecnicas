"""
Testes do repositorio de controle de acesso.

Cobrem as duas politicas que a API aplica antes de consumir a OpenAI:
autorizacao por telefone e idempotencia por message_id.
"""
import pytest

from src.api.repository import (
    buscar_vendedor_autorizado,
    registrar_mensagem,
)

TELEFONE_ATIVO = "5511900000001"
TELEFONE_INATIVO = "5511900000002"
TELEFONE_DESCONHECIDO = "5511900000999"


@pytest.fixture
def vendedores(db_transaction):
    """Cadastra um vendedor ativo e um inativo dentro da transacao."""
    with db_transaction.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vendedores_autorizados (telefone, nome, ativo)
            VALUES (%s, 'Vendedor Ativo', TRUE),
                   (%s, 'Vendedor Inativo', FALSE)
            """,
            (TELEFONE_ATIVO, TELEFONE_INATIVO),
        )
    return db_transaction


@pytest.mark.integration
class TestBuscarVendedorAutorizado:
    def test_vendedor_ativo_retorna_nome(self, vendedores):
        assert buscar_vendedor_autorizado(TELEFONE_ATIVO, conn=vendedores) == (
            "Vendedor Ativo"
        )

    def test_vendedor_inativo_nao_autoriza(self, vendedores):
        """Desativacao e soft delete: o registro existe mas nao autoriza."""
        assert buscar_vendedor_autorizado(TELEFONE_INATIVO, conn=vendedores) is None

    def test_telefone_desconhecido_nao_autoriza(self, vendedores):
        assert (
            buscar_vendedor_autorizado(TELEFONE_DESCONHECIDO, conn=vendedores) is None
        )


@pytest.mark.integration
class TestRegistrarMensagem:
    def test_mensagem_nova_retorna_true(self, db_transaction):
        assert registrar_mensagem("MSG_A", TELEFONE_ATIVO, conn=db_transaction) is True

    def test_mensagem_repetida_retorna_false(self, db_transaction):
        """Reenvio de webhook pela Evolution nao deve reprocessar."""
        registrar_mensagem("MSG_B", TELEFONE_ATIVO, conn=db_transaction)
        assert registrar_mensagem("MSG_B", TELEFONE_ATIVO, conn=db_transaction) is False

    def test_mensagens_distintas_sao_independentes(self, db_transaction):
        assert registrar_mensagem("MSG_C", TELEFONE_ATIVO, conn=db_transaction) is True
        assert registrar_mensagem("MSG_D", TELEFONE_ATIVO, conn=db_transaction) is True