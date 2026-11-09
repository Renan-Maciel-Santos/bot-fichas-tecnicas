#!/usr/bin/env bash
# =====================================================================
# Faz uma pergunta ao bot pela API, como o n8n faria vindo do WhatsApp.
#
# A requisicao parte de dentro do container da API (que ja tem Python e
# a API_KEY no ambiente): na maquina so precisa de Docker.
#
# Uso (na raiz do repositorio, depois do demo/carregar_demo.sh):
#     demo/perguntar.sh "qual o pH da agua sanitaria limpax?"
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

[[ $# -ge 1 ]] || { echo 'uso: demo/perguntar.sh "sua pergunta"'; exit 1; }

docker compose exec -T -e PERGUNTA="$1" api python - <<'PY'
import json
import os
import urllib.request
import uuid

corpo = json.dumps({
    "pergunta": os.environ["PERGUNTA"],
    "remetente": "5500000000001",           # vendedor da demo (whitelist)
    "message_id": f"demo-{uuid.uuid4()}",   # a API descarta id repetido
}).encode()
pedido = urllib.request.Request(
    "http://localhost:8000/perguntar",
    data=corpo,
    headers={"Content-Type": "application/json", "X-API-Key": os.environ["API_KEY"]},
)
with urllib.request.urlopen(pedido, timeout=90) as resposta:
    r = json.load(resposta)

print(r["resposta"])
print(f"\n[intencao: {r['intencao']} | documentos: {len(r.get('documentos') or [])}]")
PY
