# Makefile — Bot Fichas Técnicas
# Comandos de conveniência para tarefas frequentes.
# Uso: `make up`, `make psql`, etc.

.PHONY: help up down restart logs ps psql shell-n8n reset import-excel \
        migrations backup install venv test clean

# ----- Mostrar ajuda -----
help:
	@echo "Comandos disponíveis:"
	@echo "  make up            - Sobe os containers (Postgres + n8n)"
	@echo "  make down          - Para os containers"
	@echo "  make restart       - Reinicia tudo"
	@echo "  make logs          - Mostra logs em tempo real"
	@echo "  make ps            - Lista containers em execução"
	@echo "  make psql          - Abre psql conectado no banco"
	@echo "  make shell-n8n     - Shell dentro do container do n8n"
	@echo "  make backup        - Dump do banco da aplicação em backups/"
	@echo "  make reset         - APAGA TUDO sem desfazer (pede confirmação)"
	@echo "  make migrations    - Aplica db/migrations/*.sql no banco"
	@echo "  make import-excel  - Importa data/Produtos.xlsx para o banco"
	@echo "  make venv          - Cria virtualenv local em .venv (POSIX)"
	@echo "  make install       - Instala dependências Python no .venv"
	@echo "  make test          - Roda a suíte de testes"
	@echo "  make clean         - Remove caches Python"

# ----- Docker -----
up:
	docker compose up -d
	@echo "✓ Postgres em localhost:5433"
	@echo "✓ API em http://localhost:8000 (healthcheck: /health)"
	@echo "✓ n8n em http://localhost:5678"

down:
	docker compose down

restart: down up

logs:
	docker compose logs -f

ps:
	docker compose ps

# ----- Banco -----
psql:
	docker compose exec postgres psql -U $${POSTGRES_USER:-botuser} -d $${POSTGRES_DB:-bot_fichas}

# Aplicar migrations manualmente (útil em reset)
migrations:
	@for f in db/migrations/*.sql; do \
		echo "Aplicando $$f..."; \
		docker compose exec -T postgres psql -U $${POSTGRES_USER:-botuser} -d $${POSTGRES_DB:-bot_fichas} -f /migrations/$$(basename $$f); \
	done

# ----- n8n -----
shell-n8n:
	docker compose exec n8n sh

# ----- Backup -----
# Dump do database da aplicação (não inclui n8n_bot nem evolution_bot,
# que se reconstroem: workflows estão em n8n/workflows/ e a Evolution
# só precisa de novo pareamento).
backup:
	@mkdir -p backups
	@arquivo="backups/bot_fichas_$$(date +%Y%m%d_%H%M).dump"; \
	docker compose exec -T postgres pg_dump -U $${POSTGRES_USER:-botuser} \
		-d $${POSTGRES_DB:-bot_fichas} -Fc > "$$arquivo" && \
	echo "✓ $$arquivo ($$(du -h "$$arquivo" | cut -f1))" && \
	echo "  Restore: ver docs/RUNBOOK_OPERACAO.md"

# ----- DESTRUTIVO -----
# `docker compose down -v` apaga os VOLUMES, não só os containers.
# O aviso antigo dizia apenas "apaga tudo e recomeça", o que soa como
# voltar ao estado inicial limpo. Não é: o que se perde levou meses de
# ingestão, custou chamadas de LLM e não volta sozinho.
reset:
	@echo ""
	@echo "  ATENÇÃO — isto APAGA e NÃO tem desfazer:"
	@echo ""
	@echo "    • todos os documentos, chunks e embeddings já indexados"
	@echo "      (reconstruir = reingerir o Drive + pagar extração de novo)"
	@echo "    • propriedades extraídas e vínculos produto-documento"
	@echo "    • o log de consultas (métricas do piloto)"
	@echo "    • workflows, credenciais e execuções do n8n"
	@echo "    • o pareamento do WhatsApp (precisa ler o QR Code de novo)"
	@echo ""
	@echo "  E o sistema NÃO sobe funcionando depois: databases e roles"
	@echo "  precisam ser reprovisionados à mão (db/provisioning/README.md)"
	@echo "  antes de as migrations rodarem."
	@echo ""
	@echo "  Faça 'make backup' antes. Sério."
	@echo ""
	@read -p '  Digite APAGAR TUDO para confirmar: ' resposta; \
	if [ "$$resposta" != "APAGAR TUDO" ]; then \
		echo "  Cancelado — nada foi alterado."; \
		exit 1; \
	fi
	docker compose down -v
	docker compose up -d
	@echo ""
	@echo "  Containers no ar, banco VAZIO. Próximos passos:"
	@echo "    1. db/provisioning/README.md  (databases e roles)"
	@echo "    2. make migrations"
	@echo "    3. make import-excel"

# ----- Python -----
# Nota: no Windows use PowerShell diretamente —
#   python -m venv .venv; .\.venv\Scripts\Activate.ps1
# Este alvo assume shell POSIX.
venv:
	python3 -m venv .venv
	@echo "Ative com: source .venv/bin/activate"

install:
	pip install -r requirements.txt -r requirements-dev.txt

test:
	pytest

import-excel:
	python -m src.ingestion.import_excel

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
