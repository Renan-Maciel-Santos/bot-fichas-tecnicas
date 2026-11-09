#!/usr/bin/env bash
# =====================================================================
# Instala o bot numa maquina com Docker: sobe o Postgres, provisiona
# databases e roles, carrega o acervo e sobe os demais servicos.
#
# Idempotente: cada etapa confere o estado antes de agir. Rodar de novo
# depois de uma falha retoma de onde parou; rodar numa instalacao pronta
# so reconstroi a imagem da API e confere a saude.
#
# Uso (na raiz do repositorio, com o .env preenchido):
#     deploy/instalar.sh backups/bot_fichas_carga_AAAAMMDD_HHMM.dump
#     deploy/instalar.sh            # sem dump: schema vazio (migracoes)
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
# Git Bash (Windows) reescreve "/tmp/..." como caminho do Windows antes
# de passar ao docker. No Linux esta variavel nao tem efeito.
export MSYS_NO_PATHCONV=1

DUMP="${1:-}"

passo() { printf '\n==> %s\n' "$*"; }
falha() { printf '\nERRO: %s\n' "$*" >&2; exit 1; }

# --- 0. Pre-requisitos ------------------------------------------------
passo "Conferindo pre-requisitos"

docker compose version >/dev/null 2>&1 || falha "docker compose nao encontrado."
[[ -f .env ]] || falha ".env nao existe. Rode deploy/gerar_env.sh e preencha OPENAI_API_KEY."
[[ -z "$DUMP" || -f "$DUMP" ]] || falha "dump nao encontrado: $DUMP"

# tr: um .env editado no Windows traz \r no fim dos valores.
set -a; source <(tr -d '\r' < .env); set +a

OBRIGATORIAS=(
    POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
    BOT_APP_USER BOT_APP_PASSWORD
    N8N_DB_NAME N8N_DB_USER N8N_DB_PASSWORD N8N_ENCRYPTION_KEY
    EVOLUTION_DB_NAME EVOLUTION_DB_USER EVOLUTION_DB_PASSWORD
    EVOLUTION_GLOBAL_KEY EVOLUTION_PORT
    API_KEY OPENAI_API_KEY
)
for var in "${OBRIGATORIAS[@]}"; do
    valor="${!var:-}"
    [[ -n "$valor" ]] || falha "$var esta vazia no .env."
    [[ "$valor" != "troque_essa_senha_local" ]] || falha "$var ainda tem o valor de exemplo."
done
echo "ok"

psql_admin() {
    # Senhas vao por stdin (\set), nunca como argumento de processo.
    docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" "$@"
}

# --- 1. Postgres ------------------------------------------------------
passo "Subindo o Postgres"
docker compose up -d postgres
for _ in $(seq 1 60); do
    docker compose exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1 && break
    sleep 2
done
docker compose exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null \
    || falha "o Postgres nao ficou pronto. Veja: docker compose logs postgres"
echo "ok"

# --- 2. Databases e roles (db/provisioning/001a) ----------------------
passo "Provisionando databases e roles"
existentes=$(psql_admin -d postgres -tA -c \
    "select count(*) from pg_roles where rolname in ('$N8N_DB_USER', '$EVOLUTION_DB_USER', '$BOT_APP_USER')")
case "$existentes" in
    3) echo "ja provisionado" ;;
    0)
        {
            printf "\\set n8n_user '%s'\n" "$N8N_DB_USER"
            printf "\\set n8n_password '%s'\n" "$N8N_DB_PASSWORD"
            printf "\\set n8n_db '%s'\n" "$N8N_DB_NAME"
            printf "\\set evolution_user '%s'\n" "$EVOLUTION_DB_USER"
            printf "\\set evolution_password '%s'\n" "$EVOLUTION_DB_PASSWORD"
            printf "\\set evolution_db '%s'\n" "$EVOLUTION_DB_NAME"
            printf "\\set bot_app_user '%s'\n" "$BOT_APP_USER"
            printf "\\set bot_app_password '%s'\n" "$BOT_APP_PASSWORD"
            cat db/provisioning/001a_databases.sql
        } | psql_admin -d postgres
        echo "ok"
        ;;
    *) falha "provisionamento pela metade ($existentes de 3 roles). Confira com: docker compose exec postgres psql -U $POSTGRES_USER -d postgres -c '\\du'" ;;
esac

# --- 3. Acervo --------------------------------------------------------
passo "Carregando o acervo"
tem_schema=$(psql_admin -d "$POSTGRES_DB" -tA -c "select to_regclass('public.produtos') is not null")
if [[ "$tem_schema" == "t" ]]; then
    echo "o banco ja tem schema; carga pulada"
elif [[ -n "$DUMP" ]]; then
    docker compose cp "$DUMP" postgres:/tmp/carga.dump
    # As extensoes ja foram criadas pelo db/init na primeira subida; o
    # dump traz CREATE EXTENSION de novo, que falharia. Tira da lista.
    docker compose exec -T postgres sh -c "
        set -e
        pg_restore -l /tmp/carga.dump | grep -v ' EXTENSION ' > /tmp/carga.list
        pg_restore -U '$POSTGRES_USER' -d '$POSTGRES_DB' --no-owner --role='$POSTGRES_USER' \
            --exit-on-error -L /tmp/carga.list /tmp/carga.dump
        rm -f /tmp/carga.dump /tmp/carga.list
    "
    echo "ok"
else
    echo "sem dump: aplicando migracoes (o acervo fica vazio)"
    for arquivo in db/migrations/*.sql; do
        echo "  $arquivo"
        psql_admin -d "$POSTGRES_DB" < "$arquivo"
    done
    for arquivo in db/provisioning/001[b-e]_*.sql; do
        echo "  $arquivo"
        { printf "\\set bot_app_user '%s'\n" "$BOT_APP_USER"; cat "$arquivo"; } | psql_admin -d "$POSTGRES_DB"
    done
fi

# --- 4. Demais servicos ----------------------------------------------
passo "Subindo API, Evolution e n8n"
docker compose up -d --build

passo "Esperando a API ficar saudavel"
id_api=$(docker compose ps -q api)
for _ in $(seq 1 45); do
    estado=$(docker inspect -f '{{.State.Health.Status}}' "$id_api" 2>/dev/null || echo "?")
    [[ "$estado" == "healthy" ]] && break
    sleep 2
done
[[ "$estado" == "healthy" ]] || falha "a API nao ficou saudavel ($estado). Veja: docker compose logs api"
echo "ok"

passo "Resumo"
psql_admin -d "$POSTGRES_DB" -tA -F' ' -c "
    select 'produtos:', count(*) from produtos
    union all select 'documentos:', count(*) from documentos
    union all select 'vinculos:', count(*) from produto_documento
    union all select 'vendedores autorizados:', count(*) from vendedores_autorizados where ativo"
docker compose ps --format 'table {{.Name}}\t{{.Status}}'

cat <<'FIM'

Instalacao concluida. Proximos passos (docs/DEPLOY_VPS.md, a partir do passo 6):
  - tunel SSH para abrir n8n (5678) e Evolution (8081) no seu navegador
  - criar a conta de owner do n8n
  - criar a instancia bot_fichas na Evolution e ler o QR Code
  - python3 -m scripts.setup_whatsapp --aplicar
  - cadastrar os vendedores autorizados
FIM
