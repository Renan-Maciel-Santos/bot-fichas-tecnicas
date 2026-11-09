"""
Testes do motor para o pedido de documento: tipo pedido e duplicatas.

Pedir "a ficha tecnica" e receber a FDS e pior do que receber um "nao
tenho": sao documentos com finalidades diferentes e o vendedor pode
repassar o errado ao cliente.

Mesma estrategia de test_responder.py: camadas de baixo mockadas, banco
so para o log em `consultas`, rollback no teardown.
"""

import pytest

from src.query import responder as responder_mod
from src.query.responder import responder
from src.query.search_structured import DocumentoProduto, ProdutoCandidato
from tests.helpers import fake_classificacao, mockar_classificador, mockar_log_consulta

PRODUTO_LIMPAX = ProdutoCandidato(9000001, "AGUA SANITARIA LIMPAX 1L", "LIMPAX", 1.0)


@pytest.mark.integration
class TestTipoDeDocumentoPedido:
    ACERVO = [
        DocumentoProduto("doc-ft", "FT Agua Sanitaria.pdf", "FT",
                         "https://drive.google.com/ft"),
        DocumentoProduto("doc-fds", "FDS Agua Sanitaria.pdf", "FDS",
                         "https://drive.google.com/fds"),
    ]

    def _montar(self, monkeypatch, db_transaction, tipo, acervo=None):
        mockar_classificador(monkeypatch, fake_classificacao(
            intencao="fetch_documento",
            produto_termo="agua sanitaria",
            tipo_documento=tipo,
        ))
        monkeypatch.setattr(
            responder_mod, "resolver_produto", lambda t: [PRODUTO_LIMPAX]
        )
        monkeypatch.setattr(
            responder_mod, "documentos_de_produto",
            lambda c: self.ACERVO if acervo is None else acervo,
        )
        mockar_log_consulta(monkeypatch, db_transaction)

    def test_tipo_pedido_filtra_o_acervo(self, monkeypatch, db_transaction):
        self._montar(monkeypatch, db_transaction, "FT")

        r = responder("me manda a ficha tecnica da agua sanitaria")

        assert r.documentos_enviados == ["doc-ft"]
        assert "drive.google.com/fds" not in r.texto

    def test_sem_tipo_pedido_devolve_tudo(self, monkeypatch, db_transaction):
        """"manda os documentos do X" nao tem por que filtrar."""
        self._montar(monkeypatch, db_transaction, None)

        r = responder("manda os documentos da agua sanitaria")

        assert sorted(r.documentos_enviados) == ["doc-fds", "doc-ft"]

    def test_tipo_ausente_no_acervo_nao_entrega_outro(
        self, monkeypatch, db_transaction
    ):
        """
        O produto tem FT e FDS, mas nao tem laudo. A resposta diz isso
        e lista o que existe — sem anexar nada, porque
        documentos_enviados alimenta o anexo do n8n.
        """
        self._montar(monkeypatch, db_transaction, "LAUDO")

        r = responder("manda o laudo da agua sanitaria")

        assert "laudo" in r.texto.lower()
        assert r.documentos_enviados == []
        assert "FDS de Agua Sanitaria" in r.texto  # mostra o que ha

    def test_produto_sem_documento_algum(self, monkeypatch, db_transaction):
        """Distinto de "tem documento, mas nao do tipo pedido"."""
        self._montar(monkeypatch, db_transaction, "FT", acervo=[])

        r = responder("manda a ficha tecnica da agua sanitaria")

        assert "Nao ha documentos" in r.texto
        assert r.documentos_enviados == []


@pytest.mark.integration
class TestDuplicatasNaBase:
    def _montar(self, monkeypatch, db_transaction, acervo):
        mockar_classificador(monkeypatch, fake_classificacao(
            intencao="fetch_documento",
            produto_termo="agua sanitaria",
        ))
        monkeypatch.setattr(
            responder_mod, "resolver_produto", lambda t: [PRODUTO_LIMPAX]
        )
        monkeypatch.setattr(responder_mod, "documentos_de_produto", lambda c: acervo)
        mockar_log_consulta(monkeypatch, db_transaction)

    def test_duplicatas_sao_colapsadas(self, monkeypatch, db_transaction):
        """
        A ingestao gerou o mesmo arquivo em mais de uma pasta de marca,
        parte sem url_drive. Sem colapsar, o vendedor recebe a mesma
        ficha duas vezes, uma delas sem link — parece defeito do bot.
        """
        self._montar(monkeypatch, db_transaction, [
            DocumentoProduto("doc-a", "FDS Agua Sanitaria.pdf", "FDS", None),
            DocumentoProduto("doc-b", "FDS Agua Sanitaria.pdf", "FDS",
                             "https://drive.google.com/fds"),
        ])

        r = responder("manda a ficha da agua sanitaria")

        assert r.texto.count("FDS de Agua Sanitaria") == 1
        # Entre duplicatas vence a que TEM link: e a unica acionavel.
        assert r.documentos_enviados == ["doc-b"]

    def test_documentos_distintos_nao_sao_colapsados(
        self, monkeypatch, db_transaction
    ):
        """A deduplicacao nao pode engolir documento legitimo."""
        self._montar(monkeypatch, db_transaction, [
            DocumentoProduto("doc-ft", "FT Agua Sanitaria.pdf", "FT",
                             "https://drive.google.com/ft"),
            DocumentoProduto("doc-fds", "FDS Agua Sanitaria.pdf", "FDS",
                             "https://drive.google.com/fds"),
        ])

        r = responder("manda os documentos da agua sanitaria")

        assert sorted(r.documentos_enviados) == ["doc-fds", "doc-ft"]

    def test_documento_sem_link_e_declarado(self, monkeypatch, db_transaction):
        """Linha truncada parece erro de formatacao; dizer e melhor."""
        self._montar(monkeypatch, db_transaction, [
            DocumentoProduto("doc-x", "FT Xpto.pdf", "FT", None),
        ])

        r = responder("manda a ficha do xpto")

        assert "sem link" in r.texto.lower()
