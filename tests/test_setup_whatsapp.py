"""
Testes do script de fiação do canal WhatsApp.

Foco no que pode causar dano: a edição do `.env`, que convive com
senhas de banco e chaves de API que o script não conhece, e a guarda
da instância alvo — apontar o webhook para a instância errada silencia
quem a usava, sem erro nenhum.

Os passos que falam com Docker e com a Evolution não são testados
aqui: dependem de infraestrutura viva e são verificados na execução
com `--aplicar`, que confere o resultado lendo o banco de volta.
"""

import pytest

from scripts.setup_whatsapp import (
    EVOLUTION_URL_DO_HOST,
    INSTANCIA,
    WEBHOOK_DO_N8N,
    Falha,
    atualizar_env,
)

ENV_EXEMPLO = """\
# comentario que deve sobreviver
POSTGRES_PASSWORD=senha-do-banco-nao-tocar
API_KEY=chave-da-api-nao-tocar

EVOLUTION_API_URL=
EVOLUTION_API_KEY=
EVOLUTION_INSTANCE_NAME=bot_fichas
LOG_LEVEL=INFO
"""

TOKEN = "ABC12345-6789-DEF0-1234-56789ABCDEF0"


@pytest.fixture
def env(tmp_path):
    caminho = tmp_path / ".env"
    caminho.write_text(ENV_EXEMPLO, encoding="utf-8")
    return caminho


def linhas(caminho) -> dict[str, str]:
    saida = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if "=" in linha and not linha.startswith("#"):
            chave, valor = linha.split("=", 1)
            saida[chave] = valor
    return saida


class TestAtualizarEnv:
    def test_grava_token_e_url(self, env):
        alteradas = atualizar_env(TOKEN, caminho=env)

        valores = linhas(env)
        assert valores["EVOLUTION_API_KEY"] == TOKEN
        assert valores["EVOLUTION_API_URL"] == EVOLUTION_URL_DO_HOST
        assert set(alteradas) == {"EVOLUTION_API_KEY", "EVOLUTION_API_URL"}

    def test_nao_toca_no_resto_do_arquivo(self, env):
        """O .env carrega senha de banco e chave da API — nada aqui as conhece."""
        atualizar_env(TOKEN, caminho=env)

        valores = linhas(env)
        assert valores["POSTGRES_PASSWORD"] == "senha-do-banco-nao-tocar"
        assert valores["API_KEY"] == "chave-da-api-nao-tocar"
        assert valores["LOG_LEVEL"] == "INFO"
        assert "# comentario que deve sobreviver" in env.read_text(encoding="utf-8")

    def test_e_idempotente(self, env):
        """Rodar de novo reconverge em vez de reescrever."""
        atualizar_env(TOKEN, caminho=env)
        assert atualizar_env(TOKEN, caminho=env) == []

    def test_token_com_barra_invertida_nao_vira_referencia_de_grupo(self, env):
        r"""
        re.sub interpreta \g e \1 na string de substituicao. Um token
        com esses caracteres seria corrompido silenciosamente — e a
        falha so apareceria como 401 da Evolution, longe da causa.
        """
        token = r"abc\g<0>def\1ghi"
        atualizar_env(token, caminho=env)

        assert linhas(env)["EVOLUTION_API_KEY"] == token

    def test_variavel_ausente_falha_em_vez_de_inventar(self, tmp_path):
        """.env fora de sincronia com o .env.example precisa aparecer."""
        incompleto = tmp_path / ".env"
        incompleto.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")

        with pytest.raises(Falha, match="EVOLUTION_API_KEY"):
            atualizar_env(TOKEN, caminho=incompleto)

    def test_arquivo_intacto_quando_falha(self, tmp_path):
        """Falha no meio nao pode deixar o .env pela metade."""
        incompleto = tmp_path / ".env"
        original = "EVOLUTION_API_KEY=antigo\nLOG_LEVEL=INFO\n"
        incompleto.write_text(original, encoding="utf-8")

        with pytest.raises(Falha):
            atualizar_env(TOKEN, caminho=incompleto)

        assert incompleto.read_text(encoding="utf-8") == original


class TestGuardaDaInstancia:
    def test_alvo_e_a_instancia_do_bot(self):
        """
        A Evolution v2 aceita um webhook por instância e sobrescreve
        sem avisar. Apontar a errada não levanta erro — só faz as
        mensagens dela pararem de chegar em algum lugar.
        """
        assert INSTANCIA == "bot_fichas"

    def test_webhook_aponta_para_o_caminho_do_fluxo(self):
        assert WEBHOOK_DO_N8N.endswith("/webhook/bot-fichas")

    def test_webhook_usa_nome_de_servico_do_compose(self):
        """
        Quem resolve esta URL é o container da Evolution: `localhost`
        ali seria ela mesma. Na mesma rede do compose vale o nome do
        serviço.
        """
        assert WEBHOOK_DO_N8N.startswith("http://n8n:5678/")

    def test_url_do_host_usa_a_porta_publicada(self):
        """A Evolution do bot publica 8081, não a 8080 padrão."""
        assert EVOLUTION_URL_DO_HOST == "http://localhost:8081"
