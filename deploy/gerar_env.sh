#!/usr/bin/env bash
# =====================================================================
# Cria o .env da VPS a partir do .env.example, com segredos aleatorios.
#
# Preenche toda senha/chave que no exemplo esta vazia ou com o valor de
# exemplo. NAO preenche o que so voce tem: OPENAI_API_KEY (e as do Google
# Drive, que so a ingestao usa). EVOLUTION_API_KEY fica vazia: e o
# setup_whatsapp que a le da Evolution depois do pareamento.
#
# Se o .env ja existe, nao faz nada -- regerar N8N_ENCRYPTION_KEY
# inutiliza as credenciais gravadas no n8n, e regerar as senhas do banco
# separa o .env das roles ja criadas.
#
# Uso (na raiz do repositorio):
#     deploy/gerar_env.sh
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
    echo "O .env ja existe; nada foi alterado."
    echo "Para recomecar do zero (perde as senhas atuais): rm .env && deploy/gerar_env.sh"
    exit 1
fi

command -v openssl >/dev/null || { echo "ERRO: openssl nao encontrado."; exit 1; }

GERADAS=(
    POSTGRES_PASSWORD
    BOT_APP_PASSWORD
    N8N_DB_PASSWORD
    N8N_ENCRYPTION_KEY
    API_KEY
    EVOLUTION_GLOBAL_KEY
    EVOLUTION_DB_PASSWORD
)

cp .env.example .env
chmod 600 .env

for var in "${GERADAS[@]}"; do
    valor=$(openssl rand -hex 32)
    # Troca so a linha da variavel; hex nao tem caractere especial para o sed.
    sed -i -E "s|^${var}=.*$|${var}=${valor}|" .env
done

echo ".env criado com ${#GERADAS[@]} segredos gerados (permissao 600)."
echo
echo "Falta preencher a mao:"
echo "  OPENAI_API_KEY   -- obrigatoria: o motor de resposta usa OpenAI"
echo
echo "Guarde uma copia do .env no cofre de senhas. Sem ele:"
echo "  - N8N_ENCRYPTION_KEY: credenciais do n8n viram lixo"
echo "  - senhas do banco: os backups nao restauram com as roles certas"
