# Provisionamento do cluster

Scripts de criação de databases e roles. **Não são migrações** — migrações
(`db/migrations/`) versionam o schema do `bot_fichas`; estes scripts
provisionam o cluster e rodam uma vez por ambiente.

## Desenho

Um cluster Postgres, três databases isolados por role:

| Database | Role | Conteúdo |
|---|---|---|
| `bot_fichas` | `bot_app` (aplicação), `botuser` (DDL) | documentos, chunks, produtos, consultas |
| `n8n_bot` | `n8n_user` | workflows, credenciais, execuções |
| `evolution_bot` | `evolution_user` | instâncias, mensagens, contatos |

Nenhuma role tem CONNECT nos databases das outras. `pgvector` existe
apenas no `bot_fichas` (extensão é por database, não por cluster).

## 001a_databases.sql

Cria roles e databases. **Não roda em transação** (`CREATE DATABASE` não
permite). Rollback = `DROP DATABASE` / `DROP ROLE`.

Senhas vêm do `.env` via variáveis `psql` (`-v`), nunca literais no arquivo.

    docker cp db\provisioning\001a_databases.sql bot_fichas_postgres:/tmp/001a_databases.sql

    $env_vars = @{}
    Get-Content .env | Where-Object { $_ -match '^[A-Z]' } | ForEach-Object {
        $parts = $_ -split '=', 2
        $env_vars[$parts[0].Trim()] = $parts[1].Trim()
    }
    docker exec bot_fichas_postgres psql -U botuser -d postgres `
      -v n8n_user=$($env_vars['N8N_DB_USER']) `
      -v n8n_password=$($env_vars['N8N_DB_PASSWORD']) `
      -v n8n_db=$($env_vars['N8N_DB_NAME']) `
      -v evolution_user=$($env_vars['EVOLUTION_DB_USER']) `
      -v evolution_password=$($env_vars['EVOLUTION_DB_PASSWORD']) `
      -v evolution_db=$($env_vars['EVOLUTION_DB_NAME']) `
      -v bot_app_user=$($env_vars['BOT_APP_USER']) `
      -v bot_app_password=$($env_vars['BOT_APP_PASSWORD']) `
      -f /tmp/001a_databases.sql

Executar contra `-d postgres`, não `bot_fichas`.

## Verificação

    docker exec bot_fichas_postgres psql -U botuser -d postgres -c "\l" -c "\du"

Teste de isolamento — o primeiro **deve falhar**, o segundo **deve passar**:

    # esperado: FATAL: permission denied for database "bot_fichas"
    docker exec bot_fichas_postgres psql "postgresql://USER:PASS@localhost:5432/bot_fichas" -c "SELECT 1"

    # esperado: n8n_user | n8n_bot
    docker exec bot_fichas_postgres psql "postgresql://USER:PASS@localhost:5432/n8n_bot" -c "SELECT current_user, current_database()"

## Nota de segurança

As senhas passam como argumento de processo. Aceitável em dev local;
numa VPS multiusuário, `ps` as expõe — usar `PGPASSWORD` via arquivo
de ambiente ou secret manager.