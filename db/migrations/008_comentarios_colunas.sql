-- ============================================================
-- 008_comentarios_colunas.sql
-- Comentarios nas colunas nao-obvias das tabelas do acervo.
--
-- Contexto: a convencao do projeto pede comentario nas colunas
-- nao-obvias, e boa parte dessa explicacao ja existia — como
-- comentario `--` dentro de 001_initial_schema.sql. O problema e que
-- comentario de arquivo e invisivel para quem inspeciona o banco: um
-- `\d+ consultas` ou uma consulta a information_schema nao mostram
-- nada. Quem for medir o piloto vai olhar o banco, nao a migracao.
--
-- Esta migracao promove essas notas a COMMENT ON COLUMN, e aproveita
-- para corrigir e completar o que mudou desde a 001:
--
-- 1. `produto_documento.metodo_match` listava valores que nunca
--    existiram ('llm', 'nome_arquivo'). Os reais, gravados pela
--    Camada 1 de vinculacao, sao outros.
-- 2. `consultas.intencao_detectada` ganhou intencoes e um sufixo de
--    fallback que a 001 nao previa.
-- 3. Quatro colunas de `consultas` (tokens, custo, feedback) foram
--    criadas para uso futuro e continuam sem preenchimento. Dizer
--    isso evita que alguem construa relatorio sobre coluna vazia.
--
-- Colunas auto-evidentes (id, created_at, updated_at, nome, texto)
-- ficam sem comentario de proposito: comentario obvio e ruido que
-- ensina a ignorar os comentarios.
--
-- COMMENT ON e idempotente — reaplicar sobrescreve.
-- ============================================================

-- ------------------------------------------------------------
-- PRODUTOS
-- ------------------------------------------------------------
COMMENT ON COLUMN produtos.codigo IS
    'Codigo do catalogo do cliente, vindo da planilha — NAO e serial. '
    'E o numero que o vendedor fala ("o 9000001"), por isso e a PK.';

COMMENT ON COLUMN produtos.texto_busca IS
    'Coluna pre-existente na planilha. Base do indice trigram que '
    'resolve o nome falado pelo vendedor em produto '
    '(src/query/search_structured.py: resolver_produto).';

COMMENT ON COLUMN produtos.grupo_produto IS
    'Categoria da planilha do cliente. Nao usada pelo motor de resposta.';

COMMENT ON COLUMN produtos.ncm IS
    'Classificacao fiscal da planilha. Nao usada pelo motor.';

COMMENT ON COLUMN produtos.cest IS
    'Codigo fiscal de substituicao tributaria. Nao usado pelo motor.';

COMMENT ON COLUMN produtos.icms_st IS
    'Flag fiscal da planilha. Nao usada pelo motor.';

COMMENT ON COLUMN produtos.peso IS
    'Peso em kg conforme a planilha. Nao confundir com gramatura ou '
    'volume do produto, que vivem em propriedades_produto.';


-- ------------------------------------------------------------
-- DOCUMENTOS
-- ------------------------------------------------------------
COMMENT ON COLUMN documentos.drive_file_id IS
    'ID do arquivo no Google Drive. UNIQUE: e a chave que torna a '
    'reingestao idempotente — o mesmo arquivo nao vira linha nova.';

COMMENT ON COLUMN documentos.nome_arquivo IS
    'Nome como esta no Drive. Vira a citacao mostrada ao vendedor, '
    'formatado por src/query/citation.py (que remove .pdf e prefixo '
    'redundante de tipo).';

COMMENT ON COLUMN documentos.marca_pasta IS
    'Pasta do Drive onde o arquivo foi encontrado. E a origem das '
    'duplicatas: o mesmo PDF arquivado em duas pastas de marca vira '
    'duas linhas, com drive_file_id diferente.';

COMMENT ON COLUMN documentos.confianca_tipo IS
    'Confianca do classificador de tipo, 0.00 a 1.00. Abaixo de '
    'CONFIANCA_MINIMA_TIPO (config, padrao 0.7) o `tipo` fica NULL e '
    'o documento cai na v_pendentes_revisao.';

COMMENT ON COLUMN documentos.url_drive IS
    'Link de visualizacao no Drive. NULL significa documento NAO '
    'acionavel no WhatsApp: o bot consegue cita-lo, mas nao ha o que '
    'enviar ao vendedor.';

COMMENT ON COLUMN documentos.hash_conteudo IS
    'Hash do conteudo extraido. Detecta arquivo alterado no Drive '
    'entre duas ingestoes, para reprocessar so o que mudou.';

COMMENT ON COLUMN documentos.is_ocr IS
    'TRUE quando o texto veio de OCR, nao da camada de texto do PDF. '
    'Extracao de OCR e menos confiavel — util ao auditar resposta '
    'estranha.';

COMMENT ON COLUMN documentos.texto_extraido IS
    'Texto bruto do PDF. Fonte dos chunks; mantido para reprocessar '
    'chunking ou extracao sem baixar o arquivo de novo.';

COMMENT ON COLUMN documentos.data_modificacao_drive IS
    'Data de modificacao informada pelo Drive, nao por este sistema.';

COMMENT ON COLUMN documentos.data_indexacao IS
    'Quando o pipeline processou o arquivo. NULL = baixado mas ainda '
    'nao processado.';

COMMENT ON COLUMN documentos.erro_processamento IS
    'Mensagem da falha de extracao, quando houve. Preenchido = o '
    'documento existe mas nao gerou texto nem chunks.';


-- ------------------------------------------------------------
-- PRODUTO_DOCUMENTO
-- ------------------------------------------------------------
COMMENT ON COLUMN produto_documento.confianca_match IS
    'Confianca do vinculo, 0.00 a 1.00. Vinculo fraco alimenta a fila '
    'de revisao humana em vez de ser descartado.';

COMMENT ON COLUMN produto_documento.metodo_match IS
    'Como o vinculo foi criado: "marca_trigram" (similaridade do nome '
    'do arquivo com a descricao, dentro da marca), "codigo_prefixo" '
    '(codigo do produto no inicio do nome do arquivo), "manual" '
    '(curadoria), "test-fixture" (dados de teste). A migracao 001 '
    'listava "llm" e "nome_arquivo", que nunca chegaram a existir.';

COMMENT ON COLUMN produto_documento.revisado_humano IS
    'TRUE quando alguem confirmou o vinculo. Junto com confianca_match '
    'forma a fila de revisao (idx_prod_doc_revisao).';

COMMENT ON COLUMN produto_documento.revisor IS
    'Quem revisou. Texto livre — nao ha cadastro de usuarios.';


-- ------------------------------------------------------------
-- PROPRIEDADES_PRODUTO
-- ------------------------------------------------------------
COMMENT ON COLUMN propriedades_produto.documento_id IS
    'De qual documento a propriedade foi extraida. E a procedencia '
    'que o bot cita na resposta. ON DELETE SET NULL: perder a fonte '
    'nao apaga o dado, mas a resposta deixa de citar de onde veio.';

COMMENT ON COLUMN propriedades_produto.valor IS
    'Valor sempre como texto, preservando a forma do documento '
    '("10 - 13", "> 9"). E o que o vendedor le.';

COMMENT ON COLUMN propriedades_produto.valor_numerico IS
    'Mesmo valor em numero, quando aplicavel. Permite comparacao e '
    'faixa; NULL para valores textuais ou intervalos.';

COMMENT ON COLUMN propriedades_produto.unidade IS
    'Unidade separada do valor quando identificavel ("%", "g/m2").';

COMMENT ON COLUMN propriedades_produto.confianca IS
    'Confianca da extracao, 0.00 a 1.00. Ordena as fontes na resposta '
    '— a mais confiavel e citada primeiro.';

COMMENT ON COLUMN propriedades_produto.revisado_humano IS
    'TRUE quando alguem confirmou o valor extraido.';

COMMENT ON COLUMN propriedades_produto.revisor IS
    'Quem revisou. Texto livre — nao ha cadastro de usuarios.';


-- ------------------------------------------------------------
-- CHUNKS
-- ------------------------------------------------------------
COMMENT ON COLUMN chunks.produto_codigo IS
    'Produto do chunk, desnormalizado a partir do vinculo do '
    'documento. Permite filtrar a busca vetorial por produto sem '
    'JOIN; NULL quando o documento nao esta vinculado a nenhum.';

COMMENT ON COLUMN chunks.chunk_index IS
    'Ordem do pedaco dentro do documento, a partir de 0.';

COMMENT ON COLUMN chunks.embedding IS
    'Vetor de 1536 dimensoes (OpenAI text-embedding-3-small). NULL = '
    'chunk criado mas ainda nao embeddado: invisivel a busca vetorial.';

COMMENT ON COLUMN chunks.num_tokens IS
    'Tamanho do chunk em tokens, medido no chunking. Usado para '
    'estimar custo antes de embeddar.';


-- ------------------------------------------------------------
-- CONSULTAS
-- ------------------------------------------------------------
COMMENT ON COLUMN consultas.vendedor_id IS
    'Quem perguntou. Telefone E.164 sem "+" quando canal = whatsapp; '
    'identificador livre nos demais canais. NAO tem FK para '
    'vendedores_autorizados: o log precisa sobreviver ao descadastro.';

COMMENT ON COLUMN consultas.canal IS
    'Origem da pergunta: "whatsapp" (producao), "terminal" '
    '(scripts/chat.py) ou "eval" (scripts/eval_motor.py). Filtrar por '
    'este campo e o que separa uso real de teste nas metricas.';

COMMENT ON COLUMN consultas.intencao_detectada IS
    'Rota escolhida pelo classificador: busca_caracteristica, '
    'propriedades_produto, fetch_documento, pergunta_aberta, '
    'fora_escopo — ou "erro" quando o motor falhou. Recebe o sufixo '
    '" [fallback]" quando a classificacao caiu na rota tolerante, o '
    'que permite medir a taxa de erro de roteamento.';

COMMENT ON COLUMN consultas.produtos_referenciados IS
    'Codigos citados na resposta. Em pergunta ambigua, sao os '
    'candidatos oferecidos, nao um produto escolhido.';

COMMENT ON COLUMN consultas.documentos_enviados IS
    'IDs dos documentos citados/anexados. Vazio numa resposta que '
    'menciona documentos e sinal de inconsistencia.';

COMMENT ON COLUMN consultas.tempo_resposta_ms IS
    'Tempo do motor, do inicio da classificacao ate a resposta '
    'pronta. NAO inclui o trajeto pelo n8n nem pela Evolution.';

COMMENT ON COLUMN consultas.tokens_input IS
    'PREVISTO, NUNCA PREENCHIDO. O motor nao instrumenta consumo de '
    'token hoje — nao construir relatorio sobre esta coluna.';

COMMENT ON COLUMN consultas.tokens_output IS
    'PREVISTO, NUNCA PREENCHIDO. Ver tokens_input.';

COMMENT ON COLUMN consultas.custo_estimado_usd IS
    'PREVISTO, NUNCA PREENCHIDO. Depende de tokens_input/output.';

COMMENT ON COLUMN consultas.feedback IS
    'PREVISTO, NUNCA PREENCHIDO. Reservado para "positivo"/"negativo" '
    'quando houver coleta de feedback no WhatsApp.';

COMMENT ON COLUMN consultas.feedback_observacao IS
    'PREVISTO, NUNCA PREENCHIDO. Ver feedback.';

COMMENT ON COLUMN consultas.erro IS
    'Excecao capturada pelo motor. Preenchido = o vendedor recebeu a '
    'mensagem de indisponibilidade, nao uma resposta.';


-- ------------------------------------------------------------
-- VENDEDORES_AUTORIZADOS
-- ------------------------------------------------------------
COMMENT ON COLUMN vendedores_autorizados.telefone IS
    'E.164 sem "+" (ex.: 5511999998888) — igual ao remoteJid da '
    'Evolution sem o sufixo "@s.whatsapp.net". Formato errado nao '
    'autoriza e nao da erro visivel: o vendedor so recebe silencio.';

COMMENT ON COLUMN vendedores_autorizados.observacao IS
    'Nota livre de operacao (regiao, quem pediu o cadastro, motivo da '
    'desativacao).';


-- ------------------------------------------------------------
-- MENSAGENS_PROCESSADAS
-- ------------------------------------------------------------
COMMENT ON COLUMN mensagens_processadas.message_id IS
    'ID da mensagem na Evolution (data.key.id do webhook). PK: e o '
    'que torna o INSERT ... ON CONFLICT um dedupe atomico.';

COMMENT ON COLUMN mensagens_processadas.remetente IS
    'Telefone de origem, apenas para auditoria — o dedupe e por '
    'message_id.';

COMMENT ON COLUMN mensagens_processadas.processado_em IS
    'Quando a mensagem foi aceita para processamento. Nao significa '
    'que houve resposta: em falha do motor a linha e apagada para '
    'liberar o reenvio.';


-- ------------------------------------------------------------
-- CONTEXTO_CONVERSA
-- ------------------------------------------------------------
COMMENT ON COLUMN contexto_conversa.vendedor_id IS
    'Mesmo identificador de consultas.vendedor_id. PK: uma pendencia '
    'por vendedor, substituida por UPSERT.';

COMMENT ON COLUMN contexto_conversa.pergunta_original IS
    'A pergunta que gerou a desambiguacao, guardada para auditoria — '
    'a reexecucao usa intencao/campo/tipo_documento, nao este texto.';
