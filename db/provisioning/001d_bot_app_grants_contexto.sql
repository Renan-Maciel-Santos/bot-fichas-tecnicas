-- ============================================================
-- 001d_bot_app_grants_contexto.sql
-- Privilegios da aplicacao sobre o estado de conversa (migracao 007).
--
-- SELECT ja veio do ALTER DEFAULT PRIVILEGES do 001b. Aqui apenas a
-- escrita que a API precisa em runtime.
--
-- INSERT/UPDATE: o UPSERT que grava a pendencia (uma linha por vendedor).
-- DELETE: a pendencia e consumida assim que o vendedor escolhe, e
-- descartada quando ele muda de assunto. Sem DELETE, uma pendencia
-- resolvida ficaria "viva" ate o TTL, capturando a proxima mensagem.
--
-- Executar contra: -d bot_fichas
-- ============================================================

GRANT INSERT, UPDATE, DELETE ON TABLE contexto_conversa TO :"bot_app_user";
