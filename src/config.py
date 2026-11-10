"""
Configuração centralizada do projeto.
Carrega variáveis de ambiente do .env e expõe valores tipados.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Carrega .env a partir da raiz do projeto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# --- Postgres ---
POSTGRES_DB       = os.getenv("POSTGRES_DB", "bot_fichas")
POSTGRES_HOST     = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT     = int(os.getenv("POSTGRES_PORT", "5433"))

# Perfil de manutencao: DDL, migracoes, ingestao, chunking, extracao.
# Superuser. Usado apenas por scripts, nunca em runtime da aplicacao.
POSTGRES_USER     = os.getenv("POSTGRES_USER", "botuser")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

# Perfil de aplicacao: leitura do acervo + insert em `consultas`.
# Sem DDL, sem DELETE. Usado pelo motor de resposta e pela API HTTP.
BOT_APP_USER      = os.getenv("BOT_APP_USER", "bot_app")
BOT_APP_PASSWORD  = os.getenv("BOT_APP_PASSWORD", "")


def _build_database_url(user: str, password: str) -> str:
    """Monta a URL de conexao para um perfil de acesso."""
    return (
        f"postgresql://{user}:{password}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )


# URL padrao da aplicacao (least privilege).
DATABASE_URL = _build_database_url(BOT_APP_USER, BOT_APP_PASSWORD)

# URL administrativa. Importar explicitamente apenas em scripts de
# manutencao — o nome sinaliza a intencao em revisao de codigo.
DATABASE_URL_ADMIN = _build_database_url(POSTGRES_USER, POSTGRES_PASSWORD)

# --- APIs ---
# Todo o motor usa OpenAI: embeddings, classificacao de intencao,
# extracao estruturada e redacao (ver src/openai_client.py). A chave da
# Anthropic vivia aqui desde o plano original (Claude Haiku), mas nunca
# houve chamada a ela — mantê-la sugeria uma dependencia inexistente.
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")

# Prazo, em segundos, de cada chamada a OpenAI. Vazio = padrao do SDK
# (10 min), adequado aos scripts de lote. O container da API define 30
# (docker-compose.yml): o n8n desiste da pergunta em 120s, e sem prazo
# uma chamada pendurada segurava a thread da requisicao por 10 minutos.
_openai_timeout = os.getenv("OPENAI_TIMEOUT_SEGUNDOS", "").strip()
OPENAI_TIMEOUT_SEGUNDOS = float(_openai_timeout) if _openai_timeout else None

# --- Google Drive ---
GOOGLE_DRIVE_FOLDER_ID       = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON  = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    str(PROJECT_ROOT / "credentials" / "google-service-account.json"),
)

# --- WhatsApp ---
EVOLUTION_API_URL       = os.getenv("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY       = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "bot_fichas")

# --- Aplicação ---
LOG_LEVEL              = os.getenv("LOG_LEVEL", "INFO")
# Chave compartilhada com o n8n para autenticar chamadas a API HTTP.
# Sem valor padrao: ausencia de configuracao deve falhar, nao liberar.
API_KEY = os.getenv("API_KEY", "")

# Validade da pendencia de desambiguacao de produto (ver
# src/query/contexto.py). Curto de proposito: um "2" que chega muito
# depois quase certamente nao se refere mais aquela lista.
CONTEXTO_TTL_MINUTOS = int(os.getenv("CONTEXTO_TTL_MINUTOS", "15"))

# Embeddings (OpenAI)
EMBEDDING_MODEL        = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSION    = int(os.getenv("EMBEDDING_DIMENSION", "1536"))

# Modelos de chat/extração (OpenAI)
LLM_MODEL_EXTRACTION   = os.getenv("LLM_MODEL_EXTRACTION", "gpt-4o-mini")
LLM_MODEL_RESPONSE     = os.getenv("LLM_MODEL_RESPONSE", "gpt-4o-mini")

# Parâmetros de chunking (Fase 2) — valores em TOKENS
CHUNK_SIZE        = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP     = int(os.getenv("CHUNK_OVERLAP", "100"))
MIN_CHUNK_SIZE    = int(os.getenv("MIN_CHUNK_SIZE", "100"))

# --- Classificação de tipo de documento (FT/FDS/LAUDO) ---
# Confianca minima do classificador para gravar `documentos.tipo`.
# Abaixo disso, `tipo` fica NULL e o documento cai na v_pendentes_revisao.
# Usada tanto pelo pipeline de ingestao quanto pelo script de backfill.
CONFIANCA_MINIMA_TIPO = float(os.getenv("CONFIANCA_MINIMA_TIPO", "0.7"))

# Conjunto de heuristicas de dominio ativado na ingestao automatica.
# Valores: "reseller_a" (padrao) ou "none" para core puro.
# Ver src/heuristics_registry.py para os conjuntos disponiveis.
INGEST_HEURISTICAS = os.getenv("INGEST_HEURISTICAS", "reseller_a")

# --- Caminhos úteis ---
DATA_DIR    = PROJECT_ROOT / "data"
SAMPLES_DIR = DATA_DIR / "samples"