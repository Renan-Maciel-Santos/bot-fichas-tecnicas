-- =====================================================================
-- migrations/004_soft_delete.sql
-- =====================================================================
-- Objetivo: introduzir soft delete em `documentos` e `produtos`,
-- pre-requisito para a sincronizacao com o Google Drive e para
-- descontinuidade de itens do catalogo sem perder rastreabilidade.
--
-- Principios arquiteturais:
--   1. Timestamps null-when-ativo em vez de booleanos: revogado_em /
--      descontinuado_em carregam a informacao "quando", que booleanos
--      escondem. Padrao de sistemas com auditoria (Stripe, Nubank).
--   2. Backward compatibility: produtos.ativo permanece na tabela.
--      E populado descontinuado_em a partir de linhas ativo=false
--      existentes; o campo antigo entra em modo "deprecated" e sera
--      removido em migracao futura (expand-migrate-contract).
--   3. Sucessao explicita: documentos.substituido_por_id (FK auto-ref)
--      permite responder "esse PDF virou qual?" sem tabela historica.
--   4. Views existentes filtram automaticamente os itens revogados/
--      descontinuados, para que o motor de resposta nunca vaze dado
--      obsoleto sem esforco adicional na camada de query.
--   5. Sem indices parciais nesta migracao. Para o volume atual
--      (~2.700 produtos, ~22 docs) o ganho e marginal. Adicionaveis
--      em migracao futura se um EXPLAIN ANALYZE indicar hot path.
-- =====================================================================

BEGIN;

-- --------------------------------------------------------------------
-- 1) documentos: soft delete + rastro de sucessao
-- --------------------------------------------------------------------
ALTER TABLE documentos
    ADD COLUMN IF NOT EXISTS revogado_em         TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS motivo_revogacao    TEXT        NULL,
    ADD COLUMN IF NOT EXISTS substituido_por_id  UUID        NULL
        REFERENCES documentos(id) ON DELETE SET NULL;

COMMENT ON COLUMN documentos.revogado_em IS
    'Momento em que o documento foi revogado (soft delete). NULL = ativo.';
COMMENT ON COLUMN documentos.motivo_revogacao IS
    'Motivo textual da revogacao (ex.: "atualizacao INMETRO 2026-05").';
COMMENT ON COLUMN documentos.substituido_por_id IS
    'Documento sucessor, quando aplicavel (nova versao do mesmo PDF).';

CREATE INDEX IF NOT EXISTS idx_documentos_substituido_por
    ON documentos(substituido_por_id)
    WHERE substituido_por_id IS NOT NULL;

-- --------------------------------------------------------------------
-- 2) produtos: soft delete de catalogo
-- --------------------------------------------------------------------
ALTER TABLE produtos
    ADD COLUMN IF NOT EXISTS descontinuado_em      TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS motivo_descontinuacao TEXT        NULL;

COMMENT ON COLUMN produtos.descontinuado_em IS
    'Momento em que o produto foi descontinuado. NULL = ativo. Substitui a semantica de produtos.ativo (deprecated).';
COMMENT ON COLUMN produtos.motivo_descontinuacao IS
    'Motivo textual (ex.: "fora de linha", "substituido por 999999").';
COMMENT ON COLUMN produtos.ativo IS
    'DEPRECATED: use "descontinuado_em IS NULL". Mantido temporariamente para compatibilidade.';

-- Migracao de dados: linhas com ativo=false ganham timestamp.
-- Usamos updated_at como aproximacao: nao temos o momento real da
-- mudanca, mas e mais informativo que NOW() e nao gera falso
-- "todo mundo descontinuado hoje".
UPDATE produtos
   SET descontinuado_em = COALESCE(updated_at, NOW())
 WHERE ativo = FALSE
   AND descontinuado_em IS NULL;

-- --------------------------------------------------------------------
-- 3) Views existentes: filtrar revogados/descontinuados
-- --------------------------------------------------------------------
-- Estrutura de colunas mantida; apenas WHERE/joins ajustados.
-- Consumidores (Metabase, apps) continuam funcionando sem alteracao.

CREATE OR REPLACE VIEW v_cobertura_marca AS
 SELECT p.marca,
        count(*)                                                          AS total_produtos,
        count(DISTINCT pd.produto_codigo) FILTER (WHERE d.id IS NOT NULL) AS produtos_com_doc,
        round(
            (100.0 * count(DISTINCT pd.produto_codigo) FILTER (WHERE d.id IS NOT NULL))
            / NULLIF(count(*), 0)::numeric,
            1
        )                                                                 AS cobertura_pct
   FROM produtos p
   LEFT JOIN produto_documento pd ON pd.produto_codigo = p.codigo
   LEFT JOIN documentos        d  ON d.id = pd.documento_id
                                 AND d.revogado_em IS NULL
  WHERE p.descontinuado_em IS NULL
  GROUP BY p.marca
  ORDER BY count(*) DESC;

CREATE OR REPLACE VIEW v_produtos_com_docs AS
 SELECT p.codigo,
        p.descricao,
        p.marca,
        p.grupo_produto,
        count(DISTINCT pd.documento_id) FILTER (WHERE d.revogado_em IS NULL) AS num_documentos,
        bool_or(d.tipo = 'FT'    AND d.revogado_em IS NULL)                   AS tem_ficha_tecnica,
        bool_or(d.tipo = 'FDS'   AND d.revogado_em IS NULL)                   AS tem_fds,
        bool_or(d.tipo = 'LAUDO' AND d.revogado_em IS NULL)                   AS tem_laudo,
        array_agg(DISTINCT d.tipo)
            FILTER (WHERE d.tipo IS NOT NULL AND d.revogado_em IS NULL)       AS tipos_disponiveis
   FROM produtos p
   LEFT JOIN produto_documento pd ON pd.produto_codigo = p.codigo
   LEFT JOIN documentos        d  ON d.id = pd.documento_id
  WHERE p.descontinuado_em IS NULL
  GROUP BY p.codigo, p.descricao, p.marca, p.grupo_produto;

CREATE OR REPLACE VIEW v_pendentes_revisao AS
 SELECT 'vinculo_documento'::text                     AS tipo_item,
        pd.id::text                                   AS item_id,
        p.codigo                                      AS produto_codigo,
        p.descricao                                   AS produto_descricao,
        ('match com '::text || d.nome_arquivo)        AS detalhe,
        d.tipo                                        AS valor,
        pd.confianca_match                            AS confianca,
        pd.created_at
   FROM produto_documento pd
   JOIN produtos   p ON p.codigo = pd.produto_codigo
   JOIN documentos d ON d.id     = pd.documento_id
  WHERE pd.revisado_humano = FALSE
    AND pd.confianca_match < 0.80
    AND p.descontinuado_em IS NULL
    AND d.revogado_em      IS NULL
 UNION ALL
 SELECT 'propriedade'::text                           AS tipo_item,
        pp.id::text                                   AS item_id,
        p.codigo                                      AS produto_codigo,
        p.descricao                                   AS produto_descricao,
        pp.campo                                      AS detalhe,
        pp.valor,
        pp.confianca,
        pp.created_at
   FROM propriedades_produto pp
   JOIN produtos p ON p.codigo = pp.produto_codigo
  WHERE pp.revisado_humano = FALSE
    AND pp.confianca < 0.80
    AND p.descontinuado_em IS NULL
  ORDER BY 7, 8 DESC;

-- v_metricas_30d nao e alterada: opera sobre `consultas`, que e log
-- historico e nao tem semantica de soft delete.

-- --------------------------------------------------------------------
-- 4) Views novas: auditoria
-- --------------------------------------------------------------------

CREATE OR REPLACE VIEW v_documentos_revogados AS
 SELECT d.id,
        d.nome_arquivo,
        d.marca_pasta,
        d.tipo,
        d.revogado_em,
        d.motivo_revogacao,
        d.substituido_por_id,
        s.nome_arquivo  AS substituido_por_nome
   FROM documentos d
   LEFT JOIN documentos s ON s.id = d.substituido_por_id
  WHERE d.revogado_em IS NOT NULL
  ORDER BY d.revogado_em DESC;

CREATE OR REPLACE VIEW v_produtos_descontinuados AS
 SELECT p.codigo,
        p.descricao,
        p.marca,
        p.grupo_produto,
        p.descontinuado_em,
        p.motivo_descontinuacao
   FROM produtos p
  WHERE p.descontinuado_em IS NOT NULL
  ORDER BY p.descontinuado_em DESC;

-- --------------------------------------------------------------------
-- 5) Sanity check: aborta a transacao se colunas nao foram criadas
-- --------------------------------------------------------------------
DO $$
DECLARE
    v_docs_cols  INT;
    v_prods_cols INT;
BEGIN
    SELECT count(*) INTO v_docs_cols
      FROM information_schema.columns
     WHERE table_name = 'documentos'
       AND column_name IN ('revogado_em', 'motivo_revogacao', 'substituido_por_id');

    SELECT count(*) INTO v_prods_cols
      FROM information_schema.columns
     WHERE table_name = 'produtos'
       AND column_name IN ('descontinuado_em', 'motivo_descontinuacao');

    IF v_docs_cols <> 3 OR v_prods_cols <> 2 THEN
        RAISE EXCEPTION 'Migracao 004 incompleta: documentos=%/3, produtos=%/2',
            v_docs_cols, v_prods_cols;
    END IF;

    RAISE NOTICE 'Migracao 004: sanity check OK.';
END $$;

COMMIT;