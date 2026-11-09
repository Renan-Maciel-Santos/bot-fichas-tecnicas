"""
Helpers compartilhados pelos testes do motor de resposta.

Vivem aqui, e nao em conftest.py, porque sao funcoes chamadas
explicitamente pelos testes (nao fixtures injetadas pelo pytest) e
porque `_NoopContext` e uma classe que os testes instanciam.
"""

from unittest.mock import MagicMock

from src.query import responder as responder_mod
from src.query.classifier import Classificacao


def fake_classificacao(**kwargs) -> Classificacao:
    """Retorna uma Classificacao com defaults sensatos."""
    defaults = {
        "intencao": "pergunta_aberta",
        "produto_termo": None,
        "campo": None,
        "valor_busca": None,
        "tipo_documento": None,
        "fallback": False,
    }
    defaults.update(kwargs)
    return Classificacao(**defaults)


def mockar_classificador(monkeypatch, cls: Classificacao) -> None:
    """Substitui classificar() para devolver uma classificacao fixa."""
    monkeypatch.setattr(responder_mod, "classificar", lambda pergunta: cls)


def mockar_openai_client(monkeypatch, resposta_llm: str) -> MagicMock:
    """
    Substitui get_client() por um mock que devolve a resposta_llm dada
    quando chat.completions.create() e chamado. Retorna o mock para o
    teste poder inspecionar as chamadas se quiser.
    """
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=resposta_llm))]
    )
    monkeypatch.setattr(responder_mod, "get_client", lambda: mock_client)
    return mock_client


class NoopContext:
    """
    Wrapper que faz get_connection() reutilizar a conexao da fixture
    sem fechar ou commitar. Necessario porque o responder abre
    get_connection() dentro de _log_consulta e teria uma conexao
    propria, invisivel ao teste.
    """

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *args):
        # NAO fecha e NAO commita — a fixture cuida.
        return False


def mockar_log_consulta(monkeypatch, db_transaction) -> None:
    """Faz o log em `consultas` cair na transacao do teste."""
    monkeypatch.setattr(
        responder_mod, "get_connection", lambda: NoopContext(db_transaction)
    )
