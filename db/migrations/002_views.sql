-- ============================================================
-- VIEWS DE CONVENIÊNCIA
-- ============================================================
-- Migration: 002_views
-- Facilita consultas comuns sem precisar lembrar dos JOINs
-- ============================================================

-- ------------------------------------------------------------
-- Produtos com seus documentos resumidos
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_produtos_com_docs AS
SELECT 
    p.codigo,
    p.descricao,
    p.marca,
    p.grupo_produto,
    COUNT(DISTINCT pd.documento_id) AS num_documentos,
    BOOL_OR(d.tipo = 'FT')     AS tem_ficha_tecnica,
    BOOL_OR(d.tipo = 'FDS')    AS tem_fds,
    BOOL_OR(d.tipo = 'LAUDO')  AS tem_laudo,
    ARRAY_AGG(DISTINCT d.tipo) FILTER (WHERE d.tipo IS NOT NULL) AS tipos_disponiveis
FROM produtos p
LEFT JOIN produto_documento pd ON pd.produto_codigo = p.codigo
LEFT JOIN documentos d         ON d.id = pd.documento_id
GROUP BY p.codigo, p.descricao, p.marca, p.grupo_produto;


-- ------------------------------------------------------------
-- Cobertura por marca (quantos produtos têm documentação)
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_cobertura_marca AS
SELECT 
    p.marca,
    COUNT(*) AS total_produtos,
    COUNT(DISTINCT pd.produto_codigo) AS produtos_com_doc,
    ROUND(
        100.0 * COUNT(DISTINCT pd.produto_codigo) / NULLIF(COUNT(*), 0),
        1
    ) AS cobertura_pct
FROM produtos p
LEFT JOIN produto_documento pd ON pd.produto_codigo = p.codigo
GROUP BY p.marca
ORDER BY total_produtos DESC;


-- ------------------------------------------------------------
-- Itens pendentes de revisão humana
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_pendentes_revisao AS
SELECT 
    'propriedade' AS tipo_item,
    pp.id::TEXT AS item_id,
    p.codigo AS produto_codigo,
    p.descricao AS produto_descricao,
    pp.campo AS detalhe,
    pp.valor AS valor,
    pp.confianca,
    pp.created_at
FROM propriedades_produto pp
JOIN produtos p ON p.codigo = pp.produto_codigo
WHERE pp.revisado_humano = FALSE AND pp.confianca < 0.80

UNION ALL

SELECT 
    'vinculo_documento' AS tipo_item,
    pd.id::TEXT AS item_id,
    p.codigo,
    p.descricao,
    'match com ' || d.nome_arquivo AS detalhe,
    d.tipo AS valor,
    pd.confianca_match,
    pd.created_at
FROM produto_documento pd
JOIN produtos p   ON p.codigo = pd.produto_codigo
JOIN documentos d ON d.id = pd.documento_id
WHERE pd.revisado_humano = FALSE AND pd.confianca_match < 0.80

ORDER BY confianca ASC, created_at DESC;


-- ------------------------------------------------------------
-- Métricas de uso (últimos 30 dias)
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_metricas_30d AS
SELECT 
    DATE_TRUNC('day', created_at)::DATE AS dia,
    COUNT(*) AS total_consultas,
    COUNT(DISTINCT vendedor_id) AS vendedores_ativos,
    AVG(tempo_resposta_ms)::INTEGER AS tempo_medio_ms,
    SUM(custo_estimado_usd) AS custo_usd_dia,
    COUNT(*) FILTER (WHERE intencao_detectada = 'fora_escopo') AS qtd_fora_escopo,
    COUNT(*) FILTER (WHERE erro IS NOT NULL) AS qtd_erros
FROM consultas
WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY DATE_TRUNC('day', created_at)
ORDER BY dia DESC;
