#!/usr/bin/env bash
# =====================================================================
# Backup dos tres databases do bot, para rodar pelo cron na VPS.
#
#   bot_fichas     acervo + historico de uso (consultas, vendedores)
#   n8n_bot        fluxo e credenciais (cifradas com N8N_ENCRYPTION_KEY)
#   evolution_bot  instancia do WhatsApp
#
# Fica de fora o volume da sessao do WhatsApp (evolution_instances):
# perde-lo so obriga a ler o QR Code de novo.
#
# Restaurar exige o MESMO .env (senhas das roles e N8N_ENCRYPTION_KEY):
# guarde uma copia dele fora da VPS.
#
# Uso:
#     deploy/backup.sh                 # grava em backups/, guarda 14 dias
#     RETENCAO_DIAS=30 deploy/backup.sh
# Cron (todo dia as 03:15):
#     15 3 * * * cd /home/USUARIO/projeto-bot && deploy/backup.sh >> backups/backup.log 2>&1
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
# Git Bash (Windows) reescreve "/tmp/..." como caminho do Windows antes
# de passar ao docker. No Linux esta variavel nao tem efeito.
export MSYS_NO_PATHCONV=1

# tr: um .env editado no Windows traz \r no fim dos valores.
set -a; source <(tr -d '\r' < .env); set +a
RETENCAO_DIAS="${RETENCAO_DIAS:-14}"
carimbo=$(date +%Y%m%d_%H%M)
mkdir -p backups

for db in "$POSTGRES_DB" "$N8N_DB_NAME" "$EVOLUTION_DB_NAME"; do
    arquivo="backups/${db}_${carimbo}.dump"
    docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$db" \
        --format=custom --file="/tmp/backup.dump"
    docker compose cp postgres:/tmp/backup.dump "$arquivo"
    docker compose exec -T postgres rm -f /tmp/backup.dump
    echo "$(date '+%F %T') $arquivo $(du -h "$arquivo" | cut -f1)"
done

# So apaga o que este script gerou (nome com carimbo), nunca a carga inicial.
find backups -maxdepth 1 -type f -name '*_[0-9]*_[0-9]*.dump' \
    ! -name 'bot_fichas_carga_*' -mtime "+$RETENCAO_DIAS" -print -delete
