#!/usr/bin/env bash
# =====================================================================
# Exporta o acervo do bot (bot_fichas) para a carga inicial da VPS.
#
# Roda na maquina que tem os dados hoje (Git Bash no Windows serve).
# Leva schema e acervo: produtos, documentos com o texto extraido,
# chunks com embeddings, propriedades e os vinculos, inclusive os
# revisados pelo cliente -- nada disso se reconstroi de graca.
#
# Deixa de fora os DADOS de uso, que sao do ambiente e nao do acervo:
#   consultas, mensagens_processadas, contexto_conversa  (historico local)
#   vendedores_autorizados                               (cadastro e por ambiente)
# As tabelas vao vazias; o schema delas vai inteiro.
#
# Uso (na raiz do repositorio):
#     deploy/exportar_dados.sh
# Saida: backups/bot_fichas_carga_AAAAMMDD_HHMM.dump
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
# Git Bash (Windows) reescreve "/tmp/..." como caminho do Windows antes
# de passar ao docker. No Linux esta variavel nao tem efeito.
export MSYS_NO_PATHCONV=1

# tr: um .env editado no Windows traz \r no fim dos valores.
set -a; source <(tr -d '\r' < .env); set +a

mkdir -p backups
arquivo="bot_fichas_carga_$(date +%Y%m%d_%H%M).dump"

# Gera DENTRO do container e copia depois: redirecionar o stdout binario
# do `docker exec` no Windows arrisca corromper o arquivo.
docker compose exec -T postgres pg_dump \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --format=custom \
    --exclude-table-data=consultas \
    --exclude-table-data=mensagens_processadas \
    --exclude-table-data=contexto_conversa \
    --exclude-table-data=vendedores_autorizados \
    --file="/tmp/$arquivo"
docker compose cp "postgres:/tmp/$arquivo" "backups/$arquivo"
docker compose exec -T postgres rm -f "/tmp/$arquivo"

echo "Gerado: backups/$arquivo ($(du -h "backups/$arquivo" | cut -f1))"
echo
echo "Contem dados do cliente: nao versione, nao mande por canal aberto."
echo "Para a VPS:"
echo "  scp backups/$arquivo USUARIO@VPS:~/projeto-bot/backups/"
