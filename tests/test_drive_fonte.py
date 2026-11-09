"""
Testes unitarios de src/drive/fonte.py.

A funcao `baixar_arquivo` do client e mockada — nao ha chamada real
ao Google Drive nem download de rede. O objetivo e validar:

1. O DocumentoFonte gerado tem todos os campos preenchidos corretamente.
2. O cache por file_id evita download duplicado.
3. A defesa contra cache_dir inexistente funciona.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.drive.client import ArquivoDrive
from src.drive.fonte import de_arquivo_drive


def _fake_arquivo_drive() -> ArquivoDrive:
    return ArquivoDrive(
        file_id="file_abc123",
        nome="FDS Aromar.pdf",
        modificado_em=datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc),
        marca_pasta="aromar",
        url_web="https://drive.google.com/file/d/file_abc123/view",
    )


class TestDeArquivoDrive:
    def test_baixa_e_constroi_documento_fonte_com_todos_os_campos(
        self, tmp_path: Path
    ):
        """Fluxo feliz: cache vazio -> chama baixar_arquivo -> DocumentoFonte pronto."""
        arquivo = _fake_arquivo_drive()

        # Mock de baixar_arquivo: simula o download criando o arquivo.
        def fake_baixar(file_id: str, destino: Path) -> None:
            destino.write_bytes(b"%PDF-1.4 fake content")

        with patch("src.drive.fonte.baixar_arquivo", side_effect=fake_baixar):
            fonte = de_arquivo_drive(arquivo, tmp_path)

        # Identificador = file_id puro (sem prefixo local:).
        assert fonte.identificador == "file_abc123"
        # Caminho local aponta para o cache com nome estavel.
        assert fonte.caminho_local == tmp_path / "file_abc123.pdf"
        assert fonte.caminho_local.is_file()
        # Nome de exibicao e o do Drive.
        assert fonte.nome_arquivo == "FDS Aromar.pdf"
        assert fonte.marca_pasta == "aromar"
        # Timestamp e URL sao preenchidos (ao contrario do local).
        assert fonte.modificado_em == datetime(
            2026, 7, 15, 14, 30, tzinfo=timezone.utc
        )
        assert fonte.url == "https://drive.google.com/file/d/file_abc123/view"
        # E nao e local — a flag inversa confirma o tipo de origem.
        assert fonte.e_local is False

    def test_cache_hit_nao_baixa_arquivo_novamente(self, tmp_path: Path):
        """Se o arquivo ja existe no cache, baixar_arquivo NAO deve ser chamado."""
        arquivo = _fake_arquivo_drive()

        # Simula um download previo — arquivo ja esta no cache.
        (tmp_path / "file_abc123.pdf").write_bytes(b"conteudo previo")

        with patch("src.drive.fonte.baixar_arquivo") as mock_baixar:
            fonte = de_arquivo_drive(arquivo, tmp_path)

        # Nao chamou baixar_arquivo — reusou o arquivo existente.
        mock_baixar.assert_not_called()
        # Mas o DocumentoFonte foi construido corretamente do mesmo jeito.
        assert fonte.identificador == "file_abc123"
        assert fonte.caminho_local == tmp_path / "file_abc123.pdf"

    def test_cache_dir_inexistente_levanta_erro_claro(self, tmp_path: Path):
        """Defesa contra bug do caller: pasta precisa existir antes."""
        arquivo = _fake_arquivo_drive()
        cache_inexistente = tmp_path / "nao_existe"

        with pytest.raises(FileNotFoundError, match="cache_dir nao existe"):
            de_arquivo_drive(arquivo, cache_inexistente)