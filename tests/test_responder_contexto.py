"""
Testes do motor para a escolha do vendedor na desambiguacao.

A desambiguacao so tem valor se a escolha chegar a algum lugar. Antes
do estado de conversa, o motor pedia um numero que nao saberia
interpretar — a conversa morria exatamente no ponto em que o vendedor
fazia o que foi mandado.

Mesma estrategia de test_responder.py: camadas de baixo mockadas, banco
so para o log em `consultas`, rollback no teardown.
"""

from unittest.mock import MagicMock

import pytest

from src.query import responder as responder_mod
from src.query.contexto import CandidatoPendente, ContextoPendente
from src.query.responder import responder
from src.query.search_structured import (
    DocumentoProduto,
    ProdutoCandidato,
    ResultadoPropriedade,
)
from tests.helpers import fake_classificacao, mockar_classificador, mockar_log_consulta

VENDEDOR = "5511900000456"

PRODUTO_LIMPAX = ProdutoCandidato(9000001, "AGUA SANITARIA LIMPAX 1L", "LIMPAX", 1.0)

AMBIGUOS = [
    ProdutoCandidato(9000008, "AGUA SANITARIA 5L CASACLEAN", "CASACLEAN", 0.50),
    ProdutoCandidato(9000001, "AGUA SANITARIA LIMPAX 1L", "LIMPAX", 0.48),
]


def _propriedade(campo, valor):
    return ResultadoPropriedade(
        produto_codigo=9000001,
        produto_descricao="AGUA SANITARIA LIMPAX 1L",
        marca="LIMPAX",
        campo=campo,
        valor=valor,
        valor_numerico=None,
        unidade=None,
        confianca=0.9,
        documento_fonte="FDS Limpax.pdf",
        tipo_documento="FDS",
    )


def _pendencia(intencao, campo=None, tipo_documento=None, candidatos=None):
    return ContextoPendente(
        intencao=intencao,
        campo=campo,
        tipo_documento=tipo_documento,
        pergunta_original="pergunta original do vendedor",
        candidatos=candidatos or [
            CandidatoPendente(1, 9000008, "AGUA SANITARIA 5L CASACLEAN", "CASACLEAN"),
            CandidatoPendente(2, 9000001, "AGUA SANITARIA LIMPAX 1L", "LIMPAX"),
        ],
    )


@pytest.mark.integration
class TestGravaPendencia:
    def test_ambiguidade_grava_a_pendencia(self, monkeypatch, db_transaction):
        mockar_classificador(monkeypatch, fake_classificacao(
            intencao="fetch_documento",
            produto_termo="agua sanitaria",
            tipo_documento="FT",
        ))
        monkeypatch.setattr(responder_mod, "resolver_produto", lambda t: AMBIGUOS)
        monkeypatch.setattr(responder_mod, "buscar_contexto", lambda v: None)
        gravado = {}
        monkeypatch.setattr(
            responder_mod, "salvar_contexto", lambda **kw: gravado.update(kw)
        )
        mockar_log_consulta(monkeypatch, db_transaction)

        r = responder("manda a ficha tecnica da agua sanitaria",
                      vendedor_id=VENDEDOR)

        assert r.precisa_esclarecimento is True
        assert gravado["intencao"] == "fetch_documento"
        # O tipo pedido precisa sobreviver: "2" depois deve dar a FT.
        assert gravado["tipo_documento"] == "FT"
        # A ordem gravada e a MESMA mostrada ao vendedor — e o que da
        # sentido ao "2".
        assert [c.ordem for c in gravado["candidatos"]] == [1, 2]
        assert [c.codigo for c in gravado["candidatos"]] == [9000008, 9000001]

    def test_sem_vendedor_id_nao_toca_no_contexto(
        self, monkeypatch, db_transaction
    ):
        """No terminal, sem identificacao, o comportamento e o de sempre."""
        buscar = MagicMock(return_value=None)
        monkeypatch.setattr(responder_mod, "buscar_contexto", buscar)
        mockar_classificador(monkeypatch, fake_classificacao(intencao="fora_escopo"))
        mockar_log_consulta(monkeypatch, db_transaction)

        responder("qual a capital da franca?")

        buscar.assert_not_called()


@pytest.mark.integration
class TestConsomePendencia:
    def test_escolha_reexecuta_a_intencao_original(
        self, monkeypatch, db_transaction
    ):
        monkeypatch.setattr(
            responder_mod, "buscar_contexto",
            lambda v: _pendencia("fetch_documento", tipo_documento="FT"),
        )
        monkeypatch.setattr(responder_mod, "limpar_contexto", lambda v: None)
        monkeypatch.setattr(
            responder_mod, "resolver_produto", lambda t: [PRODUTO_LIMPAX]
        )
        monkeypatch.setattr(responder_mod, "documentos_de_produto", lambda c: [
            DocumentoProduto("doc-ft-limpax", "FT Limpax.pdf", "FT",
                             "https://drive.google.com/ft"),
            DocumentoProduto("doc-fds-limpax", "FDS Limpax.pdf", "FDS",
                             "https://drive.google.com/fds"),
        ])
        classificador = MagicMock()
        monkeypatch.setattr(responder_mod, "classificar", classificador)
        mockar_log_consulta(monkeypatch, db_transaction)

        r = responder("2", vendedor_id=VENDEDOR)

        # A escolha nao passa pelo classificador: "2" sozinho seria
        # classificado como fora_escopo e a conversa morreria.
        classificador.assert_not_called()
        assert "AGUA SANITARIA LIMPAX 1L" in r.texto
        # E o tipo pedido na pergunta ORIGINAL foi honrado.
        assert r.documentos_enviados == ["doc-ft-limpax"]

    def test_escolha_consome_a_pendencia(self, monkeypatch, db_transaction):
        monkeypatch.setattr(
            responder_mod, "buscar_contexto",
            lambda v: _pendencia(
                "propriedades_produto", campo="ph",
                candidatos=[CandidatoPendente(1, 9000001, "LIMPAX 1L", "LIMPAX")],
            ),
        )
        limpou = []
        monkeypatch.setattr(responder_mod, "limpar_contexto", limpou.append)
        monkeypatch.setattr(
            responder_mod, "resolver_produto", lambda t: [PRODUTO_LIMPAX]
        )
        monkeypatch.setattr(
            responder_mod, "propriedades_de_produto",
            lambda c: [_propriedade("ph", "10 - 13")],
        )
        mockar_log_consulta(monkeypatch, db_transaction)

        r = responder("1", vendedor_id=VENDEDOR)

        assert "10 - 13" in r.texto
        assert limpou == [VENDEDOR]

    def test_campo_da_pergunta_original_e_preservado(
        self, monkeypatch, db_transaction
    ):
        """"qual o pH do X?" -> "2" responde o pH, nao a ficha inteira."""
        monkeypatch.setattr(
            responder_mod, "buscar_contexto",
            lambda v: _pendencia(
                "propriedades_produto", campo="ph",
                candidatos=[CandidatoPendente(1, 9000001, "LIMPAX 1L", "LIMPAX")],
            ),
        )
        monkeypatch.setattr(responder_mod, "limpar_contexto", lambda v: None)
        monkeypatch.setattr(
            responder_mod, "resolver_produto", lambda t: [PRODUTO_LIMPAX]
        )
        monkeypatch.setattr(responder_mod, "propriedades_de_produto", lambda c: [
            _propriedade("ph", "10 - 13"),
            _propriedade("ingrediente_ativo", "Hipoclorito"),
        ])
        mockar_log_consulta(monkeypatch, db_transaction)

        r = responder("1", vendedor_id=VENDEDOR)

        assert "10 - 13" in r.texto
        assert "Hipoclorito" not in r.texto


@pytest.mark.integration
class TestDescartaPendencia:
    def test_pergunta_nova_descarta_a_pendencia(self, monkeypatch, db_transaction):
        """
        Vendedor abandonou a escolha e perguntou outra coisa: a lista
        antiga nao pode capturar a proxima mensagem dele.
        """
        monkeypatch.setattr(
            responder_mod, "buscar_contexto", lambda v: _pendencia("fetch_documento")
        )
        limpou = []
        monkeypatch.setattr(responder_mod, "limpar_contexto", limpou.append)
        mockar_classificador(monkeypatch, fake_classificacao(intencao="fora_escopo"))
        mockar_log_consulta(monkeypatch, db_transaction)

        r = responder("qual a capital da franca?", vendedor_id=VENDEDOR)

        assert r.intencao == "fora_escopo"
        assert limpou == [VENDEDOR]

    def test_produto_escolhido_que_sumiu_nao_quebra_a_conversa(
        self, monkeypatch, db_transaction
    ):
        """
        Produto saiu do catalogo entre a pergunta e a escolha: cai no
        fluxo normal em vez de levantar excecao no meio da conversa.
        """
        monkeypatch.setattr(
            responder_mod, "buscar_contexto", lambda v: _pendencia("fetch_documento")
        )
        monkeypatch.setattr(responder_mod, "limpar_contexto", lambda v: None)
        monkeypatch.setattr(responder_mod, "resolver_produto", lambda t: [])
        mockar_classificador(monkeypatch, fake_classificacao(intencao="fora_escopo"))
        mockar_log_consulta(monkeypatch, db_transaction)

        r = responder("1", vendedor_id=VENDEDOR)

        assert r.intencao == "fora_escopo"
