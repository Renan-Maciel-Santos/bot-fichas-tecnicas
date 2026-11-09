-- 005_exige_documentacao.sql
--
-- Adiciona semantica de exigencia documental por produto.
--
-- Motivacao:
--   Nem todo produto do catalogo exige documentacao tecnica.
--   Vassouras, panos, baldes e utilitarios nao regulamentados
--   nao possuem FT nem FDS por natureza --- nao por omissao.
--   Sem essa distincao, v_pendentes_revisao produz falsos
--   positivos permanentes e corroi a confianca na observabilidade.
--
-- Estrategia:
--   Dois booleanos independentes (FT e FDS podem exigir separadamente).
--   Default TRUE por conservadorismo (falso negativo esconde
--   documentacao legalmente obrigatoria; falso positivo e corrigivel
--   via heuristica na migration 006).
--   Migration puramente estrutural: nao popula, nao altera views.

ALTER TABLE produtos
  ADD COLUMN exige_ft  BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN exige_fds BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN produtos.exige_ft IS
  'TRUE quando o produto exige Ficha Tecnica. FALSE para itens nao regulamentados (vassouras, panos, embalagens). Populado via heuristica em migration 006.';

COMMENT ON COLUMN produtos.exige_fds IS
  'TRUE quando o produto exige FDS (produto quimico, ABNT NBR 14725). FALSE para itens nao quimicos.';