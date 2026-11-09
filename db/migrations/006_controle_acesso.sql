-- ============================================================
-- 006_controle_acesso.sql
-- Tabelas de controle de acesso e idempotencia para a API HTTP.
--
-- Contexto: com a entrada do canal WhatsApp, a aplicacao passa a
-- receber requisicoes de origem externa. Duas politicas precisam de
-- um ponto unico de decisao no backend:
--
-- 1. Autorizacao: apenas numeros cadastrados podem consultar. Sem
--    isso, qualquer pessoa que descubra o numero consome a cota da
--    OpenAI.
-- 2. Idempotencia: a Evolution API reenvia webhook quando nao recebe
--    ack a tempo. Sem dedupe, o vendedor recebe resposta duplicada e
--    o custo e cobrado duas vezes.
--
-- Ambas ficam na API (nao no n8n) para manter ponto unico de politica:
-- se o transporte mudar, a regra nao se move.
-- ============================================================

-- --- Vendedores autorizados a consultar o bot ---
CREATE TABLE IF NOT EXISTS vendedores_autorizados (
    telefone    text PRIMARY KEY,
    nome        text NOT NULL,
    ativo       boolean NOT NULL DEFAULT TRUE,
    observacao  text,
    criado_em   timestamptz NOT NULL DEFAULT now(),
    atualizado_em timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE vendedores_autorizados IS
    'Whitelist de numeros habilitados. Formato do telefone: E.164 sem "+" '
    '(ex.: 5511999998888), igual ao remoteJid da Evolution sem o sufixo.';

COMMENT ON COLUMN vendedores_autorizados.ativo IS
    'Desativacao e soft delete: preserva o historico em consultas.vendedor_id.';

CREATE INDEX IF NOT EXISTS idx_vendedores_ativos
    ON vendedores_autorizados (telefone) WHERE ativo;

-- --- Idempotencia de mensagens recebidas ---
CREATE TABLE IF NOT EXISTS mensagens_processadas (
    message_id    text PRIMARY KEY,
    remetente     text,
    processado_em timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE mensagens_processadas IS
    'Dedupe de webhooks reenviados pela Evolution API. INSERT ... ON '
    'CONFLICT DO NOTHING: se nao inseriu, a mensagem ja foi respondida.';

CREATE INDEX IF NOT EXISTS idx_mensagens_processadas_data
    ON mensagens_processadas (processado_em DESC);