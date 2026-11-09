# Deploy numa VPS

Do zero até o bot respondendo no WhatsApp, a partir do repositório no
GitHub. Os passos 1 a 5 levam uns 20 minutos; o resto depende do chip
do bot estar em mãos.

O roteiro foi ensaiado do começo ao fim numa máquina limpa (clone novo,
sem volumes), nos dois caminhos: com a carga do acervo e sem ela.

## O que vai rodar

Os quatro serviços do `docker-compose.yml`, iguais ao ambiente local:
Postgres, API, Evolution e n8n. **Nenhuma porta fica aberta para a
internet**: todas são publicadas só no loopback da VPS. O WhatsApp não
precisa de porta de entrada — a Evolution conecta para fora — e a
Evolution fala com o n8n, e o n8n com a API, pela rede interna do
compose. Os painéis (n8n e Evolution) são abertos por túnel SSH.

## Requisitos

| | Mínimo | Recomendado |
|---|---|---|
| CPU | 1 vCPU | 2 vCPU |
| RAM | 2 GB + swap | 4 GB |
| Disco | 25 GB | 40 GB |
| Sistema | Ubuntu 22.04 ou 24.04 | |

Medido em 10/2026: os quatro serviços parados usam ~570 MB de RAM; as
imagens somam ~6 GB; o acervo, ~80 MB no banco.

Você vai precisar também de:

- a `OPENAI_API_KEY` (o motor usa OpenAI);
- o dump do acervo, gerado na máquina que tem os dados hoje (passo 4);
- um **chip só para o bot**, com WhatsApp instalado num celular (passo 8).

---

## 1. Preparar a VPS

Como root, num servidor novo:

```bash
adduser bot
usermod -aG sudo bot

# Firewall: só SSH. Nada mais precisa entrar.
ufw allow OpenSSH
ufw enable

# Docker (script oficial) e o usuário no grupo docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker bot
```

Saia e entre de novo como `bot` (o grupo `docker` só vale numa sessão
nova). Confira:

```bash
docker compose version
```

> **Docker e ufw:** o Docker escreve regras de iptables que passam por
> cima do ufw. Por isso as portas do compose são publicadas em
> `127.0.0.1` — o firewall sozinho não as protegeria. Não troque por
> `0.0.0.0`.

## 2. Acesso ao repositório

O repositório é privado. Use uma **deploy key** (só leitura, só este
repositório) em vez da sua senha ou token pessoal:

```bash
ssh-keygen -t ed25519 -C "vps-bot-fichas" -f ~/.ssh/github_bot -N ""
cat ~/.ssh/github_bot.pub
```

No GitHub: repositório → *Settings* → *Deploy keys* → *Add deploy key*,
cole a chave pública e **não** marque *Allow write access*.

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
    IdentityFile ~/.ssh/github_bot
EOF

git clone git@github.com:Renan-Maciel-Santos/bot-fichas-tecnicas.git
cd projeto-bot
```

## 3. Configurar o `.env`

```bash
deploy/gerar_env.sh
nano .env          # preencha OPENAI_API_KEY
```

O script gera todas as senhas e chaves. Só a `OPENAI_API_KEY` é com
você. **Guarde uma cópia do `.env` no cofre de senhas**: a
`N8N_ENCRYPTION_KEY` e as senhas do banco não se recuperam, e os
backups só restauram com elas.

## 4. Levar o acervo

Na máquina que tem os dados hoje (Git Bash no Windows serve):

```bash
deploy/exportar_dados.sh
scp backups/bot_fichas_carga_AAAAMMDD_HHMM.dump bot@IP_DA_VPS:~/projeto-bot/backups/
```

O dump leva produtos, documentos com o texto extraído, chunks com
embeddings, propriedades e vínculos — inclusive os revisados pelo
cliente. Deixa de fora o histórico de consultas e a lista de vendedores
autorizados, que são do ambiente. Tem dados do cliente: não versione e
não mande por canal aberto.

> Sem dump, o `instalar.sh` cria o schema vazio pelas migrações, e o
> acervo teria de ser ingerido do Drive na VPS.

## 5. Instalar

Na VPS:

```bash
deploy/instalar.sh backups/bot_fichas_carga_AAAAMMDD_HHMM.dump
```

Sobe o Postgres, cria databases e roles, carrega o acervo e sobe API,
Evolution e n8n. Termina com um resumo (produtos, documentos, vínculos)
e o estado dos containers. Leva ~2 minutos mais o download das imagens.

Pode rodar de novo sem medo: cada etapa confere o estado antes de agir.

## 6. Abrir os painéis

Do seu computador, abra um túnel e deixe o terminal aberto:

```bash
ssh -L 5678:localhost:5678 -L 8081:localhost:8081 bot@IP_DA_VPS
```

Agora `http://localhost:5678` é o n8n da VPS e
`http://localhost:8081/manager` é a Evolution.

**n8n:** na primeira vez ele pede para criar a conta de owner. Crie com
um e-mail e senha fortes — é a única proteção do painel.

## 7. Criar a instância do WhatsApp

Em `http://localhost:8081/manager`, entre com a `EVOLUTION_GLOBAL_KEY`
do `.env` e crie uma instância chamada **`bot_fichas`** (o nome tem de
ser exatamente esse — o `setup_whatsapp` e o fluxo do n8n dependem
dele).

## 8. Parear o número

No manager, abra a instância e gere o QR Code. No celular com o chip do
bot: WhatsApp → *Aparelhos conectados* → *Conectar aparelho*. O estado
da instância deve virar `open`.

## 9. Ligar Evolution → n8n → API

Na VPS:

```bash
python3 -m scripts.setup_whatsapp            # mostra o plano
python3 -m scripts.setup_whatsapp --aplicar  # executa
```

Grava o token da instância no `.env`, cria a credencial no n8n, importa
o fluxo, aponta o webhook da Evolution e publica o fluxo. Só usa a
biblioteca padrão do Python — o `python3` do Ubuntu basta.

## 10. Autorizar os vendedores

Só números cadastrados recebem resposta (formato `55` + DDD + número):

```bash
docker compose exec postgres psql -U botuser -d bot_fichas -c \
  "insert into vendedores_autorizados (telefone, nome, observacao) values ('5511999998888', 'Nome do Vendedor', 'piloto');"
```

## 11. Testar

De um número autorizado, mande para o número do bot:

> me manda a FDS da água sanitária Limpax

Se não voltar nada, siga *"O vendedor mandou mensagem e não voltou
nada"* no `docs/RUNBOOK_OPERACAO.md`.

## 12. Backup diário

```bash
crontab -e
```

```
15 3 * * * cd /home/bot/projeto-bot && deploy/backup.sh >> backups/backup.log 2>&1
```

Grava os três databases em `backups/` e apaga os de mais de 14 dias
(o dump da carga inicial nunca é apagado). Os backups ficam **na própria
VPS**: copie-os para fora de tempos em tempos, ou eles somem junto com
ela.

---

## Atualizar para uma versão nova

```bash
cd ~/projeto-bot
git pull
docker compose up -d --build
```

Se a versão nova trouxer arquivo em `db/migrations/`, aplique-o antes
de subir a API:

```bash
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U botuser -d bot_fichas < db/migrations/009_exemplo.sql
```

## Restaurar um backup

Com o **mesmo** `.env` da instalação original:

```bash
docker compose stop api n8n evolution
docker compose cp backups/bot_fichas_AAAAMMDD_HHMM.dump postgres:/tmp/r.dump
docker compose exec postgres pg_restore -U botuser -d bot_fichas --clean --if-exists --no-owner /tmp/r.dump
docker compose up -d
```

O mesmo vale para `n8n_bot` e `evolution_bot`, trocando o arquivo e o
`-d`.
