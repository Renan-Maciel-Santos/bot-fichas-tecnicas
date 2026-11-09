-- ============================================================
-- 007_contexto_conversa.sql
-- Estado minimo de conversa para resolver a desambiguacao de produto.
--
-- Contexto: quando o termo do vendedor casa com varios produtos, o
-- motor responde "Encontrei mais de um produto parecido. Qual deles?
-- 1... 2... 3..." e pede que ele responda com o numero. Sem guardar
-- nada, a proxima mensagem ("2") chega ao classificador sem contexto,
-- e a conversa morre ali — o vendedor nunca consegue chegar na
-- resposta que pediu.
--
-- Escopo deliberadamente estreito: isto NAO e historico de conversa
-- nem memoria de dialogo. Guarda uma unica pendencia por vendedor,
-- com validade curta, exclusivamente para reexecutar a intencao
-- original assim que ele escolher o produto. Historico completo
-- continua em `consultas` (append-only, auditoria).
--
-- Uma linha por vendedor (PK em vendedor_id): a pendencia nova
-- substitui a anterior por UPSERT. Se o vendedor abandonou a escolha
-- e perguntou outra coisa, a pendencia velha nao interessa mais.
-- ============================================================

CREATE TABLE IF NOT EXISTS contexto_conversa (
    vendedor_id       text PRIMARY KEY,
    intencao          text NOT NULL,
    campo             text,
    tipo_documento    text,
    pergunta_original text NOT NULL,
    candidatos        jsonb NOT NULL,
    criado_em         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE contexto_conversa IS
    'Pendencia de desambiguacao por vendedor. Uma linha por vendedor, '
    'substituida por UPSERT. Expira por TTL na aplicacao (ver '
    'src/query/contexto.py), nao por job de limpeza.';

COMMENT ON COLUMN contexto_conversa.intencao IS
    'Intencao da pergunta ORIGINAL, para reexecutar apos a escolha. '
    'Sem isto, "2" seria reclassificado do zero e viraria fora_escopo.';

COMMENT ON COLUMN contexto_conversa.campo IS
    'Campo perguntado na pergunta original (ex.: "ph"), quando havia um. '
    'Preserva "qual o pH do X?" -> "2" -> responde o pH, nao tudo.';

COMMENT ON COLUMN contexto_conversa.tipo_documento IS
    'Tipo pedido na pergunta original (FT/FDS/LAUDO), quando havia um. '
    'Preserva "manda a ficha tecnica do X" -> "2" -> entrega a FT, '
    'nao a FDS.';

COMMENT ON COLUMN contexto_conversa.candidatos IS
    'Array JSON de {ordem, codigo, descricao, marca}, na MESMA ordem '
    'apresentada ao vendedor. A ordem e o contrato: ele responde "2" '
    'referindo-se a posicao que leu na mensagem.';

COMMENT ON COLUMN contexto_conversa.criado_em IS
    'Base do TTL. Pendencia antiga e ignorada na leitura: o vendedor '
    'que volta horas depois esta comecando outra conversa.';

-- Leitura por TTL ("pendencia deste vendedor, se recente"). O indice
-- so ajuda numa varredura de expurgo; a consulta por PK ja e direta.
CREATE INDEX IF NOT EXISTS idx_contexto_conversa_criado_em
    ON contexto_conversa (criado_em);
