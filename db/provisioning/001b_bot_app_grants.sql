-- ============================================================
-- 001b_bot_app_grants.sql
-- Privilegios da role de aplicacao (bot_app) no database bot_fichas.
--
-- Principio: least privilege. A aplicacao (API HTTP + motor de
-- resposta) le o acervo e escreve apenas o log de consultas.
-- Ingestao, chunking, extracao e migracoes rodam como botuser.
--
-- GRANT e transacional: rodar dry-run com BEGIN/ROLLBACK antes de aplicar.
--
-- Executar contra: -d bot_fichas
-- ============================================================

-- --- Acesso ao schema ---
GRANT USAGE ON SCHEMA public TO :"bot_app_user";

-- --- Leitura de todo o acervo (tabelas e views) ---
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"bot_app_user";

-- --- Escrita: apenas o log de consultas ---
-- Unico ponto de escrita da aplicacao em runtime (_log_consulta).
-- Sem UPDATE/DELETE: log e append-only por design de auditoria.
GRANT INSERT ON TABLE consultas TO :"bot_app_user";

-- --- Privilegios padrao para objetos futuros ---
-- Sem isto, a proxima migracao cria tabela sem acesso para bot_app,
-- e a quebra aparece semanas depois sem relacao obvia com a causa.
-- Aplica-se apenas a objetos criados por botuser.
ALTER DEFAULT PRIVILEGES FOR ROLE botuser IN SCHEMA public
    GRANT SELECT ON TABLES TO :"bot_app_user";