-- 003: Estende v_pendentes_revisao para incluir propriedades extraidas
--
-- Motivacao:
--   O design original (README de src/extraction) previa revisao humana
--   de propriedades com confianca < 0.80. A view da migration 002 cobria
--   apenas vinculos documento-produto; esta migration adiciona as
--   propriedades extraidas pelo LLM, mantendo o formato de feed unificado.
--
-- Idempotente: CREATE OR REPLACE.

CREATE OR REPLACE VIEW v_pendentes_revisao AS
SELECT 'vinculo_documento'::text AS tipo_item,
    pd.id::text AS item_id,
    p.codigo AS produto_codigo,
    p.descricao AS produto_descricao,
    'match com '::text || d.nome_arquivo AS detalhe,
    d.tipo AS valor,
    pd.confianca_match AS confianca,
    pd.created_at
   FROM produto_documento pd
     JOIN produtos p ON p.codigo = pd.produto_codigo
     JOIN documentos d ON d.id = pd.documento_id
  WHERE pd.revisado_humano = false AND pd.confianca_match < 0.80

UNION ALL

SELECT 'propriedade'::text AS tipo_item,
    pp.id::text AS item_id,
    p.codigo AS produto_codigo,
    p.descricao AS produto_descricao,
    pp.campo AS detalhe,
    pp.valor AS valor,
    pp.confianca AS confianca,
    pp.created_at
   FROM propriedades_produto pp
     JOIN produtos p ON p.codigo = pp.produto_codigo
  WHERE pp.revisado_humano = false AND pp.confianca < 0.80

ORDER BY 7, 8 DESC;