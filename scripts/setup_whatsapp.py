"""
Fecha a fiação do canal WhatsApp depois que a instância da Evolution
existir.

O que sobra de manual hoje (criar a instância `bot_fichas` e parear o
número no QR Code) exige um segundo chip e a mão de alguém. Tudo o que
vem DEPOIS é mecânico, envolve três sistemas e tem um passo perigoso —
configurar webhook na Evolution, onde errar de instância silencia quem
a usava. É isso que este script automatiza.

    python -m scripts.setup_whatsapp              # mostra o plano
    python -m scripts.setup_whatsapp --aplicar    # executa

Sem --aplicar nada é alterado: o script inspeciona, valida e imprime o
que faria. O padrão é o modo seguro porque um dos passos reconfigura
um serviço compartilhado.

Idempotente: rodar de novo reconverge o estado em vez de duplicar.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENV = RAIZ / ".env"
FLUXO = RAIZ / "n8n" / "workflows" / "bot_fichas_whatsapp.json"

# --- Alvos. Tudo aqui e do compose deste projeto: desde 09/2026 a
# --- Evolution do bot e propria (bot_fichas_evolution), com database
# --- e volume so dela. A instancia segue como CONSTANTE, e nao
# --- argumento, porque webhook e configurado POR INSTANCIA: apontar a
# --- errada nao da erro, apenas silencia quem deveria receber.
INSTANCIA = "bot_fichas"

CONTAINER_EVOLUTION = "bot_fichas_evolution"
CONTAINER_EVOLUTION_DB = "bot_fichas_postgres"
CONTAINER_N8N = "bot_fichas_n8n"

EVOLUTION_DB = "evolution_bot"
# Superuser do cluster. Aqui so le a tabela Instance; a aplicacao
# continua conectando como bot_app.
EVOLUTION_DB_USER = "botuser"

# Enderecos internos: n8n, Evolution e API dividem a rede do compose,
# entao valem os nomes de SERVICO. 'localhost' dentro de um container
# e o proprio container.
EVOLUTION_URL_DO_N8N = "http://evolution:8080"
WEBHOOK_DO_N8N = "http://n8n:5678/webhook/bot-fichas"

# Visto do host, para as chamadas deste script. A porta publicada e a
# 8081 para nao disputar a 8080 com outra Evolution na maquina.
EVOLUTION_URL_DO_HOST = "http://localhost:8081"

N8N_PROJETO = "QO6IItYw8IhyMApI"
CRED_ID_API = "botFichasApiKey1"
CRED_ID_EVOLUTION = "botFichasEvo0001"
CRED_NOME_EVOLUTION = "Evolution API (apikey)"
FLUXO_ID = "botFichasWhats01"


class Falha(Exception):
    """Erro esperado, com mensagem para humano — nao stacktrace."""


def rodar(cmd: list[str], entrada: str | None = None) -> str:
    r = subprocess.run(
        cmd, input=entrada, capture_output=True, text=True, encoding="utf-8"
    )
    if r.returncode != 0:
        raise Falha(f"comando falhou: {' '.join(cmd[:4])}...\n{r.stderr.strip()}")
    return r.stdout.strip()


def psql_evolution(sql: str) -> str:
    return rodar([
        "docker", "exec", CONTAINER_EVOLUTION_DB, "psql",
        "-U", EVOLUTION_DB_USER, "-d", EVOLUTION_DB, "-tAF|", "-c", sql,
    ])


# ----------------------------------------------------------------------
# Leitura do estado atual
# ----------------------------------------------------------------------

def ler_instancia() -> dict:
    """
    Confirma que a instancia existe e devolve token e status.

    Sem ela nao ha o que configurar, e o erro precisa dizer exatamente
    o que fazer — este e o ponto onde o processo costuma travar.
    """
    linhas = psql_evolution(
        'select name, token, "connectionStatus", coalesce("ownerJid", \'\') '
        f"from \"Instance\" where name = '{INSTANCIA}'"
    )
    if not linhas:
        existentes = psql_evolution('select name from "Instance"') or "(nenhuma)"
        raise Falha(
            f"a instancia '{INSTANCIA}' nao existe na Evolution.\n"
            f"  instancias atuais: {existentes.replace(chr(10), ', ')}\n\n"
            f"  Crie no manager ({EVOLUTION_URL_DO_HOST}/manager) ou por API,\n"
            "  e pareie um numero de WhatsApp exclusivo do bot.\n"
            "  Ver n8n/README.md."
        )

    nome, token, status, dono = (linhas.split("|") + ["", "", ""])[:4]
    if not token:
        raise Falha(f"a instancia '{nome}' existe mas esta sem token.")
    return {"nome": nome, "token": token, "status": status, "dono": dono}


def ler_chave_global() -> str:
    """Chave admin da Evolution, lida do proprio container."""
    chave = rodar([
        "docker", "exec", CONTAINER_EVOLUTION,
        "printenv", "AUTHENTICATION_API_KEY",
    ])
    if not chave:
        raise Falha("AUTHENTICATION_API_KEY nao esta definida no container.")
    return chave


def webhook_atual() -> str:
    return psql_evolution(
        'select coalesce(w.url, \'(nenhum)\') from "Instance" i '
        'left join "Webhook" w on w."instanceId" = i.id '
        f"where i.name = '{INSTANCIA}'"
    ) or "(nenhum)"


# ----------------------------------------------------------------------
# Passos de escrita
# ----------------------------------------------------------------------

def atualizar_env(token: str, caminho: Path | None = None) -> list[str]:
    """
    Grava a chave da instancia e a URL no .env, preservando o resto.

    Substitui linha existente em vez de reescrever o arquivo: o .env
    tem senhas de banco e chaves que nada aqui conhece. Se a chave
    esperada nao existir, falha — inventar a linha esconderia um .env
    fora de sincronia com o .env.example.

    Returns:
        Nomes das variaveis efetivamente alteradas (vazio = ja correto).
    """
    caminho = caminho or ENV
    texto = caminho.read_text(encoding="utf-8")
    mudancas = []
    for chave, valor in [
        ("EVOLUTION_API_KEY", token),
        ("EVOLUTION_API_URL", EVOLUTION_URL_DO_HOST),
        ("EVOLUTION_INSTANCE_NAME", INSTANCIA),
    ]:
        padrao = re.compile(rf"^{chave}=.*$", re.MULTILINE)
        achado = padrao.search(texto)
        if not achado:
            raise Falha(f"{chave} nao existe no .env; nao vou inventar a linha.")
        if achado.group(0).split("=", 1)[1] != valor:
            # Lambda no lugar da string: o token pode conter \ ou \g,
            # que re.sub interpretaria como referencia de grupo.
            texto = padrao.sub(lambda _: f"{chave}={valor}", texto, count=1)
            mudancas.append(chave)
    if mudancas:
        caminho.write_text(texto, encoding="utf-8")
    return mudancas


def credencial_evolution(token: str) -> None:
    """
    Cria ou atualiza a credencial da Evolution no n8n.

    Passa pelo /tmp do container porque a CLI so aceita arquivo; o
    arquivo e removido logo depois (a chave fica cifrada no banco do
    n8n, sob a N8N_ENCRYPTION_KEY).
    """
    payload = json.dumps([{
        "id": CRED_ID_EVOLUTION,
        "name": CRED_NOME_EVOLUTION,
        "type": "httpHeaderAuth",
        "data": {"name": "apikey", "value": token},
    }])
    rodar(["docker", "exec", "-i", CONTAINER_N8N,
           "sh", "-c", "cat > /tmp/_cred.json"], entrada=payload)
    try:
        rodar(["docker", "exec", CONTAINER_N8N, "n8n", "import:credentials",
               "--input=/tmp/_cred.json", f"--projectId={N8N_PROJETO}"])
    finally:
        # -u root: o arquivo chega como root e o container roda como node.
        subprocess.run(["docker", "exec", "-u", "root", CONTAINER_N8N,
                        "rm", "-f", "/tmp/_cred.json"], capture_output=True)


def importar_fluxo() -> None:
    """Importa o fluxo do repositorio com as duas credenciais apontadas."""
    wf = json.loads(FLUXO.read_text(encoding="utf-8"))
    wf["id"] = FLUXO_ID
    for no in wf["nodes"]:
        cred = (no.get("credentials") or {}).get("httpHeaderAuth")
        if not cred:
            continue
        if no["name"] == "Pergunta ao motor":
            cred["id"] = CRED_ID_API
        elif no["name"] == "Envia resposta no WhatsApp":
            cred["id"] = CRED_ID_EVOLUTION

    rodar(["docker", "exec", "-i", CONTAINER_N8N,
           "sh", "-c", "cat > /tmp/_wf.json"],
          entrada=json.dumps([wf], ensure_ascii=False))
    try:
        rodar(["docker", "exec", CONTAINER_N8N, "n8n", "import:workflow",
               "--input=/tmp/_wf.json", f"--projectId={N8N_PROJETO}"])
    finally:
        subprocess.run(["docker", "exec", "-u", "root", CONTAINER_N8N,
                        "rm", "-f", "/tmp/_wf.json"], capture_output=True)


def apontar_webhook(chave_global: str) -> None:
    """
    Configura o webhook da instancia do bot — e SOMENTE dela.

    A URL e montada com a constante INSTANCIA, nunca com nome vindo de
    argumento ou de leitura. A Evolution v2 aceita um webhook por
    instancia e sobrescreve sem avisar: apontar a errada nao levanta
    erro, so faz as mensagens dela pararem de chegar em algum lugar.
    """
    assert INSTANCIA == "bot_fichas", "guarda: instancia alvo alterada"
    url = f"{EVOLUTION_URL_DO_HOST}/webhook/set/{INSTANCIA}"

    corpo_aninhado = {"webhook": {
        "enabled": True,
        "url": WEBHOOK_DO_N8N,
        "webhookByEvents": False,
        "webhookBase64": False,
        "events": ["MESSAGES_UPSERT"],
    }}
    # Formato antigo, usado por algumas 2.x. Tentado so se o primeiro
    # for recusado — o contrato mudou entre versoes menores.
    corpo_plano = {
        "enabled": True,
        "url": WEBHOOK_DO_N8N,
        "webhookByEvents": False,
        "webhookBase64": False,
        "events": ["MESSAGES_UPSERT"],
    }

    import httpx

    ultimo = None
    for corpo in (corpo_aninhado, corpo_plano):
        r = httpx.post(url, headers={"apikey": chave_global},
                       json=corpo, timeout=30)
        if r.status_code < 300:
            return
        ultimo = f"HTTP {r.status_code}: {r.text[:250]}"
    raise Falha(
        f"a Evolution recusou os dois formatos de webhook.\n  {ultimo}\n"
        f"  Configure pelo manager: {EVOLUTION_URL_DO_HOST}/manager"
    )


def publicar_fluxo() -> None:
    rodar(["docker", "exec", CONTAINER_N8N, "n8n", "publish:workflow",
           f"--id={FLUXO_ID}"])
    # A CLI avisa que publicacao nao vale com o n8n rodando: sem o
    # restart a rota do webhook nao e registrada.
    rodar(["docker", "restart", CONTAINER_N8N])


def esperar_n8n(tentativas: int = 40) -> bool:
    import httpx

    for _ in range(tentativas):
        try:
            if httpx.get("http://localhost:5678/healthz", timeout=3).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# ----------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--aplicar", action="store_true",
                   help="executa as mudancas (sem isso, so mostra o plano)")
    args = p.parse_args()

    print(f"Instancia alvo: {INSTANCIA}\n", flush=True)

    inst = ler_instancia()
    print(f"  instancia   : {inst['nome']}  [{inst['status']}]")
    print(f"  numero      : {inst['dono'] or '(nao pareado)'}")
    print(f"  token       : {len(inst['token'])} caracteres (nao exibido)")
    print(f"  webhook hoje: {webhook_atual()}")

    if inst["status"] != "open":
        print(
            f"\n  AVISO: status '{inst['status']}'. A instancia so recebe "
            "mensagem com status 'open'.\n         Pareie o QR Code antes "
            "de testar de verdade."
        )

    print("\nO que sera feito:")
    print(f"  1. .env: EVOLUTION_API_KEY, EVOLUTION_API_URL, EVOLUTION_INSTANCE_NAME")
    print(f"  2. n8n: credencial '{CRED_NOME_EVOLUTION}'")
    print(f"  3. n8n: importa o fluxo com as duas credenciais apontadas")
    print(f"  4. Evolution: webhook de '{INSTANCIA}' -> {WEBHOOK_DO_N8N}")
    print(f"  5. n8n: publica o fluxo e reinicia o container")

    if not args.aplicar:
        print("\n(simulacao — nada foi alterado. Use --aplicar para executar.)")
        return 0

    print("\nAplicando...")

    mudou = atualizar_env(inst["token"])
    print(f"  1. .env: {', '.join(mudou) if mudou else 'ja estava correto'}")

    credencial_evolution(inst["token"])
    print("  2. credencial da Evolution gravada no n8n")

    importar_fluxo()
    print("  3. fluxo importado")

    apontar_webhook(ler_chave_global())
    print(f"  4. webhook apontado ({webhook_atual()})")

    publicar_fluxo()
    print("  5. fluxo publicado; aguardando o n8n subir...")
    if not esperar_n8n():
        raise Falha("o n8n nao respondeu apos o restart.")

    print("\n  pronto. Mande uma mensagem de um numero cadastrado em")
    print("  vendedores_autorizados e acompanhe em http://localhost:5678")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Falha as e:
        print(f"\nERRO: {e}", file=sys.stderr)
        sys.exit(1)
