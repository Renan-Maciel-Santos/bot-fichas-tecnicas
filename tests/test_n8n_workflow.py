"""
Validacao estrutural do fluxo do n8n.

O fluxo e um JSON escrito a mao e editado pela interface do n8n, onde
as ligacoes entre nos sao feitas por NOME. Renomear um no na interface
quebra silenciosamente qualquer expressao que o referencie por
`$('Nome')` — o fluxo importa, ativa e so falha em producao, com o
vendedor esperando.

Estes testes nao exercitam a logica JavaScript do no de codigo (isso e
`n8n/workflows/testa_extracao.mjs`, que roda no container do n8n).
Aqui verificamos o que quebra por edicao descuidada: referencias,
conexoes, alcancabilidade e as barreiras que nao podem sumir.
"""

import json
from pathlib import Path

import pytest

FLUXO = Path(__file__).resolve().parent.parent / "n8n" / "workflows" / (
    "bot_fichas_whatsapp.json"
)

NO_WEBHOOK = "Webhook Evolution"
NO_EXTRACAO = "Extrai a mensagem"


@pytest.fixture(scope="module")
def fluxo() -> dict:
    return json.loads(FLUXO.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def nomes(fluxo) -> set[str]:
    return {no["name"] for no in fluxo["nodes"]}


@pytest.fixture(scope="module")
def bruto() -> str:
    return FLUXO.read_text(encoding="utf-8")


class TestIntegridade:
    def test_ids_de_no_sao_unicos(self, fluxo):
        ids = [no["id"] for no in fluxo["nodes"]]
        assert len(ids) == len(set(ids))

    def test_toda_conexao_liga_nos_existentes(self, fluxo, nomes):
        for origem, saidas in fluxo["connections"].items():
            assert origem in nomes, f"conexao parte de no inexistente: {origem}"
            for grupo in saidas.get("main", []):
                for ligacao in grupo:
                    assert ligacao["node"] in nomes, (
                        f"{origem} aponta para no inexistente: {ligacao['node']}"
                    )

    def test_referencias_em_expressoes_apontam_para_nos_existentes(
        self, bruto, nomes
    ):
        """
        `$('Configuracao')` e `$('Extrai a mensagem')` sao resolvidos
        por nome em tempo de execucao. Um nome errado nao impede
        importar nem ativar o fluxo.
        """
        import re

        referencias = set(re.findall(r"\$\(\\?'([^')]+)\\?'\)", bruto))
        assert referencias, "esperava ao menos uma referencia entre nos"
        for ref in referencias:
            assert ref in nomes, f"expressao referencia no inexistente: {ref}"

    def test_todo_no_e_alcancavel_a_partir_do_webhook(self, fluxo, nomes):
        """No orfao e trabalho que nunca roda — quase sempre erro de edicao."""
        alcancados: set[str] = set()
        fila = [NO_WEBHOOK]
        while fila:
            atual = fila.pop()
            if atual in alcancados:
                continue
            alcancados.add(atual)
            for grupo in fluxo["connections"].get(atual, {}).get("main", []):
                fila.extend(ligacao["node"] for ligacao in grupo)

        assert nomes - alcancados == set()


class TestBarreiras:
    """
    Os cortes que impedem o fluxo de se autoalimentar ou de gastar
    chamada paga a toa. Sao uma linha cada e faceis de remover sem
    perceber.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def codigo(fluxo) -> str:
        no = next(n for n in fluxo["nodes"] if n["name"] == NO_EXTRACAO)
        return no["parameters"]["jsCode"]

    def test_corta_eco_do_proprio_bot(self, codigo):
        """Sem isto o bot responde a propria resposta, em loop."""
        assert "fromMe" in codigo

    def test_corta_grupos(self, codigo):
        assert "@g.us" in codigo

    def test_trunca_no_limite_da_api(self, codigo):
        """PerguntaRequest.pergunta tem max_length=1000."""
        assert "slice(0, 1000)" in codigo

    def test_respeita_o_limite_do_message_id(self, codigo):
        """PerguntaRequest.message_id tem max_length=100."""
        assert "slice(0, 100)" in codigo


class TestChamadaAoMotor:
    @staticmethod
    @pytest.fixture(scope="class")
    def no(fluxo) -> dict:
        return next(n for n in fluxo["nodes"] if n["name"] == "Pergunta ao motor")

    def test_usa_nomes_de_servico_do_compose(self, fluxo):
        """
        Entre containers, localhost e o proprio container. API e
        Evolution dividem a rede do compose com o n8n, entao valem os
        nomes de servico e as portas INTERNAS (a Evolution publica
        8081 no host, mas atende 8080 na rede).
        """
        cfg = next(n for n in fluxo["nodes"] if n["name"] == "Configuracao")
        valores = {
            a["name"]: a["value"]
            for a in cfg["parameters"]["assignments"]["assignments"]
        }
        assert valores["api_url"] == "http://api:8000/perguntar"
        assert valores["evolution_url"] == "http://evolution:8080"

    def test_envia_os_tres_campos_do_contrato(self, no):
        corpo = no["parameters"]["jsonBody"]
        for campo in ("pergunta", "remetente", "message_id"):
            assert campo in corpo

    def test_autentica_com_header_auth(self, no):
        assert no["parameters"]["genericAuthType"] == "httpHeaderAuth"
        assert "httpHeaderAuth" in no["credentials"]

    def test_nao_trata_4xx_como_falha_de_execucao(self, no):
        """
        403 (fora da whitelist) e resposta valida do fluxo: precisa
        chegar ao IF seguinte, nao abortar a execucao.
        """
        resposta = no["parameters"]["options"]["response"]["response"]
        assert resposta["neverError"] is True
        assert resposta["fullResponse"] is True, (
            "sem fullResponse nao da para ler o statusCode"
        )

    def test_timeout_cobre_pergunta_aberta(self, no):
        """Pergunta aberta faz embeddings + LLM; o padrao de 300ms nao serve."""
        assert no["parameters"]["options"]["timeout"] >= 60000


class TestEnvioDaResposta:
    @staticmethod
    @pytest.fixture(scope="class")
    def condicoes(fluxo) -> list[dict]:
        no = next(
            n for n in fluxo["nodes"] if n["name"] == "Tem resposta para enviar?"
        )
        return no["parameters"]["conditions"]["conditions"]

    def test_so_envia_com_status_200(self, condicoes):
        assert any("statusCode" in c["leftValue"] for c in condicoes)

    def test_nao_envia_quando_duplicada(self, condicoes):
        """
        duplicada=true significa que a primeira copia do webhook ja foi
        respondida. Reenviar e exatamente o que o dedupe evita.
        """
        duplicada = next(c for c in condicoes if "duplicada" in c["leftValue"])
        assert duplicada["operator"]["operation"] == "false"

    def test_nao_envia_resposta_vazia(self, condicoes):
        vazia = next(c for c in condicoes if "resposta" in c["leftValue"])
        assert vazia["operator"]["operation"] == "notEmpty"

    def test_responde_para_o_remetente_original(self, fluxo):
        no = next(
            n for n in fluxo["nodes"] if n["name"] == "Envia resposta no WhatsApp"
        )
        corpo = no["parameters"]["jsonBody"]
        assert f"$('{NO_EXTRACAO}')" in corpo
        assert "remetente" in corpo
