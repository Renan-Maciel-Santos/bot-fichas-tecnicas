-- ============================================================
-- EXTENSIONS DO POSTGRES
-- ============================================================
-- Executado automaticamente na primeira inicialização do banco
-- (via docker-entrypoint-initdb.d)
-- ============================================================

-- pgvector: armazenar e buscar embeddings vetoriais
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm: busca por similaridade textual (trigram)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- unaccent: remover acentos em buscas textuais
CREATE EXTENSION IF NOT EXISTS unaccent;

-- uuid-ossp: gerar UUIDs para chaves primárias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
