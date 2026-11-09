-- ============================================================
-- 001e_bot_app_grants_dedupe.sql
-- Permite a aplicacao DESFAZER o dedupe de uma mensagem (migracao 006).
--
-- O registro em mensagens_processadas acontece ANTES do motor rodar,
-- para que dois webhooks simultaneos da Evolution nao gerem duas
-- respostas. O preco e que uma falha do motor deixa a mensagem
-- marcada como processada sem nunca ter sido respondida: o reenvio da
-- Evolution e descartado como duplicata e o vendedor nao recebe nada.
--
-- O DELETE devolve o message_id ao fluxo, tornando o reenvio uma nova
-- tentativa real. Continua sem UPDATE: a tabela e append-only exceto
-- por esta retratacao.
--
-- Executar contra: -d bot_fichas
-- ============================================================

GRANT DELETE ON TABLE mensagens_processadas TO :"bot_app_user";
