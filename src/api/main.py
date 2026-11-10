"""
Aplicacao FastAPI do bot de fichas tecnicas.

Ponto de entrada do processo servidor. Monta as rotas, configura
logging e expoe healthcheck para o orquestrador.

Execucao local:
    uvicorn src.api.main:app --reload --port 8000
"""
import logging

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from src import config
from src.api.routes import router
from src.db import get_connection

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger(__name__)

app = FastAPI(
    title="Bot Fichas Tecnicas",
    description="API de consulta a documentacao tecnica de produtos.",
    version="0.1.0",
    docs_url=None,      # documentacao interativa desabilitada:
    redoc_url=None,     # a API nao e publica, e o schema revela superficie
    openapi_url=None,
)

app.include_router(router)


@app.get("/health", summary="Verifica se o servico esta operacional")
def health():
    """
    Healthcheck para o Docker e para o Traefik.

    Verifica a dependencia critica (banco), nao apenas se o processo
    esta vivo: um servidor que responde mas nao alcanca o Postgres
    esta indisponivel na pratica.

    Banco fora responde 503, nao 200: o HEALTHCHECK do Dockerfile usa
    `curl -f`, que so enxerga o status HTTP. Com 200 o container ficava
    "healthy" sem alcancar o Postgres.

    `def`, nao `async def`: psycopg2 e bloqueante. Numa corrotina ele
    travaria o event loop; como funcao comum o FastAPI a roda em thread.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception:
        log.exception("Healthcheck falhou: banco inacessivel")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degradado", "banco": False},
        )

    return {"status": "ok", "banco": True}