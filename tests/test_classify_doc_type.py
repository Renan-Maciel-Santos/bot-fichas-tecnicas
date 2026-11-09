"""
Testes do classificador de tipo de documento.

Estrategia:
- Casos sinteticos, um por branch da arvore de decisao.
- Os 22 documentos reais ficam para o dry-run (scripts/apply_doc_type.py).
- Testes de heuristica opt-in verificam Dependency Inversion: mesmo input,
  resultado muda conforme heuristicas sao injetadas.
"""

from __future__ import annotations

import pytest

from src.classify_doc_type import (
    ClassificationResult,
    Signal,
    classify_doc_type,
)
from src.heuristics_reseller_a import detectar_sinais_reseller_a


# ============================================================
# Camada 1 + Camada 2: concordancia (confianca 1.0)
# ============================================================

class TestConcordanciaTotal:
    """Nome e texto apontam para o mesmo tipo -> confianca 1.0."""

    def test_ft_nome_e_texto_concordam(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="FT Produto X.pdf",
            texto_extraido="Ficha tecnica do produto X. Composicao: ...",
        )
        assert resultado.tipo == "FT"
        assert resultado.confianca == 1.0

    def test_fds_nome_e_texto_concordam(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="FDS Produto Y.pdf",
            texto_extraido="Ficha de dados de seguranca. NBR 14725.",
        )
        assert resultado.tipo == "FDS"
        assert resultado.confianca == 1.0

    def test_laudo_nome_e_texto_concordam(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="Laudo 001-2025.pdf",
            texto_extraido="Relatorio de ensaio conforme laudo tecnico.",
        )
        assert resultado.tipo == "LAUDO"
        assert resultado.confianca == 1.0


# ============================================================
# Uma fonte apenas (confianca 0.7)
# ============================================================

class TestUmaFonte:
    """Apenas nome OU apenas texto casa -> confianca 0.7."""

    def test_ft_apenas_pelo_nome(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="FICHA TECNICA acabamento.pdf",
            texto_extraido="Nova Quimtec Industria Ltda. Rua Miguel Gimenes.",
        )
        assert resultado.tipo == "FT"
        assert resultado.confianca == 0.7

    def test_fds_apenas_pelo_texto(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="documento_sem_prefixo.pdf",
            texto_extraido="FICHA COM DADOS DE SEGURANCA. NBR 14725:2023.",
        )
        assert resultado.tipo == "FDS"
        assert resultado.confianca == 0.7

    def test_laudo_apenas_pelo_texto(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="512-10-XY.pdf",
            texto_extraido="O presente relatorio de ensaio tem valor restrito.",
        )
        assert resultado.tipo == "LAUDO"
        assert resultado.confianca == 0.7


# ============================================================
# Conflito (confianca 0.3, tipo=None)
# ============================================================

class TestConflito:
    """Sinais discordantes -> None, confianca 0.3."""

    def test_nome_ft_texto_fds_conflita(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="FT Produto Z.pdf",
            texto_extraido="Ficha com dados de seguranca do produto Z.",
        )
        assert resultado.tipo is None
        assert resultado.confianca == 0.3


# ============================================================
# Nenhum sinal (confianca 0.0, tipo=None)
# ============================================================

class TestSemSinal:
    def test_documento_sem_pistas(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="INFORMACOES DOMESTICA SOFT.pdf",
            texto_extraido="Senhores representantes, informamos os itens.",
        )
        assert resultado.tipo is None
        assert resultado.confianca == 0.0

    def test_texto_vazio_sem_pistas_no_nome(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="9000007-6775-Toalha.pdf",
            texto_extraido="",
        )
        assert resultado.tipo is None
        assert resultado.confianca == 0.0

    def test_texto_none_sem_pistas_no_nome(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="arquivo_generico.pdf",
            texto_extraido=None,
        )
        assert resultado.tipo is None
        assert resultado.confianca == 0.0


# ============================================================
# Robustez das regex
# ============================================================

class TestRobustezRegex:
    """`\\b` evita falsos positivos em palavras que contem as siglas."""

    def test_ft_nao_casa_dentro_de_soft(self) -> None:
        # "SOFT" contem "FT" mas nao deve disparar
        resultado = classify_doc_type(
            nome_arquivo="florbela SOFT.pdf",
            texto_extraido="Produto de higiene, marca florbela.",
        )
        # Sem sinal de FT/FDS/LAUDO em lugar nenhum
        assert resultado.tipo is None

    def test_texto_com_acentos_normaliza(self) -> None:
        # "técnica" acentuado deve casar com "tecnica" sem acento
        resultado = classify_doc_type(
            nome_arquivo="documento.pdf",
            texto_extraido="Ficha técnica do produto.",
        )
        assert resultado.tipo == "FT"
        assert resultado.confianca == 0.7


# ============================================================
# Heuristicas opt-in (Dependency Inversion)
# ============================================================

class TestHeuristicaAnin:
    """Template REF dispara FT via heuristica injetada."""

    _TEXTO_REF = (
        "COD REF\n"
        "124401\n"
        "PRODUTO\n"
        "INTERFOLHA ORCA BRANCO C/ 1000 20X21\n"
        "MARCA\n"
        "ORCA\n"
        "TIPO DE PRODUTO\n"
        "Papel Toalha 2 Dobras\n"
    )

    def test_sem_heuristica_nao_classifica(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="4401 Interfolha Orca.pdf",
            texto_extraido=self._TEXTO_REF,
        )
        assert resultado.tipo is None

    def test_com_heuristica_classifica_como_ft(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="4401 Interfolha Orca.pdf",
            texto_extraido=self._TEXTO_REF,
            heuristicas=[detectar_sinais_reseller_a],
        )
        assert resultado.tipo == "FT"
        # Fonte diferente da regex normal => motivo audita
        assert any("ref" in m.lower() for m in resultado.motivos)


class TestHeuristicaTabelaPapel:
    """Tabela tecnica de papel exige gramatura + >=2 secundarios."""

    def test_gramatura_sozinha_nao_dispara(self) -> None:
        resultado = classify_doc_type(
            nome_arquivo="documento.pdf",
            texto_extraido="Gramatura mencionada isoladamente no texto.",
            heuristicas=[detectar_sinais_reseller_a],
        )
        assert resultado.tipo is None

    def test_gramatura_mais_indicadores_dispara_ft(self) -> None:
        texto = (
            "CNPJ 04.490.046.0001/97\n"
            "Gramatura - g/m2: 18\n"
            "pH\n"
            "Absorcao de Agua - gH2O/g: 6,35\n"
            "Alvura %\n"
            "maciez (Nm/g): 2,13\n"
        )
        resultado = classify_doc_type(
            nome_arquivo="ficha tecnica florbela.pdf",
            texto_extraido=texto,
            heuristicas=[detectar_sinais_reseller_a],
        )
        # Nome ja casa FT (0.7) + heuristica reforca -> 2 fontes distintas -> 1.0
        assert resultado.tipo == "FT"
        assert resultado.confianca == 1.0


class TestHeuristicaResiliente:
    """Heuristica quebrada nao derruba o classificador."""

    def test_heuristica_com_excecao_e_ignorada(self) -> None:
        def heuristica_quebrada(nome: str, texto: str | None) -> list[Signal]:
            raise RuntimeError("simulando bug numa heuristica")

        # Sinal valido do nome + heuristica que explode = classificacao ok
        resultado = classify_doc_type(
            nome_arquivo="FDS Produto.pdf",
            texto_extraido="conteudo qualquer sem palavra-chave",
            heuristicas=[heuristica_quebrada],
        )
        assert resultado.tipo == "FDS"
        assert resultado.confianca == 0.7