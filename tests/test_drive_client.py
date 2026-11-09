"""
Testes unitarios de src/drive/client.py.

A API do Google Drive e mockada — este arquivo NAO faz chamada real.
O objetivo e validar a logica de travessia recursiva, parse de metadados
e filtros por mimeType. A integracao real (contra Drive de verdade)
sera validada quando rodarmos a ingestao completa com o ID do amigo.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.drive.client import (
    MIMETYPE_FOLDER,
    MIMETYPE_PDF,
    ArquivoDrive,
    _construir_arquivo_drive,
    listar_pdfs_recursivo,
)


# --- Helpers para construir mocks da API do Drive --------------------

def _resposta_lista(files: list[dict], next_page_token: str | None = None) -> dict:
    """Formato do retorno de service.files().list().execute()."""
    resp = {"files": files}
    if next_page_token:
        resp["nextPageToken"] = next_page_token
    return resp


def _mock_service(chamadas_list_por_folder: dict[str, list[dict]]) -> MagicMock:
    """
    Constroi um mock do Resource do Google Drive.

    chamadas_list_por_folder: mapeia folder_id -> lista de dicts que a
    API retornaria em files().list() para aquela pasta.
    """
    service = MagicMock()

    def side_effect_list(**kwargs):
        # A query esta em kwargs['q'] no formato "'<folder_id>' in parents..."
        query = kwargs["q"]
        # Extrai o folder_id da query (padrao: "'<id>' in parents ...")
        folder_id = query.split("'")[1]
        files = chamadas_list_por_folder.get(folder_id, [])
        mock_execute = MagicMock()
        mock_execute.execute.return_value = _resposta_lista(files)
        return mock_execute

    service.files.return_value.list.side_effect = side_effect_list
    return service


# --- _construir_arquivo_drive ----------------------------------------

class TestConstruirArquivoDrive:
    def test_parseia_modified_time_iso_com_z(self):
        item = {
            "id": "file123",
            "name": "FDS Aromar.pdf",
            "modifiedTime": "2026-07-15T14:30:00.000Z",
            "webViewLink": "https://drive.google.com/x",
        }
        arq = _construir_arquivo_drive(item, "aromar")

        assert arq.file_id == "file123"
        assert arq.nome == "FDS Aromar.pdf"
        assert arq.marca_pasta == "aromar"
        assert arq.modificado_em == datetime(
            2026, 7, 15, 14, 30, 0, tzinfo=timezone.utc
        )
        assert arq.url_web == "https://drive.google.com/x"

    def test_url_web_ausente_vira_string_vazia(self):
        """Alguns arquivos podem nao ter webViewLink na resposta."""
        item = {
            "id": "f1",
            "name": "x.pdf",
            "modifiedTime": "2026-01-01T00:00:00Z",
        }
        arq = _construir_arquivo_drive(item, "marca")
        assert arq.url_web == ""


# --- listar_pdfs_recursivo -------------------------------------------

class TestListarPdfsRecursivo:
    def test_raiz_sem_pastas_retorna_vazio(self):
        service = _mock_service({"raiz_id": []})

        with patch(
            "src.drive.client._construir_service", return_value=service
        ):
            pdfs = listar_pdfs_recursivo("raiz_id")

        assert pdfs == []

    def test_descobre_pdf_em_subpasta_profunda(self):
        """
        Estrutura:
          raiz/
            limpax/           <- pasta de marca (nivel 1)
              subpasta/      <- subpasta arbitraria
                FDS.pdf      <- alvo
        """
        service = _mock_service({
            "raiz_id": [
                {"id": "limpax_id", "name": "limpax", "mimeType": MIMETYPE_FOLDER},
            ],
            "limpax_id": [
                {"id": "sub_id", "name": "subpasta", "mimeType": MIMETYPE_FOLDER},
            ],
            "sub_id": [
                {
                    "id": "pdf_id",
                    "name": "FDS.pdf",
                    "mimeType": MIMETYPE_PDF,
                    "modifiedTime": "2026-01-01T00:00:00Z",
                    "webViewLink": "https://drive/x",
                },
            ],
        })

        with patch(
            "src.drive.client._construir_service", return_value=service
        ):
            pdfs = listar_pdfs_recursivo("raiz_id")

        assert len(pdfs) == 1
        assert pdfs[0].nome == "FDS.pdf"

    def test_marca_pasta_e_do_nivel_1_mesmo_em_subpasta(self):
        """PDF em limpax/subpasta/x.pdf deve ter marca_pasta = 'limpax'."""
        service = _mock_service({
            "raiz_id": [
                {"id": "limpax_id", "name": "limpax", "mimeType": MIMETYPE_FOLDER},
            ],
            "limpax_id": [
                {"id": "sub_id", "name": "outra", "mimeType": MIMETYPE_FOLDER},
            ],
            "sub_id": [
                {
                    "id": "pdf_id",
                    "name": "FDS.pdf",
                    "mimeType": MIMETYPE_PDF,
                    "modifiedTime": "2026-01-01T00:00:00Z",
                    "webViewLink": "https://drive/x",
                },
            ],
        })

        with patch(
            "src.drive.client._construir_service", return_value=service
        ):
            pdfs = listar_pdfs_recursivo("raiz_id")

        assert pdfs[0].marca_pasta == "limpax"

    def test_arquivos_nao_pdf_sao_ignorados(self):
        """docx, imagens, planilhas na arvore nao aparecem no resultado."""
        service = _mock_service({
            "raiz_id": [
                {"id": "marca_id", "name": "marca", "mimeType": MIMETYPE_FOLDER},
            ],
            "marca_id": [
                {
                    "id": "pdf_id",
                    "name": "FDS.pdf",
                    "mimeType": MIMETYPE_PDF,
                    "modifiedTime": "2026-01-01T00:00:00Z",
                    "webViewLink": "https://x",
                },
                {
                    "id": "docx_id",
                    "name": "manual.docx",
                    "mimeType": (
                        "application/vnd.openxmlformats-"
                        "officedocument.wordprocessingml.document"
                    ),
                    "modifiedTime": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "jpg_id",
                    "name": "logo.jpg",
                    "mimeType": "image/jpeg",
                    "modifiedTime": "2026-01-01T00:00:00Z",
                },
            ],
        })

        with patch(
            "src.drive.client._construir_service", return_value=service
        ):
            pdfs = listar_pdfs_recursivo("raiz_id")

        assert len(pdfs) == 1
        assert pdfs[0].nome == "FDS.pdf"