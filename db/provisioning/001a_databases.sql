-- ============================================================
-- 001a_databases.sql
-- Provisionamento de databases e roles dedicados por sistema.
--
-- Contexto: o cluster nasceu com um unico database (bot_fichas) e um
-- unico usuario superuser (botuser), compartilhado entre a aplicacao
-- e o n8n. Este script separa cada sistema em seu proprio database,
-- com role dedicada e sem privilegio cruzado.
--
-- ATENCAO: CREATE DATABASE nao pode rodar dentro de transacao.
-- Este script NAO usa BEGIN/ROLLBACK. Rollback = DROP DATABASE
-- (seguro: os databases nascem vazios).
-- ============================================================

-- --- n8n: workflows, credenciais, execucoes ---
CREATE ROLE :"n8n_user" LOGIN PASSWORD :'n8n_password';
CREATE DATABASE :"n8n_db" OWNER :"n8n_user";

-- --- Evolution API: instancias, mensagens, contatos ---
CREATE ROLE :"evolution_user" LOGIN PASSWORD :'evolution_password';
CREATE DATABASE :"evolution_db" OWNER :"evolution_user";

-- --- Role de aplicacao do bot (sem superuser, sem DDL) ---
-- botuser continua existindo para migracoes e DDL.
-- A aplicacao Python passa a conectar como bot_app.
CREATE ROLE :"bot_app_user" LOGIN PASSWORD :'bot_app_password';

-- --- Fecha o padrao permissivo do Postgres ---
-- Por padrao, PUBLIC pode conectar em qualquer database. Sem este
-- REVOKE, n8n_user e evolution_user conectariam em bot_fichas mesmo
-- sem serem donos.
REVOKE CONNECT ON DATABASE bot_fichas FROM PUBLIC;
REVOKE CONNECT ON DATABASE :"n8n_db" FROM PUBLIC;
REVOKE CONNECT ON DATABASE :"evolution_db" FROM PUBLIC;

-- Reconcede apenas a quem precisa.
GRANT CONNECT ON DATABASE bot_fichas TO :"bot_app_user";
GRANT CONNECT ON DATABASE :"n8n_db" TO :"n8n_user";
GRANT CONNECT ON DATABASE :"evolution_db" TO :"evolution_user";