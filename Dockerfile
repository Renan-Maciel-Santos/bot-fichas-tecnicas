# ============================================================
# Imagem da API do bot de fichas tecnicas.
#
# Build multi-stage: o estagio `builder` compila as dependencias
# (psycopg2 e pymupdf trazem toolchain pesado) e o estagio final
# recebe apenas os pacotes prontos. Reduz o tamanho da imagem e a
# superficie de ataque em producao.
#
# Build:  docker build -t folhabela-api:latest .
# Run:    docker run -p 8000:8000 --env-file .env folhabela-api:latest
# ============================================================

# --- Estagio 1: dependencias ---
FROM python:3.12-slim AS builder

# Toolchain necessario apenas para compilar; nao vai para a imagem final.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .

# --user instala em /root/.local, facil de copiar para o estagio final.
RUN pip install --no-cache-dir --user -r requirements.txt


# --- Estagio 2: runtime ---
FROM python:3.12-slim

# libpq5 e a dependencia de runtime do psycopg2 (sem os headers de dev).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Usuario dedicado: um processo comprometido nao tem privilegio de root.
RUN useradd --create-home --shell /bin/bash --uid 1000 appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local
COPY --chown=appuser:appuser src/ ./src/

USER appuser

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]