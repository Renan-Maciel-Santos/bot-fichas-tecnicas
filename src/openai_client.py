"""
Cliente OpenAI centralizado para o projeto.
Single source of truth para chave, modelos e configuração.
"""
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Modelos definidos uma vez só — usados em todo o projeto
MODEL_CHAT = "gpt-4o-mini"
MODEL_EMBEDDING = "text-embedding-3-small"
EMBEDDING_DIM = 1536  # documentação: precisa bater com vector(1536) da tabela chunks

# Cliente singleton
_client = None

def get_client() -> OpenAI:
    """Retorna uma instância única do cliente OpenAI."""
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY não encontrada. Verifique o arquivo .env."
            )
        _client = OpenAI(api_key=api_key)
    return _client