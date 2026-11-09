# Fluxo do n8n — WhatsApp

`workflows/bot_fichas_whatsapp.json` liga a Evolution API ao motor de
resposta. O n8n **transporta**: não decide quem pode consultar nem o
que responder. Autorização, idempotência e política de resposta vivem
na API, para que trocar de canal não mova as regras de lugar.

```
WhatsApp → Evolution API → [webhook] → n8n → POST /perguntar → API
                                        ↓                        ↓
                              sendText ← ─────────── resposta ← motor
```

## Endereços entre containers

Postgres, API, Evolution e n8n são todos serviços deste
`docker-compose.yml`, na mesma rede. Vale o **nome do serviço** e a
**porta interna**:

| De | Para | Endereço |
|---|---|---|
| n8n | API do bot | `http://api:8000` |
| n8n | Evolution | `http://evolution:8080` |
| Evolution | n8n (webhook) | `http://n8n:5678` |
| Você, do host | Evolution | `http://localhost:8081` |

`localhost` dentro de um container é o próprio container — não use.

A porta publicada é **8081** para não disputar a 8080 com outra
Evolution na mesma máquina. Na rede do compose a do bot continua
atendendo em 8080.

Os endereços que podem mudar estão isolados no nó `Configuracao`, no
início do fluxo.

## As duas chaves da Evolution

Não são intercambiáveis, e usar a errada dá acesso a mais do que devia:

| Chave | Escopo | Onde vive |
|---|---|---|
| `EVOLUTION_GLOBAL_KEY` | admin: cria, lista e apaga instâncias | `.env`, injetada no container |
| `EVOLUTION_API_KEY` | só a instância `bot_fichas` | `.env` e credencial do n8n |

O n8n usa a **da instância**. A global só aparece na criação da
instância e no manager.

## Setup

### Atalho: o script faz os passos 1 a 5

O único trabalho realmente manual é **criar a instância `bot_fichas` na
Evolution e parear o número no QR Code** — precisa de um chip diferente
do que já está em uso e da mão de alguém. Tudo depois disso é mecânico:

```bash
python -m scripts.setup_whatsapp
```

Mostra o plano sem alterar nada: estado da instância, webhook atual e o
que seria feito. Para executar:

```bash
python -m scripts.setup_whatsapp --aplicar
```

Ele grava a chave da instância no `.env`, cria a credencial da Evolution
no n8n, importa o fluxo com as duas credenciais apontadas, configura o
webhook **apenas da instância `bot_fichas`** e publica o fluxo. É
idempotente — rodar de novo reconverge o estado.

A instância alvo é uma constante verificada em teste, nunca argumento:
a Evolution v2 aceita **um webhook por instância** e sobrescreve sem
avisar. Apontar a errada não dá erro — só faz as mensagens dela
pararem de chegar em algum lugar.

O resto desta seção descreve os mesmos passos à mão, para quando você
quiser conferir ou fazer pela interface.

### 1. Importar o fluxo

n8n → *Workflows* → *Import from File* → `workflows/bot_fichas_whatsapp.json`.

### 2. Criar as duas credenciais

O fluxo referencia credenciais do tipo **Header Auth** pelo nome; na
importação o n8n pede para escolher cada uma. Crie antes, em
*Credentials* → *New* → *Header Auth*:

| Nome | Header | Valor |
|---|---|---|
| `Bot Fichas API (X-API-Key)` | `X-API-Key` | valor de `API_KEY` do `.env` |
| `Evolution API (apikey)` | `apikey` | token da instância — **não** a global |

Credencial em vez de variável de ambiente porque o n8n do compose não
recebe `API_KEY` nem `EVOLUTION_API_KEY` — e não deveria: a chave fica
cifrada no banco do n8n, sob a `N8N_ENCRYPTION_KEY`.

### 3. Apontar o webhook da Evolution

O caminho confiável é o manager da Evolution em
`http://localhost:8081/manager` → instância → *Webhook*:

- **URL**: `http://n8n:5678/webhook/bot-fichas`
- **Eventos**: apenas `MESSAGES_UPSERT`

Pela API, o endpoint é `POST /webhook/set/{instancia}` com header
`apikey`. O formato do corpo mudou entre versões menores da 2.x — se
a chamada direta falhar, use o manager.

> Enquanto estiver testando no editor do n8n (botão *Test workflow*), a
> URL é `/webhook-test/bot-fichas`. A de produção (`/webhook/`) só
> responde com o fluxo **ativo**.

### 4. Cadastrar quem pode usar

Número fora da whitelist recebe 403 e o fluxo não responde nada:

```sql
INSERT INTO vendedores_autorizados (telefone, nome)
VALUES ('5511999998888', 'Nome do Vendedor');
```

Formato: E.164 sem `+`, só dígitos — igual ao `remoteJid` da Evolution
sem o sufixo.

### 5. Ativar

Só depois de um teste manual pelo editor.

## O que o fluxo descarta, e por quê

O nó `Extrai a mensagem` filtra antes de gastar qualquer chamada paga.
Cada descarte registra um `motivo` legível na execução — sem isso,
depurar "o bot não respondeu" vira adivinhação.

| Situação | Motivo |
|---|---|
| `fromMe: true` | é o eco da própria resposta do bot; sem o corte ele responde a si mesmo, em loop |
| `@g.us` | grupo: o jid não é telefone e a whitelist é por telefone |
| `status@broadcast` | não é conversa |
| áudio, imagem, figurinha | não há texto para perguntar |
| telefone fora de 10–20 dígitos | seria 422 na API |
| mensagem sem `id` | sem chave de dedupe, um reenvio viraria resposta duplicada |

Depois da chamada ao motor, também não envia nada quando:

- `duplicada: true` — webhook reenviado; a primeira cópia já foi respondida;
- **403** — remetente fora da whitelist. Responder confirmaria a um
  estranho que o número é um bot. Se no piloto você preferir avisar,
  ligue um `sendText` na saída `Sem resposta a enviar`.

Mensagem acima de 1000 caracteres é truncada, não rejeitada: o
vendedor recebe resposta em vez de silêncio.

## Testar o nó de extração

A lógica de descarte é a parte com mais risco, e roda em JavaScript.
`workflows/testa_extracao.mjs` exercita os 13 casos acima contra o
próprio JSON do fluxo. Não precisa de Node instalado no host — use o
container do n8n:

```bash
docker cp n8n/workflows/testa_extracao.mjs bot_fichas_n8n:/tmp/t.mjs && docker exec bot_fichas_n8n node /tmp/t.mjs
```

A estrutura do fluxo (conexões, referências entre nós, credenciais)
é validada pela suíte Python, em `tests/test_n8n_workflow.py` — renomear
um nó quebra as expressões silenciosamente, e o teste pega isso.

## Pendências conhecidas

- **A instância `bot_fichas` ainda não existe.** A Evolution do bot já
  sobe pelo compose, com database e volume próprios, mas criar a
  instância e parear o QR Code exige um número de WhatsApp exclusivo e
  a mão de alguém. É o único passo manual que resta.
- **Documentos vão como link no texto, não como anexo.** O campo
  `documentos` da resposta traz UUIDs, e anexar exigiria resolvê-los
  para URL antes do `sendMedia`. Os links do Drive já vão no corpo da
  mensagem.
- **Sem rate limiting.** Um vendedor autorizado pode disparar chamadas
  ilimitadas à OpenAI.
