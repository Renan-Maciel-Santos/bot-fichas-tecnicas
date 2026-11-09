"""
Testes da camada HTTP (src/api/routes.py e src/api/deps.py).

Estrategia: TestClient do FastAPI com o repositorio e o motor mockados.
O que esta sob teste aqui e a POLITICA — quem entra, quem e barrado, em
que ordem, e o que sai no corpo da resposta — nao a busca nem o SQL,
que tem cobertura propria.

A ordem das barreiras e testada explicitamente: um remetente nao
autorizado ou um webhook reenviado nunca podem chegar ao motor, porque
o motor custa dinheiro (embeddings + LLM) e as barreiras nao.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src import config
from src.api import routes as routes_mod
from src.api.main import app
from src.query.responder import RespostaBot

CHAVE = "chave-de-teste"
AUTORIZADO = "5511900000123"

PAYLOAD = {
    "pergunta": "qual o pH da agua sanitaria limpax?",
    "remetente": AUTORIZADO,
    "message_id": "MSG_TESTE_1",
}


@pytest.fixture
def client(monkeypatch):
    """
    TestClient com API key fixa e as barreiras liberadas por padrao.

    Cada teste sobrescreve o que precisa negar. O default e o caminho
    feliz para que a intencao de cada teste fique no proprio teste.
    """
    monkeypatch.setattr(config, "API_KEY", CHAVE)
    monkeypatch.setattr(routes_mod, "buscar_vendedor_autorizado", lambda tel: "Fulano")
    monkeypatch.setattr(routes_mod, "registrar_mensagem", lambda mid, rem: True)
    monkeypatch.setattr(routes_mod, "liberar_mensagem", lambda mid: None)
    monkeypatch.setattr(
        routes_mod,
        "responder",
        lambda pergunta, vendedor_id, canal: RespostaBot(
            texto="pH entre 10 e 13.",
            intencao="propriedades_produto",
            documentos_enviados=["doc-1"],
        ),
    )
    return TestClient(app)


# --- autenticacao do sistema (API key) -------------------------------

class TestApiKey:
    def test_sem_header_recusa(self, client):
        r = client.post("/perguntar", json=PAYLOAD)
        assert r.status_code == 422

    def test_chave_errada_recusa(self, client):
        r = client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": "errada"})
        assert r.status_code == 401

    def test_chave_correta_passa(self, client):
        r = client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})
        assert r.status_code == 200

    def test_servidor_sem_chave_configurada_recusa_tudo(self, client, monkeypatch):
        """
        Ausencia de configuracao deve FECHAR, nunca abrir: sem esta
        barreira, esquecer API_KEY no deploy publicaria a API.
        """
        monkeypatch.setattr(config, "API_KEY", "")
        r = client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": "qualquer"})
        assert r.status_code == 503


# --- autorizacao do usuario (whitelist) ------------------------------

class TestWhitelist:
    def test_remetente_nao_cadastrado_recebe_403(self, client, monkeypatch):
        monkeypatch.setattr(routes_mod, "buscar_vendedor_autorizado", lambda tel: None)
        r = client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})
        assert r.status_code == 403

    def test_nao_autorizado_nunca_chega_ao_motor(self, client, monkeypatch):
        """A barreira barata protege a cara: sem isto, qualquer numero
        que descubra o bot consome cota da OpenAI."""
        monkeypatch.setattr(routes_mod, "buscar_vendedor_autorizado", lambda tel: None)
        motor = MagicMock()
        monkeypatch.setattr(routes_mod, "responder", motor)

        client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})

        motor.assert_not_called()

    def test_chave_invalida_nem_consulta_a_whitelist(self, client, monkeypatch):
        """Ordem das barreiras: autentica o sistema antes de tocar no banco."""
        consulta = MagicMock(return_value="Fulano")
        monkeypatch.setattr(routes_mod, "buscar_vendedor_autorizado", consulta)

        client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": "errada"})

        consulta.assert_not_called()


# --- idempotencia ----------------------------------------------------

class TestDedupe:
    def test_reenvio_responde_duplicada(self, client, monkeypatch):
        monkeypatch.setattr(routes_mod, "registrar_mensagem", lambda mid, rem: False)

        r = client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})

        assert r.status_code == 200
        corpo = r.json()
        assert corpo["duplicada"] is True
        assert corpo["intencao"] == "duplicada"
        assert corpo["resposta"] == ""

    def test_reenvio_nunca_chega_ao_motor(self, client, monkeypatch):
        monkeypatch.setattr(routes_mod, "registrar_mensagem", lambda mid, rem: False)
        motor = MagicMock()
        monkeypatch.setattr(routes_mod, "responder", motor)

        client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})

        motor.assert_not_called()

    def test_mensagem_nova_marca_como_duplicada_false(self, client):
        r = client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})
        assert r.json()["duplicada"] is False


# --- recuperacao de falha do motor -----------------------------------

class TestFalhaDoMotor:
    def test_erro_do_motor_libera_o_message_id(self, client, monkeypatch):
        """
        Sem liberar, o reenvio da Evolution seria descartado como
        duplicata e o vendedor ficaria sem resposta nenhuma.
        """
        monkeypatch.setattr(
            routes_mod,
            "responder",
            lambda pergunta, vendedor_id, canal: RespostaBot(
                texto="Tive um problema tecnico.",
                intencao="erro",
                erro="boom",
            ),
        )
        liberar = MagicMock()
        monkeypatch.setattr(routes_mod, "liberar_mensagem", liberar)

        r = client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})

        assert r.status_code == 200
        liberar.assert_called_once_with(PAYLOAD["message_id"])

    def test_resposta_normal_nao_libera(self, client, monkeypatch):
        liberar = MagicMock()
        monkeypatch.setattr(routes_mod, "liberar_mensagem", liberar)

        client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})

        liberar.assert_not_called()


# --- contrato de entrada e saida -------------------------------------

class TestContrato:
    @pytest.mark.parametrize(
        "campo,valor",
        [
            ("pergunta", ""),                  # min_length=1
            ("pergunta", "x" * 1001),          # max_length=1000
            ("remetente", "551199"),           # min_length=10
            ("remetente", "55a1199999888"),    # pattern: so digitos
            ("remetente", "+5511999998888"),   # E.164 sem "+"
            ("message_id", ""),                # min_length=1
        ],
    )
    def test_payload_invalido_recusado_antes_do_motor(
        self, client, monkeypatch, campo, valor
    ):
        motor = MagicMock()
        monkeypatch.setattr(routes_mod, "responder", motor)

        r = client.post(
            "/perguntar",
            json={**PAYLOAD, campo: valor},
            headers={"X-API-Key": CHAVE},
        )

        assert r.status_code == 422
        motor.assert_not_called()

    def test_campo_extra_no_payload_e_ignorado(self, client):
        """A Evolution pode crescer o webhook; o contrato nao quebra."""
        r = client.post(
            "/perguntar",
            json={**PAYLOAD, "campo_novo_da_evolution": "x"},
            headers={"X-API-Key": CHAVE},
        )
        assert r.status_code == 200

    def test_resposta_carrega_os_campos_que_o_n8n_usa(self, client):
        r = client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})
        corpo = r.json()

        assert corpo["resposta"] == "pH entre 10 e 13."
        assert corpo["intencao"] == "propriedades_produto"
        assert corpo["documentos"] == ["doc-1"]
        assert corpo["precisa_esclarecimento"] is False

    def test_esclarecimento_e_propagado(self, client, monkeypatch):
        """O n8n precisa saber que a conversa espera uma escolha."""
        monkeypatch.setattr(
            routes_mod,
            "responder",
            lambda pergunta, vendedor_id, canal: RespostaBot(
                texto="Encontrei mais de um produto parecido. Qual deles?",
                intencao="fetch_documento",
                precisa_esclarecimento=True,
            ),
        )
        r = client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})
        assert r.json()["precisa_esclarecimento"] is True

    def test_remetente_e_repassado_como_vendedor_id(self, client, monkeypatch):
        """O motor precisa saber de quem e a pergunta (log e contexto)."""
        motor = MagicMock(return_value=RespostaBot(texto="ok", intencao="x"))
        monkeypatch.setattr(routes_mod, "responder", motor)

        client.post("/perguntar", json=PAYLOAD, headers={"X-API-Key": CHAVE})

        _, kwargs = motor.call_args
        assert kwargs["vendedor_id"] == AUTORIZADO
        assert kwargs["canal"] == "whatsapp"


# --- healthcheck ------------------------------------------------------

class TestHealth:
    def test_banco_ok(self, client, monkeypatch):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] in {"ok", "degradado"}

    def test_banco_inacessivel_reporta_degradado(self, client, monkeypatch):
        """
        Healthcheck que so diz "o processo esta vivo" e inutil: o
        orquestrador precisa tirar do ar um servidor que nao alcanca
        o Postgres.
        """
        from src.api import main as main_mod

        def explode():
            raise RuntimeError("banco fora")

        monkeypatch.setattr(main_mod, "get_connection", explode)

        r = client.get("/health")

        assert r.status_code == 200
        assert r.json() == {"status": "degradado", "banco": False}


# --- superficie exposta ----------------------------------------------

class TestSuperficie:
    @pytest.mark.parametrize("rota", ["/docs", "/redoc", "/openapi.json"])
    def test_documentacao_interativa_desabilitada(self, client, rota):
        """A API nao e publica; o schema revela superficie de ataque."""
        assert client.get(rota).status_code == 404
