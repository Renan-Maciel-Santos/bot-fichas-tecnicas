"""
Testes de integracao para src/query/responder.py.

Estrategia: o responder e um orquestrador, entao os modulos abaixo
(classifier, search_structured, search_vector, openai_client) sao
mockados. O banco entra APENAS para verificar o log em `consultas`.
Rollback no teardown via fixture db_transaction.

Cada caminho do responder (fora_escopo, busca_caracteristica,
propriedades_produto, fetch_documento, pergunta_aberta) e exercitado
com cenarios notaveis: resultado feliz, resultado vazio, ambiguidade
e — no caminho pergunta_aberta — o fallback de re-busca sem filtro.
"""

from unittest.mock import MagicMock

import pytest

from src.query import responder as responder_mod
from src.query.classifier import Classificacao
from src.query.responder import (
    DISCLAIMER,
    FATOR_DESTAQUE,
    MSG_FORA_ESCOPO,
    MSG_NAO_ENCONTREI,
    MSG_PRODUTO_NAO_ENCONTRADO,
    responder,
)
from src.query.search_structured import (
    DocumentoProduto,
    ProdutoCandidato,
    ResultadoPropriedade,
)
from src.query.search_vector import ChunkEncontrado
from tests.helpers import (
    NoopContext,
    fake_classificacao,
    mockar_classificador,
    mockar_openai_client,
)


# --- Helpers para instalar mocks -------------------------------------
# Definicoes em tests/helpers.py (compartilhadas com
# tests/test_responder_contexto.py). Os alias com underscore preservam
# os nomes usados no corpo dos testes deste modulo.

_fake_classificacao = fake_classificacao
_mockar_classificador = mockar_classificador
_mockar_openai_client = mockar_openai_client


# --- fora_escopo -----------------------------------------------------

@pytest.mark.integration
class TestForaEscopo:
    def test_resposta_contem_mensagem_e_disclaimer(
        self, db_transaction, monkeypatch
    ):
        _mockar_classificador(
            monkeypatch, _fake_classificacao(intencao="fora_escopo")
        )
        # _NoopContext, e nao a conexao crua: `with conn:` num psycopg2
        # COMMITA ao sair, o que confirmaria a transacao da fixture e
        # deixaria a linha de log no banco de dev.
        monkeypatch.setattr(
            responder_mod, "get_connection", lambda: _NoopContext(db_transaction)
        )

        r = responder("qual a capital da franca?")

        assert MSG_FORA_ESCOPO in r.texto
        assert DISCLAIMER in r.texto
        assert r.intencao == "fora_escopo"
        assert r.produtos_referenciados == []
        assert r.documentos_enviados == []

    def test_log_em_consultas_grava_intencao_correta(
        self, db_transaction, monkeypatch
    ):
        _mockar_classificador(
            monkeypatch, _fake_classificacao(intencao="fora_escopo")
        )
        # A conexao usada pelo _log_consulta precisa ser a mesma da
        # transacao do teste, senao o SELECT nao ve a linha.
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        responder("qual a capital da franca?", canal="teste")

        with db_transaction.cursor() as cur:
            cur.execute(
                "SELECT intencao_detectada, canal FROM consultas "
                "WHERE canal = %s ORDER BY created_at DESC LIMIT 1",
                ("teste",),
            )
            row = cur.fetchone()

        assert row is not None
        assert row[0] == "fora_escopo"
        assert row[1] == "teste"


_NoopContext = NoopContext


# --- busca_caracteristica --------------------------------------------

@pytest.mark.integration
class TestBuscaCaracteristica:
    def test_resultados_formatados_com_citacao(self, monkeypatch, db_transaction):
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="busca_caracteristica",
            campo="ingrediente_ativo",
            valor_busca="hipoclorito",
        ))
        monkeypatch.setattr(
            responder_mod, "produtos_por_propriedade",
            lambda campo, valor: [
                ResultadoPropriedade(
                    produto_codigo=9000001,
                    produto_descricao="AGUA SANITARIA LIMPAX 5L",
                    marca="LIMPAX",
                    campo="ingrediente_ativo",
                    valor="Hipoclorito de Sódio",
                    valor_numerico=None,
                    unidade=None,
                    confianca=0.95,
                    documento_fonte="FDS Agua Sanitaria Limpax.pdf",
                    tipo_documento="FDS",
                ),
            ],
        )
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("qual produto tem hipoclorito?")

        assert "AGUA SANITARIA LIMPAX 5L" in r.texto
        assert "Hipoclorito de Sódio" in r.texto
        assert "FDS de Agua Sanitaria Limpax" in r.texto  # citation.py em acao
        assert 9000001 in r.produtos_referenciados

    def test_sem_resultados_devolve_nao_encontrei(
        self, monkeypatch, db_transaction
    ):
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="busca_caracteristica",
            campo="ingrediente_ativo",
            valor_busca="xpto",
        ))
        monkeypatch.setattr(
            responder_mod, "produtos_por_propriedade", lambda campo, valor: []
        )
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("tem algum com xpto?")

        assert MSG_NAO_ENCONTREI in r.texto
        assert r.produtos_referenciados == []


# --- propriedades_produto --------------------------------------------

@pytest.mark.integration
class TestPropriedadesProduto:
    def _instalar_resolver(self, monkeypatch, candidatos: list[ProdutoCandidato]):
        monkeypatch.setattr(
            responder_mod, "resolver_produto", lambda termo: candidatos
        )

    def _instalar_props(self, monkeypatch, props: list[ResultadoPropriedade]):
        monkeypatch.setattr(
            responder_mod, "propriedades_de_produto", lambda codigo: props
        )

    def test_produto_unico_lista_propriedades_com_citacao(
        self, monkeypatch, db_transaction
    ):
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="propriedades_produto",
            produto_termo="agua sanitaria limpax",
        ))
        self._instalar_resolver(monkeypatch, [
            ProdutoCandidato(
                codigo=9000001,
                descricao="AGUA SANITARIA LIMPAX 5L",
                marca="LIMPAX",
                score=1.0,
            ),
        ])
        self._instalar_props(monkeypatch, [
            ResultadoPropriedade(
                produto_codigo=9000001,
                produto_descricao="AGUA SANITARIA LIMPAX 5L",
                marca="LIMPAX",
                campo="ph", valor="10 - 13",
                valor_numerico=None, unidade=None,
                confianca=0.9,
                documento_fonte="FDS Agua Sanitaria Limpax.pdf",
                tipo_documento="FDS",
            ),
        ])
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("qual o pH da agua sanitaria limpax?")

        assert "ph: 10 - 13" in r.texto
        assert "FDS de Agua Sanitaria Limpax" in r.texto
        assert 9000001 in r.produtos_referenciados

    def test_ambiguidade_gera_pergunta_de_esclarecimento(
        self, monkeypatch, db_transaction
    ):
        """Dois candidatos com scores proximos -> nao pode adivinhar."""
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="propriedades_produto",
            produto_termo="detergente",
        ))
        # Ratio = 0.30 / 0.28 = 1.07 (abaixo do FATOR_DESTAQUE 1.3).
        self._instalar_resolver(monkeypatch, [
            ProdutoCandidato(1, "DETERGENTE NEUTRO 5L", "LIMPAX", 0.30),
            ProdutoCandidato(2, "DETERGENTE ALCALINO 5L", "OUTRA", 0.28),
        ])
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("qual o pH do detergente?")

        assert r.precisa_esclarecimento is True
        assert "1." in r.texto and "2." in r.texto  # numerou candidatos

    def test_produto_nao_encontrado_devolve_mensagem_apropriada(
        self, monkeypatch, db_transaction
    ):
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="propriedades_produto",
            produto_termo="xpto imaginario",
        ))
        self._instalar_resolver(monkeypatch, [])
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("qual o pH do xpto?")

        assert MSG_PRODUTO_NAO_ENCONTRADO in r.texto
        assert r.precisa_esclarecimento is True

    def test_produto_sem_propriedades_extraidas(
        self, monkeypatch, db_transaction
    ):
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="propriedades_produto",
            produto_termo="folhabela",
        ))
        self._instalar_resolver(monkeypatch, [
            ProdutoCandidato(2, "PAPEL FOLHABELA", "FOLHABELA", 1.0),
        ])
        self._instalar_props(monkeypatch, [])
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("propriedades do folhabela?")

        assert "Nao ha propriedades" in r.texto or "Não ha propriedades" in r.texto
        assert 2 in r.produtos_referenciados


# --- fetch_documento -------------------------------------------------

@pytest.mark.integration
class TestFetchDocumento:
    def test_produto_com_documentos_lista_com_url(
        self, monkeypatch, db_transaction
    ):
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="fetch_documento",
            produto_termo="agua sanitaria",
        ))
        monkeypatch.setattr(responder_mod, "resolver_produto", lambda t: [
            ProdutoCandidato(9000001, "AGUA SANITARIA LIMPAX", "LIMPAX", 1.0),
        ])
        monkeypatch.setattr(responder_mod, "documentos_de_produto", lambda c: [
            DocumentoProduto(
                documento_id="doc-1",
                nome_arquivo="FDS Agua Sanitaria.pdf",
                tipo="FDS",
                url_drive="https://drive.google.com/x",
            ),
        ])
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("me manda a FDS da agua sanitaria")

        assert "FDS de Agua Sanitaria" in r.texto
        assert "drive.google.com/x" in r.texto
        assert "doc-1" in r.documentos_enviados

    def test_produto_sem_documentos_devolve_mensagem_apropriada(
        self, monkeypatch, db_transaction
    ):
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="fetch_documento",
            produto_termo="produto sem docs",
        ))
        monkeypatch.setattr(responder_mod, "resolver_produto", lambda t: [
            ProdutoCandidato(999, "PRODUTO SEM DOCS", "X", 1.0),
        ])
        monkeypatch.setattr(responder_mod, "documentos_de_produto", lambda c: [])
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("manda a ficha do produto sem docs")

        assert "Nao ha documentos" in r.texto or "Não ha documentos" in r.texto


# --- pergunta_aberta -------------------------------------------------

@pytest.mark.integration
class TestPerguntaAberta:
    def test_chunks_recuperados_llm_redige_com_citacao(
        self, monkeypatch, db_transaction
    ):
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="pergunta_aberta",
        ))
        monkeypatch.setattr(responder_mod, "buscar_chunks", lambda p, filtro_produto_codigo=None: [
            ChunkEncontrado(
                documento_id="doc-1",
                chunk_index=0,
                produto_codigo=9000001,
                texto="Em caso de contato com os olhos, lave com agua.",
                similaridade=0.75,
                documento_fonte="FDS Agua Sanitaria.pdf",
                tipo_documento="FDS",
            ),
        ])
        _mockar_openai_client(
            monkeypatch,
            "Lave os olhos com agua conforme a FDS de Agua Sanitaria.",
        )
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("o que fazer em contato com os olhos?")

        assert "Lave os olhos" in r.texto
        assert DISCLAIMER in r.texto
        assert "doc-1" in r.documentos_enviados

    def test_chunks_vazios_devolve_nao_encontrei_sem_chamar_llm(
        self, monkeypatch, db_transaction
    ):
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="pergunta_aberta",
        ))
        monkeypatch.setattr(responder_mod, "buscar_chunks", lambda p, filtro_produto_codigo=None: [])
        mock_client = _mockar_openai_client(monkeypatch, "resposta que nao devia acontecer")
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("qualquer pergunta sem match")

        assert MSG_NAO_ENCONTREI in r.texto
        # Regra de ouro: sem fonte, sem LLM. Prova por nao-chamada.
        mock_client.chat.completions.create.assert_not_called()

    def test_fallback_re_busca_sem_filtro_quando_produto_filtrado_nao_retorna(
        self, monkeypatch, db_transaction
    ):
        """
        Se ha produto identificado mas a busca filtrada e vazia, o
        responder faz uma segunda busca SEM filtro (mais tolerante).
        """
        _mockar_classificador(monkeypatch, _fake_classificacao(
            intencao="pergunta_aberta",
            produto_termo="limpax",
        ))
        monkeypatch.setattr(responder_mod, "resolver_produto", lambda t: [
            ProdutoCandidato(9000001, "AGUA SANITARIA LIMPAX", "LIMPAX", 1.0),
        ])

        chamadas = []
        def fake_buscar(pergunta, filtro_produto_codigo=None):
            chamadas.append(filtro_produto_codigo)
            # Primeira chamada (com filtro): vazia. Segunda (sem): 1 chunk.
            if filtro_produto_codigo is not None:
                return []
            return [ChunkEncontrado(
                documento_id="doc-1",
                chunk_index=0,
                produto_codigo=None,
                texto="conteudo generico",
                similaridade=0.6,
                documento_fonte="Doc Geral.pdf",
                tipo_documento=None,
            )]

        monkeypatch.setattr(responder_mod, "buscar_chunks", fake_buscar)
        _mockar_openai_client(monkeypatch, "resposta baseada no doc geral")
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("pergunta sobre limpax")

        assert chamadas == [9000001, None]  # filtro primeiro, depois sem
        assert "doc-1" in r.documentos_enviados


# --- Robustez ---------------------------------------------------------

@pytest.mark.integration
class TestRobustez:
    def test_excecao_interna_vira_resposta_de_indisponibilidade(
        self, monkeypatch, db_transaction
    ):
        """classificar explode -> responder devolve mensagem tecnica, nao propaga."""
        def _explode(pergunta):
            raise RuntimeError("simulando falha total")

        monkeypatch.setattr(responder_mod, "classificar", _explode)
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        r = responder("qualquer pergunta")

        assert "problema tecnico" in r.texto
        assert r.intencao == "erro"
        assert r.erro is not None
        assert "simulando falha total" in r.erro

    def test_log_em_consultas_grava_mesmo_em_erro(
        self, monkeypatch, db_transaction
    ):
        """Best-effort do _log_consulta: erro na resposta nao impede o log."""
        def _explode(pergunta):
            raise RuntimeError("boom")

        monkeypatch.setattr(responder_mod, "classificar", _explode)
        monkeypatch.setattr(
            responder_mod, "get_connection",
            lambda: _NoopContext(db_transaction),
        )

        responder("qualquer pergunta", canal="teste_erro")

        with db_transaction.cursor() as cur:
            cur.execute(
                "SELECT intencao_detectada, erro FROM consultas "
                "WHERE canal = %s ORDER BY created_at DESC LIMIT 1",
                ("teste_erro",),
            )
            row = cur.fetchone()

        assert row is not None
        assert row[0] == "erro"
        assert "boom" in row[1]


# --- Salvaguarda de constantes ---------------------------------------

def test_fator_destaque_confirma_valor_calibrado():
    """
    FATOR_DESTAQUE = 1.3 foi calibrado empiricamente na Fase 4:
    ratio 1.40 resolve 'agua sanitaria limpax', 1.13 ainda pede esclarecimento.
    Este teste vermelho denuncia mudanca silenciosa da constante.
    """
    assert FATOR_DESTAQUE == 1.3