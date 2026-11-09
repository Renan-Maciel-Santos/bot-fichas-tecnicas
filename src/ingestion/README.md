# src/ingestion

Pipelines de entrada de dados. Tudo que enche o banco com informação vem daqui.

## O que tem aqui

### `import_excel.py` (funcional)
Importa a planilha de produtos para a tabela `produtos`. Faz UPSERT — pode rodar várias vezes sem duplicar.

Uso:
```bash
python -m src.ingestion.import_excel data/Produtos.xlsx
# ou simplesmente
make import-excel
```

## O que falta construir

### `drive_sync.py` (Fase 2)
Lista PDFs na pasta do Drive, baixa novos/modificados, popula a tabela `documentos`.

### `pdf_extract.py` (Fase 2)
Extrai texto de PDF. Tenta extração nativa primeiro (pypdf/pdfplumber); se falhar ou retornar texto vazio, fallback para OCR (tesseract local ou API).

### `chunker.py` (Fase 2)
Quebra o texto extraído em chunks de ~500 tokens com overlap. Cada chunk vira uma linha na tabela `chunks`.

### `embedder.py` (Fase 2)
Gera embeddings dos chunks via OpenAI API e popula a coluna `embedding` da tabela `chunks`.

## Ordem de execução típica (Fase 2 em diante)

1. `import_excel.py` — popular produtos
2. `drive_sync.py` — baixar PDFs
3. `pdf_extract.py` — extrair texto
4. `extraction/extract_fields.py` — extrair campos estruturados
5. `chunker.py` + `embedder.py` — indexar para busca semântica
