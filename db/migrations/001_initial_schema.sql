-- ============================================================
-- SCHEMA INICIAL — BOT FICHAS TÉCNICAS
-- ============================================================
-- Migration: 001_initial_schema
-- Aplicar em banco já com extensions (ver 01_extensions.sql)
-- ============================================================

-- ------------------------------------------------------------
-- PRODUTOS
-- ------------------------------------------------------------
-- Espelha a planilha de produtos do amigo.
-- Importada via src/ingestion/import_excel.py
-- ------------------------------------------------------------
CREATE TABLE produtos (
    codigo              INTEGER PRIMARY KEY,
    descricao           TEXT NOT NULL,
    marca               TEXT NOT NULL,
    grupo_produto       TEXT,
    ncm                 TEXT,
    cest                TEXT,
    peso                NUMERIC(10, 3),
    icms_st             BOOLEAN,
    texto_busca         TEXT,              -- coluna pré-existente na planilha
    ativo               BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Busca textual rápida por descrição e marca
CREATE INDEX idx_produtos_marca ON produtos (marca);
CREATE INDEX idx_produtos_grupo ON produtos (grupo_produto);
CREATE INDEX idx_produtos_texto_busca_trgm 
    ON produtos USING GIN (texto_busca gin_trgm_ops);
CREATE INDEX idx_produtos_descricao_trgm 
    ON produtos USING GIN (descricao gin_trgm_ops);

COMMENT ON TABLE produtos IS 'Catálogo de produtos importado da planilha Excel do amigo';


-- ------------------------------------------------------------
-- DOCUMENTOS
-- ------------------------------------------------------------
-- Cada arquivo encontrado no Google Drive vira uma linha aqui.
-- Tipo: FT (ficha técnica), FDS (ficha de segurança), 
--       LAUDO, OUTRO (institucional, representantes, etc.)
-- ------------------------------------------------------------
CREATE TABLE documentos (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    drive_file_id           TEXT UNIQUE NOT NULL,
    nome_arquivo            TEXT NOT NULL,
    marca_pasta             TEXT NOT NULL,       -- nome da pasta no Drive
    tipo                    TEXT CHECK (tipo IN ('FT', 'FDS', 'LAUDO', 'OUTRO', 'INDEFINIDO')),
    confianca_tipo          NUMERIC(3, 2),       -- 0.00 a 1.00
    url_drive               TEXT,
    hash_conteudo           TEXT,                -- detecta mudança de conteúdo
    tamanho_bytes           BIGINT,
    num_paginas             INTEGER,
    is_ocr                  BOOLEAN DEFAULT FALSE,
    texto_extraido          TEXT,
    data_modificacao_drive  TIMESTAMPTZ,
    data_indexacao          TIMESTAMPTZ,
    erro_processamento      TEXT,                -- se falhou na extração, fica aqui
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_documentos_marca ON documentos (marca_pasta);
CREATE INDEX idx_documentos_tipo ON documentos (tipo);
CREATE INDEX idx_documentos_drive_file_id ON documentos (drive_file_id);

COMMENT ON TABLE documentos IS 'Arquivos PDF encontrados no Drive, com texto extraído';
COMMENT ON COLUMN documentos.tipo IS 'FT=Ficha Técnica, FDS=Ficha de Dados de Segurança, LAUDO=Laudo, OUTRO=Outros (institucional)';


-- ------------------------------------------------------------
-- PRODUTO_DOCUMENTO
-- ------------------------------------------------------------
-- Vínculo N:M entre produtos e documentos.
-- Um produto pode ter 0..N documentos; um documento pode 
-- (raramente) referenciar mais de um produto.
-- ------------------------------------------------------------
CREATE TABLE produto_documento (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    produto_codigo      INTEGER NOT NULL REFERENCES produtos(codigo) ON DELETE CASCADE,
    documento_id        UUID NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    confianca_match     NUMERIC(3, 2) NOT NULL,  -- 0.00 a 1.00
    metodo_match        TEXT,                     -- 'llm', 'nome_arquivo', 'manual', 'codigo_no_pdf'
    revisado_humano     BOOLEAN DEFAULT FALSE,
    revisor             TEXT,
    revisado_em         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (produto_codigo, documento_id)
);

CREATE INDEX idx_prod_doc_produto ON produto_documento (produto_codigo);
CREATE INDEX idx_prod_doc_documento ON produto_documento (documento_id);
CREATE INDEX idx_prod_doc_revisao ON produto_documento (revisado_humano, confianca_match);

COMMENT ON TABLE produto_documento IS 'Associação entre produtos e seus documentos (FT/FDS/Laudo)';


-- ------------------------------------------------------------
-- PROPRIEDADES_PRODUTO
-- ------------------------------------------------------------
-- Campos estruturados extraídos das fichas técnicas.
-- Modelo key-value com confiança e fonte (qual documento gerou).
-- Permite responder "produtos com peróxido de hidrogênio" via SQL.
-- ------------------------------------------------------------
CREATE TABLE propriedades_produto (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    produto_codigo      INTEGER NOT NULL REFERENCES produtos(codigo) ON DELETE CASCADE,
    documento_id        UUID REFERENCES documentos(id) ON DELETE SET NULL,
    campo               TEXT NOT NULL,           -- 'ativo', 'concentracao', 'ph', 'diluicao', etc.
    valor               TEXT NOT NULL,           -- valor sempre como texto
    valor_numerico      NUMERIC,                 -- preenchido quando aplicável
    unidade             TEXT,                    -- '%', 'ml', 'cm', etc.
    confianca           NUMERIC(3, 2) NOT NULL,  -- 0.00 a 1.00
    revisado_humano     BOOLEAN DEFAULT FALSE,
    revisor             TEXT,
    revisado_em         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_prop_produto ON propriedades_produto (produto_codigo);
CREATE INDEX idx_prop_campo ON propriedades_produto (campo);
CREATE INDEX idx_prop_campo_valor ON propriedades_produto (campo, valor);
CREATE INDEX idx_prop_revisao ON propriedades_produto (revisado_humano, confianca);
-- Busca textual no valor (ex: encontrar todos com 'peróxido' em qualquer campo)
CREATE INDEX idx_prop_valor_trgm ON propriedades_produto USING GIN (valor gin_trgm_ops);

COMMENT ON TABLE propriedades_produto IS 'Campos estruturados extraídos das fichas via LLM';
COMMENT ON COLUMN propriedades_produto.campo IS 'Ex: ativo, concentracao, ph, diluicao, dimensao, validade, registro_anvisa';


-- ------------------------------------------------------------
-- CHUNKS
-- ------------------------------------------------------------
-- Pedaços de texto das fichas, com embedding vetorial.
-- Usado para busca semântica em perguntas abertas
-- ("como aplicar este produto?", "qual a finalidade?").
-- Dimensão 1536 = OpenAI text-embedding-3-small.
-- ------------------------------------------------------------
CREATE TABLE chunks (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    documento_id        UUID NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    produto_codigo      INTEGER REFERENCES produtos(codigo) ON DELETE SET NULL,
    chunk_index         INTEGER NOT NULL,        -- ordem do chunk no documento
    texto               TEXT NOT NULL,
    embedding           vector(1536),
    num_tokens          INTEGER,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_chunks_documento ON chunks (documento_id);
CREATE INDEX idx_chunks_produto ON chunks (produto_codigo);
-- Índice vetorial: HNSW é mais rápido e preciso que IVFFlat para nosso volume
CREATE INDEX idx_chunks_embedding ON chunks 
    USING hnsw (embedding vector_cosine_ops);

COMMENT ON TABLE chunks IS 'Pedaços de texto com embeddings para busca semântica';


-- ------------------------------------------------------------
-- CONSULTAS
-- ------------------------------------------------------------
-- Log de toda interação com o bot. Crucial para:
--   1. Auditoria de respostas erradas
--   2. Identificar perguntas frequentes
--   3. Detectar lacunas na base
--   4. Calcular custo de operação
-- ------------------------------------------------------------
CREATE TABLE consultas (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vendedor_id             TEXT,                -- número WhatsApp ou identificador
    canal                   TEXT DEFAULT 'whatsapp',
    pergunta                TEXT NOT NULL,
    resposta                TEXT,
    intencao_detectada      TEXT,                -- 'busca_caracteristica', 'fetch_documento', 'pergunta_aberta', 'fora_escopo'
    produtos_referenciados  INTEGER[],           -- códigos dos produtos citados na resposta
    documentos_enviados     UUID[],              -- IDs dos PDFs anexados
    tempo_resposta_ms       INTEGER,
    tokens_input            INTEGER,
    tokens_output           INTEGER,
    custo_estimado_usd      NUMERIC(10, 6),
    feedback                TEXT,                -- 'positivo', 'negativo', NULL — uso futuro
    feedback_observacao     TEXT,
    erro                    TEXT,                -- se algo deu errado
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_consultas_vendedor ON consultas (vendedor_id);
CREATE INDEX idx_consultas_created ON consultas (created_at DESC);
CREATE INDEX idx_consultas_intencao ON consultas (intencao_detectada);

COMMENT ON TABLE consultas IS 'Log completo das interações com o bot (auditoria, métricas, melhoria)';


-- ------------------------------------------------------------
-- TRIGGER: atualizar updated_at automaticamente
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION atualizar_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_produtos_updated_at
    BEFORE UPDATE ON produtos
    FOR EACH ROW EXECUTE FUNCTION atualizar_updated_at();

CREATE TRIGGER trg_documentos_updated_at
    BEFORE UPDATE ON documentos
    FOR EACH ROW EXECUTE FUNCTION atualizar_updated_at();

CREATE TRIGGER trg_propriedades_updated_at
    BEFORE UPDATE ON propriedades_produto
    FOR EACH ROW EXECUTE FUNCTION atualizar_updated_at();
