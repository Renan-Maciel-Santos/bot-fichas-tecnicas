#!/usr/bin/env bash
# =====================================================================
# Carrega a demo: catalogo ficticio + PDFs ficticios, pelo pipeline real.
#
# Pre-requisito: o bot instalado sem dump (deploy/instalar.sh), com a
# OPENAI_API_KEY no .env. Custo: alguns centavos de dolar (embeddings,
# extracao de propriedades e a Camada 2 da vinculacao).
#
# Cada etapa roda dentro do container da API, com a pasta demo/ montada:
# quem clonar nao precisa de Python na maquina.
#
#   1. catalogo         demo/catalogo.xlsx            -> produtos
#   2. ingestao         demo/pdfs/<MARCA>/*.pdf       -> documentos (texto + tipo)
#   3. chunks           texto em trechos + embeddings -> chunks
#   4. vinculacao 0+1   codigo no nome / nome parecido -> produto_documento
#   5. vinculacao 2     LLM le o texto dos ambiguos   -> produto_documento
#   6. extracao         LLM tira pH, ativo, CAS...    -> propriedades_produto
#   7. vendedor demo    5500000000001 na whitelist
#
# A extracao vem DEPOIS da vinculacao: propriedade e de um produto, e so
# e extraida de documento ligado a exatamente um (src/process_extraction.py).
# Ficha que vale para varios produtos (o gel em 500 ML e 1 L, o
# desinfetante em tres fragrancias) e enviada, mas nao gera propriedade.
#
# Uso (na raiz do repositorio):
#     demo/carregar_demo.sh
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
# Git Bash (Windows) reescreve "/app/..." como caminho do Windows antes
# de passar ao docker. No Linux esta variavel nao tem efeito.
export MSYS_NO_PATHCONV=1

# tr: um .env editado no Windows traz \r no fim dos valores.
set -a; source <(tr -d '\r' < .env); set +a

VENDEDOR_DEMO="5500000000001"

passo() { printf '\n==> %s\n' "$*"; }
rodar() { docker compose run --rm -T -v ./demo:/app/demo:ro api python -m "$@"; }

passo "1/7 Catalogo ficticio"
rodar src.ingestion.import_excel demo/catalogo.xlsx

passo "2/7 PDFs (uma pasta por marca)"
for pasta in demo/pdfs/*/; do
    marca=$(basename "$pasta")
    rodar src.ingestion.ingest_pdfs "demo/pdfs/$marca" --marca-pasta "$marca"
done

passo "3/7 Chunks e embeddings"
rodar src.process_chunks

passo "4/7 Vinculacao: Camadas 0 e 1"
rodar src.link_documents

passo "5/7 Vinculacao: Camada 2 (LLM)"
# Na operacao real os vinculos da Camada 2 vao para conferencia humana
# antes de chegar ao bot (scripts/conferencia_llm.py). Na demo, gravam direto.
rodar src.link_llm

passo "6/7 Extracao de propriedades"
rodar src.process_extraction

passo "7/7 Vendedor da demo"
docker compose exec -T postgres psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "insert into vendedores_autorizados (telefone, nome, observacao)
     values ('$VENDEDOR_DEMO', 'Vendedor Demo', 'demo') on conflict (telefone) do nothing"

passo "Resumo"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    select p.codigo, p.descricao, coalesce(string_agg(pd.metodo_match, ', '), '-') as vinculos
    from produtos p
    left join produto_documento pd on pd.produto_codigo = p.codigo
    group by p.codigo, p.descricao order by p.codigo"

cat <<'FIM'

Pronto. Pergunte ao bot:
    demo/perguntar.sh "qual o pH da agua sanitaria limpax?"
    demo/perguntar.sh "me manda a FDS do desinfetante de eucalipto"
    demo/perguntar.sh "o que fazer se o alcool gel cair no olho?"
FIM
