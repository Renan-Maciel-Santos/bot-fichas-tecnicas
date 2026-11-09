# Runbook de operação — Bot Fichas Técnicas

Para resolver coisa na mão quando algo não vai bem. Organizado por
**sintoma**, não por componente: quem chega aqui sabe o que quebrou do
lado de fora, não onde está a causa.

Os runbooks em `RUNBOOK_N8N_BOT_FICHAS.md` são outra coisa — migrações
de uma vez só, já executadas. Este é o do dia a dia.

> **`make` não está instalado nesta máquina.** Todos os comandos aqui
> são `docker` / `docker compose` diretos. Os alvos do `Makefile`
> continuam válidos como documentação de intenção, mas não rodam aqui.

---

## O mapa

Tudo do bot está no `docker-compose.yml` deste repositório, na rede
`projeto-bot_default`:

| Serviço | Container | Porta (host) | Onde grava |
|---|---|---|---|
| postgres | `bot_fichas_postgres` | **5433** | volume `postgres_data` |
| api | `bot_fichas_api` | **8001** | — |
| evolution | `bot_fichas_evolution` | **8081** | `evolution_bot` + volume de sessão |
| n8n | `bot_fichas_n8n` | 5678 | `n8n_bot` + volume |

**Entre containers vale o nome do serviço e a porta interna:**
`api:8000`, `evolution:8080`, `n8n:5678`, `postgres:5432`. `localhost`
dentro de um container é o próprio container.

**As portas do host são deslocadas** (5433, 8001, 8081) para conviver
com outros Postgres/APIs/Evolutions na mesma máquina. Antes de
diagnosticar, olhe o `docker ps` inteiro, não só o `docker compose ps`.

Visão geral rápida:

```bash
docker compose ps
```

---

# Diagnóstico por sintoma

## "O vendedor mandou mensagem e não voltou nada"

Siga na ordem — cada passo elimina uma camada.

**1. A mensagem chegou no n8n?**

```bash
docker exec bot_fichas_postgres psql -U botuser -d n8n_bot -tAF' | ' -c "select id, status, \"startedAt\" from execution_entity order by id desc limit 5;"
```

- **Nenhuma execução recente** → o webhook não chegou. O fluxo está
  publicado? A Evolution está apontada para ele? Ver
  *"O webhook não chega no n8n"*.
- **Execução com status `error`** → abra em `http://localhost:5678`,
  aba *Executions*, e veja em que nó parou.
- **Execução `success` mas sem resposta** → siga para o passo 2. É o
  caso mais comum, e quase sempre é descarte deliberado.

**2. O fluxo descartou de propósito?**

Abra a execução no n8n. Se ela terminou no nó **`Ignorada`**, o campo
`motivo` diz por quê. Os descartes normais estão em `n8n/README.md` —
eco do próprio bot, grupo, áudio sem texto, telefone malformado.

Se terminou em **`Sem resposta a enviar`**, foram dois casos possíveis:

- `duplicada: true` — webhook reenviado, a primeira cópia já respondeu;
- **403** — o remetente não está na whitelist. Ver *"Cadastrar um
  vendedor"*.

**3. O motor registrou a consulta?**

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -tAF' | ' -c "select to_char(created_at,'DD/MM HH24:MI'), canal, intencao_detectada, left(pergunta,40), tempo_resposta_ms, erro from consultas order by created_at desc limit 5;"
```

- **`intencao_detectada = 'erro'`** com a coluna `erro` preenchida → o
  motor falhou. A API já liberou o `message_id`, então o reenvio da
  Evolution é uma tentativa real. Veja o traceback:

```bash
docker compose logs api --tail 100
```

- **Nada registrado** → a requisição não chegou na API. Confirme:

```bash
docker exec bot_fichas_n8n wget -qO- http://api:8000/health
```

## "O bot respondeu, mas a resposta está errada"

O motor nunca responde sem fonte. Rastreie da consulta até o documento:

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -c "select pergunta, intencao_detectada, produtos_referenciados, documentos_enviados from consultas order by created_at desc limit 1;"
```

Com o UUID de `documentos_enviados`, veja de onde veio:

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -c "select nome_arquivo, tipo, marca_pasta, url_drive, is_ocr from documentos where id = 'COLE_O_UUID_AQUI';"
```

`is_ocr = true` explica boa parte das respostas estranhas: o texto veio
de OCR e pode estar corrompido.

Para valores contraditórios (o produto tem dois pH diferentes), veja a
procedência de cada um:

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -c "select pp.campo, pp.valor, pp.confianca, d.nome_arquivo from propriedades_produto pp left join documentos d on d.id = pp.documento_id where pp.produto_codigo = 9000001 order by pp.campo, pp.confianca desc;"
```

Isso é dívida de dados conhecida, não defeito do motor: ver *Pendências*
no fim.

## "O bot não acha um produto que existe"

O produto precisa estar **ativo** e ter `texto_busca` preenchido — é o
que alimenta a busca por similaridade.

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -c "select codigo, descricao, marca, ativo, texto_busca is not null as tem_busca from produtos where descricao ilike '%agua sanitaria%' limit 10;"
```

Se o produto existe mas não tem documento vinculado, o bot só responde
o que estiver em `propriedades_produto`:

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -c "select count(*) from produto_documento where produto_codigo = 9000001;"
```

## "O webhook não chega no n8n"

**O fluxo está publicado?**

```bash
docker exec bot_fichas_postgres psql -U botuser -d n8n_bot -tAF' | ' -c "select id, name, active from workflow_entity;"
```

`active = f` → publique e **reinicie** (a CLI avisa que publicação não
vale com o n8n rodando):

```bash
docker exec bot_fichas_n8n n8n publish:workflow --id=botFichasWhats01 && docker restart bot_fichas_n8n
```

**A Evolution está apontada?**

```bash
docker exec bot_fichas_postgres psql -U botuser -d evolution_bot -tAF' | ' -c "select i.name, w.url, w.enabled from \"Instance\" i left join \"Webhook\" w on w.\"instanceId\" = i.id;"
```

A URL correta é `http://n8n:5678/webhook/bot-fichas` — nome de serviço,
porque quem resolve é o container da Evolution.

**A rota responde?** 404 significa fluxo despublicado:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5678/webhook/bot-fichas -H "Content-Type: application/json" -d "{}"
```

## "A API não sobe ou responde 503"

```bash
curl -s http://localhost:8001/health
```

- `{"status":"degradado","banco":false}` → a API está viva mas não
  alcança o Postgres. Confira se o banco está saudável
  (`docker compose ps`) e se `BOT_APP_PASSWORD` no `.env` bate com a
  senha da role.
- **503 em `/perguntar`** com header correto → `API_KEY` está vazia no
  ambiente do container. Ausência de configuração fecha a API de
  propósito. Corrija o `.env` e recrie:

```bash
docker compose up -d --force-recreate api
```

## "O WhatsApp desconectou"

```bash
docker exec bot_fichas_postgres psql -U botuser -d evolution_bot -tAF' | ' -c "select name, \"connectionStatus\", \"ownerJid\" from \"Instance\";"
```

Só `connectionStatus = open` recebe mensagem. Se caiu, reconecte pelo
manager em `http://localhost:8081/manager` (login com a
`EVOLUTION_GLOBAL_KEY` do `.env`) e leia o QR Code de novo.

**Não apague e recrie a instância** para reconectar: o token muda, e aí
a credencial do n8n e o `EVOLUTION_API_KEY` do `.env` param de valer.
Se precisar mesmo recriar, rode depois:

```bash
python -m scripts.setup_whatsapp --aplicar
```

---

# Tarefas de operação

## Cadastrar um vendedor

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -c "insert into vendedores_autorizados (telefone, nome, observacao) values ('5511999998888', 'Nome do Vendedor', 'piloto');"
```

Formato: E.164 sem `+`, só dígitos — igual ao `remoteJid` da Evolution
sem o `@s.whatsapp.net`. **Formato errado não dá erro visível**: o
vendedor simplesmente não recebe resposta.

## Desativar um vendedor

Soft delete, para o histórico em `consultas` continuar fazendo sentido:

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -c "update vendedores_autorizados set ativo = false, atualizado_em = now() where telefone = '5511999998888';"
```

## Ver o uso real do piloto

`canal` é o que separa uso real de teste:

- `whatsapp` — vendedor de verdade
- `terminal` — `scripts/chat.py` e testes
- `eval` — `scripts/eval_motor.py`

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -c "select date(created_at) as dia, intencao_detectada, count(*) from consultas where canal = 'whatsapp' group by 1, 2 order by 1 desc, 3 desc;"
```

Perguntas que não acharam resposta — a lista mais útil do piloto,
porque mostra a lacuna da base:

```bash
docker exec bot_fichas_postgres psql -U botuser -d bot_fichas -c "select pergunta, created_at from consultas where canal = 'whatsapp' and resposta like 'Não encontrei%' order by created_at desc limit 20;"
```

## Backup

```bash
docker compose exec -T postgres pg_dump -U botuser -d bot_fichas -Fc > backups/bot_fichas_$(date +%Y%m%d_%H%M).dump
```

No PowerShell:

```bash
docker compose exec -T postgres pg_dump -U botuser -d bot_fichas -Fc > "backups\bot_fichas_$(Get-Date -Format yyyyMMdd_HHmm).dump"
```

Cobre só `bot_fichas`. `n8n_bot` e `evolution_bot` ficam de fora de
propósito: os workflows estão versionados em `n8n/workflows/` e a
Evolution só precisa de novo pareamento. O que não se reconstrói sem
custo é o acervo — documentos, chunks com embedding, propriedades.

Dumps não vão para o Git (`.gitignore`). Guarde fora da máquina.

## Restore

**Testado em 08/09/2026**: 23 MB, 0 erros, 613 documentos e 3026
embeddings íntegros, busca vetorial funcionando.

A pegadinha: as **extensões precisam existir antes** do `pg_restore`.
Sem `vector`, o tipo da coluna `embedding` é desconhecido e a tabela
`chunks` falha inteira.

Restaure primeiro num database descartável, e só depois decida:

```bash
docker exec bot_fichas_postgres psql -U botuser -d postgres -c "CREATE DATABASE restore_teste;"
```

```bash
docker exec bot_fichas_postgres psql -U botuser -d restore_teste -c 'CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS unaccent; CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
```

```bash
docker cp backups/SEU_ARQUIVO.dump bot_fichas_postgres:/tmp/r.dump
```

```bash
docker exec bot_fichas_postgres pg_restore -U botuser -d restore_teste --no-owner --no-privileges /tmp/r.dump
```

Confira antes de confiar:

```bash
docker exec bot_fichas_postgres psql -U botuser -d restore_teste -c "select count(*) from documentos union all select count(*) from chunks where embedding is not null;"
```

Descarte o teste:

```bash
docker exec bot_fichas_postgres psql -U botuser -d postgres -c "DROP DATABASE restore_teste;"
```

## Reiniciar um serviço

```bash
docker compose restart api
```

Depois de mexer no `.env`, `restart` **não** basta — variáveis de
ambiente só são relidas ao recriar o container:

```bash
docker compose up -d --force-recreate api
```

O n8n precisa de restart depois de publicar ou despublicar fluxo pela
CLI.

## Rotacionar chaves

| Chave | Onde muda | O que quebra junto |
|---|---|---|
| `API_KEY` | `.env` + credencial do n8n | recriar container da API; sem a credencial nova, o fluxo passa a receber 401 |
| `EVOLUTION_API_KEY` | token da instância | recriar a credencial do n8n |
| `EVOLUTION_GLOBAL_KEY` | `.env` | recriar container da Evolution; só afeta manager e criação de instância |
| `N8N_ENCRYPTION_KEY` | **não rotacione** | inutiliza TODAS as credenciais gravadas no n8n, sem recuperação |

Depois de trocar `API_KEY` ou o token da Evolution:

```bash
python -m scripts.setup_whatsapp --aplicar
```

## Reingestão do Drive

Precisa de `GOOGLE_DRIVE_FOLDER_ID` no `.env` (hoje vazio) e da service
account em `credentials/`. Faça `make backup` antes — a ingestão faz
upsert, mas erro de configuração pode gerar duplicatas.

```bash
python -m scripts.ingest_from_drive --dry-run
```

---

# O que NÃO fazer

**Não rode `docker compose down -v`.** Apaga os volumes: acervo
inteiro, workflows do n8n e pareamento do WhatsApp. E o sistema não
sobe funcionando depois — databases e roles precisam ser reprovisionados
à mão antes de as migrations rodarem. É o mesmo que `make reset`, que
hoje pede confirmação digitada justamente por isso.

**Não aponte o webhook de outra instância para o bot.** A Evolution v2
aceita um webhook por instância e sobrescreve sem avisar: repontar a
instância errada silencia quem a usava, sem erro nenhum.

**Não rotacione a `N8N_ENCRYPTION_KEY`.** Sem ela, as credenciais
gravadas no n8n viram lixo cifrado e o container entra em loop com
"Mismatching encryption keys".

**Não conecte a aplicação como `botuser`.** O `bot_app` é sem DDL e sem
DELETE no acervo de propósito — é o que impede um bug de apagar
documento. Scripts de manutenção usam `DATABASE_URL_ADMIN`
explicitamente.

---

# Pendências que aparecem na operação

- **Cobertura documental de 2,7%** — 76 de 2767 produtos têm documento.
  Pergunta sobre produto fora dessa faixa devolve "não encontrei", e
  isso é a base, não o motor.
- **125 pares (produto, campo) com valores conflitantes** em 38
  produtos. O bot lista os dois. Falta regra de desempate.
- **22 documentos sem `url_drive`** — o bot cita, mas não tem link para
  enviar. A resposta declara isso em vez de mostrar linha truncada.
- **Sem rate limiting** — vendedor autorizado pode disparar chamadas
  ilimitadas à OpenAI.
- **`tokens_input`, `tokens_output`, `custo_estimado_usd` e `feedback`
  em `consultas` nunca são preenchidos.** Não monte relatório de custo
  sobre eles.
