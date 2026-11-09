"""
Testes do orquestrador de ingestao do Drive (scripts/ingest_from_drive.py).

Foco em processar_arquivos e StatsIngestao — a logica extraida do main().
de_arquivo_drive e processar_documento sao mockados: aqui validamos o
CONTROLE DE FLUXO (contagem por acao, erro de download nao derruba o
lote), nao a integracao real com Drive/pipeline (ja coberta em outros
testes).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.ingest_from_drive import StatsIngestao, processar_arquivos
from src.drive.client import ArquivoDrive
from datetime import datetime, timezone


def _arquivo(nome: str, file_id: str = "fid") -> ArquivoDrive:
    return ArquivoDrive(
        file_id=file_id,
        nome=nome,
        modificado_em=datetime(2026, 1, 1, tzinfo=timezone.utc),
        marca_pasta="ACME",
        url_web="https://drive/x",
    )


def _resultado(acao: str, **extra) -> dict:
    """Simula o dict retornado por processar_documento."""
    base = {
        "acao": acao,
        "num_paginas": 1,
        "texto_chars": 100,
        "tipo": "FT",
    }
    base.update(extra)
    return base


# --- StatsIngestao ---

class TestStatsIngestao:
    def test_registrar_incrementa_acao_conhecida(self):
        stats = StatsIngestao()
        stats.registrar("inserido")
        stats.registrar("inserido")
        stats.registrar("skip")
        assert stats.inserido == 2
        assert stats.skip == 1
        assert stats.atualizado == 0

    def test_registrar_acao_desconhecida_vira_erro(self):
        """Robustez: se o pipeline devolver acao inesperada, conta como erro
        em vez de estourar KeyError."""
        stats = StatsIngestao()
        stats.registrar("acao_que_nao_existe")
        assert stats.erro == 1


# --- processar_arquivos ---

class TestProcessarArquivos:
    def test_conta_cada_acao_do_pipeline(self, tmp_path: Path):
        arquivos = [
            _arquivo("a.pdf", "1"),
            _arquivo("b.pdf", "2"),
            _arquivo("c.pdf", "3"),
        ]
        acoes = iter(["inserido", "skip", "atualizado"])

        with patch(
            "scripts.ingest_from_drive.de_arquivo_drive",
            side_effect=lambda arq, cache: Path(cache) / f"{arq.file_id}.pdf",
        ), patch(
            "scripts.ingest_from_drive.processar_documento",
            side_effect=lambda fonte, heuristicas, force: _resultado(next(acoes)),
        ):
            stats = processar_arquivos(
                arquivos, tmp_path, heuristicas=None, force=False, dry_run=False
            )

        assert stats.inserido == 1
        assert stats.skip == 1
        assert stats.atualizado == 1
        assert stats.erro == 0

    def test_erro_de_download_nao_derruba_o_lote(self, tmp_path: Path):
        """O 2o arquivo falha no download; 1o e 3o devem ser processados."""
        arquivos = [
            _arquivo("ok1.pdf", "1"),
            _arquivo("falha.pdf", "2"),
            _arquivo("ok2.pdf", "3"),
        ]

        def fake_download(arq, cache):
            if arq.file_id == "2":
                raise OSError("simulando falha de rede no download")
            return Path(cache) / f"{arq.file_id}.pdf"

        with patch(
            "scripts.ingest_from_drive.de_arquivo_drive",
            side_effect=fake_download,
        ), patch(
            "scripts.ingest_from_drive.processar_documento",
            side_effect=lambda fonte, heuristicas, force: _resultado("inserido"),
        ):
            stats = processar_arquivos(
                arquivos, tmp_path, heuristicas=None, force=False, dry_run=False
            )

        # 2 processados com sucesso, 1 erro de download — lote nao parou.
        assert stats.inserido == 2
        assert stats.erro == 1

    def test_acao_erro_do_pipeline_e_contabilizada(self, tmp_path: Path):
        """Erro vindo do proprio pipeline (nao do download) conta como erro."""
        arquivos = [_arquivo("problema.pdf", "1")]

        with patch(
            "scripts.ingest_from_drive.de_arquivo_drive",
            side_effect=lambda arq, cache: Path(cache) / f"{arq.file_id}.pdf",
        ), patch(
            "scripts.ingest_from_drive.processar_documento",
            side_effect=lambda fonte, heuristicas, force: _resultado(
                "erro", motivo="NUL byte no texto"
            ),
        ):
            stats = processar_arquivos(
                arquivos, tmp_path, heuristicas=None, force=False, dry_run=False
            )

        assert stats.erro == 1
        assert stats.inserido == 0

    def test_lista_vazia_retorna_stats_zerado(self, tmp_path: Path):
        stats = processar_arquivos(
            [], tmp_path, heuristicas=None, force=False, dry_run=False
        )
        assert stats.inserido == 0
        assert stats.erro == 0
        assert stats.skip == 0
        assert stats.atualizado == 0

    def test_passa_force_e_heuristicas_para_o_pipeline(self, tmp_path: Path):
        """Garante que os parametros chegam ao processar_documento."""
        arquivos = [_arquivo("x.pdf", "1")]
        chamada = {}

        def captura(fonte, heuristicas, force):
            chamada["heuristicas"] = heuristicas
            chamada["force"] = force
            return _resultado("inserido")

        with patch(
            "scripts.ingest_from_drive.de_arquivo_drive",
            side_effect=lambda arq, cache: Path(cache) / f"{arq.file_id}.pdf",
        ), patch(
            "scripts.ingest_from_drive.processar_documento",
            side_effect=captura,
        ):
            processar_arquivos(
                arquivos, tmp_path,
                heuristicas="conjunto_x", force=True, dry_run=False
            )

        assert chamada["heuristicas"] == "conjunto_x"
        assert chamada["force"] is True