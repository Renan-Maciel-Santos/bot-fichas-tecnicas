-- ============================================================
-- 001c_bot_app_grants_controle_acesso.sql
-- Privilegios de escrita da aplicacao nas tabelas de controle de
-- acesso, introduzidas pela migracao 006.
--
-- SELECT ja foi concedido automaticamente pelo ALTER DEFAULT
-- PRIVILEGES do 001b. Aqui apenas o INSERT em mensagens_processadas,
-- que a API precisa para o dedupe de webhooks.
--
-- vendedores_autorizados permanece somente-leitura para a aplicacao:
-- cadastrar vendedor e operacao administrativa, feita como botuser.
-- ============================================================

GRANT INSERT ON TABLE mensagens_processadas TO :"bot_app_user";