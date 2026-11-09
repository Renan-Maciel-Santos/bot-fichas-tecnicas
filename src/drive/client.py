"""
Cliente Google Drive: autenticacao, travessia recursiva e download.

Camada de I/O pura. NAO conhece o `DocumentoFonte` do pipeline de
ingestao — apenas fala protocolo Drive. A traducao para o vocabulario
do dominio mora em `src/drive/fonte.py`.

Escopo: `drive.readonly` — a Service Account so consegue LER arquivos e
listar pastas onde foi compartilhada. Nao pode escrever, deletar ou
alterar metadados. Defesa em profundidade contra bug ou credencial
vazada.
"""

import io
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from src.config import GOOGLE_SERVICE_ACCOUNT_JSON

logger = logging.getLogger(__name__)

# Escopo minimo suficiente para o sync: listar pastas e baixar arquivos.
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Tipo MIME das pastas do Google Drive (sentinela para a recursao).
MIMETYPE_FOLDER = "application/vnd.google-apps.folder"
MIMETYPE_PDF = "application/pdf"

# Campos retornados pelo files().list() — pedidos explicitamente para
# nao trafegar dados desnecessarios. `modifiedTime` alimenta o Nivel 1
# do sync (comparacao barata sem download).
FIELDS_ARQUIVO = "id, name, mimeType, modifiedTime, parents, webViewLink"

# Quantidade maxima de arquivos por pagina na API.
PAGE_SIZE = 100


@dataclass(frozen=True)
class ArquivoDrive:
    """Metadados brutos de um PDF descoberto na travessia do Drive.

    Nao ha download aqui — apenas o descritor. O download acontece so
    quando o adapter `fonte.py` precisa entregar o PDF ao pipeline.
    """

    file_id: str
    nome: str
    modificado_em: datetime
    marca_pasta: str  # nome da pasta de 1o nivel onde o arquivo esta
    url_web: str  # link direto para visualizar no Drive


def _construir_service() -> Resource:
    """Autentica via Service Account e devolve o cliente da Drive API v3.

    A credencial JSON e lida do path configurado em
    `config.GOOGLE_SERVICE_ACCOUNT_JSON`. Se o arquivo nao existir ou
    for invalido, a excecao explode aqui — falha rapida e clara.
    """
    caminho_credencial = Path(GOOGLE_SERVICE_ACCOUNT_JSON)
    if not caminho_credencial.is_file():
        raise FileNotFoundError(
            f"Credencial da Service Account nao encontrada: {caminho_credencial}. "
            f"Verifique GOOGLE_SERVICE_ACCOUNT_JSON no .env ou coloque o arquivo "
            f"em credentials/google-service-account.json."
        )

    credentials = service_account.Credentials.from_service_account_file(
        str(caminho_credencial),
        scopes=SCOPES,
    )
    # cache_discovery=False evita warning em ambientes sem oauth2client;
    # a performance perdida e desprezivel em runs unicos.
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _listar_filhos(service: Resource, folder_id: str) -> list[dict]:
    """Lista todos os itens filhos diretos de uma pasta, com paginacao.

    Retorna dicts crus da API. A conversao para `ArquivoDrive` acontece
    so na travessia principal, ja com o contexto da marca_pasta.
    """
    itens: list[dict] = []
    page_token: str | None = None
    query = f"'{folder_id}' in parents and trashed = false"

    while True:
        resposta = service.files().list(
            q=query,
            spaces="drive",
            fields=f"nextPageToken, files({FIELDS_ARQUIVO})",
            pageSize=PAGE_SIZE,
            pageToken=page_token,
        ).execute()

        itens.extend(resposta.get("files", []))
        page_token = resposta.get("nextPageToken")
        if not page_token:
            break

    return itens


def _obter_nome_pasta(service: Resource, folder_id: str) -> str:
    """Recupera o nome de uma pasta pelo id.

    Necessario para preencher `marca_pasta` no `ArquivoDrive`. Se der
    404 (id invalido ou sem acesso), retorna o proprio id como fallback
    seguro — melhor um nome estranho que uma falha silenciosa.
    """
    try:
        resposta = service.files().get(
            fileId=folder_id, fields="name"
        ).execute()
        return resposta.get("name", folder_id)
    except HttpError as exc:
        logger.warning(
            "Nao consegui ler o nome da pasta %s: %s. Usando o id.",
            folder_id, exc,
        )
        return folder_id


def listar_pdfs_recursivo(folder_id_raiz: str) -> list[ArquivoDrive]:
    """Percorre a arvore a partir de uma pasta raiz e lista todos os PDFs.

    Convencao do dominio: pastas de 1o nivel = marcas. O `marca_pasta`
    do ArquivoDrive e sempre o nome dessa pasta de 1o nivel, mesmo que
    o PDF esteja em subpastas mais profundas.

    Args:
        folder_id_raiz: id da pasta que contem as subpastas de marca.

    Returns:
        Lista de ArquivoDrive com todos os PDFs descobertos. Vazia se
        a pasta nao tiver PDFs (ou se estiver vazia mesmo).

    Raises:
        HttpError: se a raiz nao existir ou nao tiver sido compartilhada
            com a Service Account.
    """
    service = _construir_service()
    pdfs: list[ArquivoDrive] = []

    marcas = _listar_filhos(service, folder_id_raiz)
    marcas_pastas = [m for m in marcas if m["mimeType"] == MIMETYPE_FOLDER]

    logger.info(
        "Descobertas %d pastas de marca em %s",
        len(marcas_pastas), folder_id_raiz,
    )

    for marca in marcas_pastas:
        nome_marca = marca["name"]
        pdfs.extend(
            _descer_arvore(service, marca["id"], nome_marca)
        )

    logger.info("Total de PDFs descobertos: %d", len(pdfs))
    return pdfs


def _descer_arvore(
    service: Resource,
    folder_id: str,
    marca_pasta: str,
) -> list[ArquivoDrive]:
    """Percorre uma subarvore em profundidade, coletando os PDFs.

    Recursao explicita (nao usa pilha propria) — a profundidade tipica
    do Drive de catalogo e baixa (2-3 niveis), sem risco de stack
    overflow.
    """
    pdfs: list[ArquivoDrive] = []
    filhos = _listar_filhos(service, folder_id)

    for item in filhos:
        mime = item["mimeType"]
        if mime == MIMETYPE_FOLDER:
            pdfs.extend(_descer_arvore(service, item["id"], marca_pasta))
        elif mime == MIMETYPE_PDF:
            pdfs.append(_construir_arquivo_drive(item, marca_pasta))
        # Outros tipos (docx, imagens, etc): ignorados silenciosamente.

    return pdfs


def _construir_arquivo_drive(item: dict, marca_pasta: str) -> ArquivoDrive:
    """Converte um dict cru da API em ArquivoDrive tipado."""
    # modifiedTime vem em ISO 8601 UTC. `fromisoformat` aceita direto
    # a partir do Python 3.11 (voce esta em 3.12).
    modificado = datetime.fromisoformat(
        item["modifiedTime"].replace("Z", "+00:00")
    )
    return ArquivoDrive(
        file_id=item["id"],
        nome=item["name"],
        modificado_em=modificado,
        marca_pasta=marca_pasta,
        url_web=item.get("webViewLink", ""),
    )


def baixar_arquivo(file_id: str, destino: Path) -> None:
    """Baixa um arquivo do Drive para o path indicado.

    O caller e responsavel por limpar o arquivo apos o uso. Uso tipico:
    tempfile.NamedTemporaryFile ou tempfile.mkdtemp com cleanup pos-run.

    Args:
        file_id: id opaco do Drive.
        destino: caminho final do arquivo em disco. Diretorio pai deve
            existir (o download nao cria pastas).
    """
    service = _construir_service()
    request = service.files().get_media(fileId=file_id)

    with destino.open("wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    logger.debug("Baixado %s -> %s", file_id, destino)