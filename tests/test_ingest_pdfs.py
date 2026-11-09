"""
Testes da funcao _calcular_tipo_para_gravar.

Foco: threshold de confianca e integracao com heuristicas opt-in.
Nao testa I/O (banco, arquivo) — isso e responsabilidade dos testes
de integracao (a rodada real do script cumpre esse papel).
"""

from src.ingestion.ingest_pdfs import _calcular_tipo_para_gravar
from src.heuristics_reseller_a import detectar_sinais_reseller_a


class TestThreshold:
    """Verifica que so tipos com confianca suficiente sao gravados."""

    def test_confianca_alta_grava(self) -> None:
        # Nome + texto concordam -> confianca 1.0
        tipo = _calcular_tipo_para_gravar(
            "FDS Aromatizante.pdf",
            "FICHA COM DADOS DE SEGURANCA",
            heuristicas=None,
        )
        assert tipo == "FDS"

    def test_confianca_media_grava(self) -> None:
        # So o nome casa -> confianca 0.7 (limite exato)
        tipo = _calcular_tipo_para_gravar(
            "FICHA TECNICA Produto X.pdf",
            "Conteudo qualquer sem palavra chave",
            heuristicas=None,
        )
        assert tipo == "FT"

    def test_conflito_nao_grava(self) -> None:
        # Nome diz FT, texto diz FDS -> confianca 0.3 -> None
        tipo = _calcular_tipo_para_gravar(
            "FT Produto.pdf",
            "Ficha com dados de seguranca",
            heuristicas=None,
        )
        assert tipo is None

    def test_sem_sinais_nao_grava(self) -> None:
        tipo = _calcular_tipo_para_gravar(
            "documento_qualquer.pdf",
            "texto qualquer sem palavra chave",
            heuristicas=None,
        )
        assert tipo is None

    def test_texto_vazio_nao_grava(self) -> None:
        # Caso real: PDF escaneado sem OCR
        tipo = _calcular_tipo_para_gravar(
            "9000007 - Toalha Bobina.pdf",
            "",
            heuristicas=None,
        )
        assert tipo is None


class TestHeuristicas:
    """Verifica que heuristicas opt-in mudam o resultado da ingestao."""

    _TEXTO_REF = (
        "COD REF\n124401\nPRODUTO\nInterfolha Orca\n"
        "MARCA\nSHARK\nTIPO DE PRODUTO\nPapel Toalha\n"
    )

    def test_sem_heuristicas_template_ref_nao_classifica(self) -> None:
        tipo = _calcular_tipo_para_gravar(
            "4401 Interfolha Orca.pdf",
            self._TEXTO_REF,
            heuristicas=None,
        )
        assert tipo is None

    def test_com_heuristicas_template_ref_vira_ft(self) -> None:
        tipo = _calcular_tipo_para_gravar(
            "4401 Interfolha Orca.pdf",
            self._TEXTO_REF,
            heuristicas=[detectar_sinais_reseller_a],
        )
        assert tipo == "FT"