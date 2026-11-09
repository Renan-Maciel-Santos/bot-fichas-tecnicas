"""
Testes da politica de citacao (src/query/citation.py).

Golden set: para cada tipo (FT, FDS, LAUDO, None), verifica que a
citacao final e legivel e nao duplica o rotulo do tipo.
"""

from src.query.citation import formatar_fonte


class TestFDS:
    def test_prefixo_com_espaco(self) -> None:
        assert formatar_fonte(
            "FDS Aromatizante 120ml Aromar.pdf", "FDS"
        ) == "FDS de Aromatizante 120ml Aromar"

    def test_prefixo_com_hifen(self) -> None:
        assert formatar_fonte(
            "FDS-Água sanitária Limpax.pdf", "FDS"
        ) == "FDS de Água sanitária Limpax"

    def test_prefixo_composto_com_norma(self) -> None:
        # Nome enrolado da vida real: aceitavel deixar como esta apos "FDS-"
        assert formatar_fonte(
            "FDS-NBR-14725-PT-BRILHO-INOX.pdf", "FDS"
        ) == "FDS de NBR-14725-PT-BRILHO-INOX"

    def test_sem_prefixo_no_nome(self) -> None:
        # Hipotetico: nome sem "FDS" mas tipo=FDS via texto
        assert formatar_fonte(
            "Aromatizante Aromar.pdf", "FDS"
        ) == "FDS de Aromatizante Aromar"


class TestFT:
    def test_prefixo_ficha_tecnica_com_hifen(self) -> None:
        assert formatar_fonte(
            "FICHA TÉCNICA - ACABAMENTO ACRÍLICO.pdf", "FT"
        ) == "FT de ACABAMENTO ACRÍLICO"

    def test_prefixo_ficha_tecnica_espacos_duplos(self) -> None:
        assert formatar_fonte(
            "ficha técnica  florbela soft (1).pdf", "FT"
        ) == "FT de florbela soft (1)"

    def test_sem_prefixo_no_nome(self) -> None:
        # Caso comum: nome so com codigo e descricao, tipo=FT via texto
        assert formatar_fonte(
            "4401 Interfolha Orca branco.pdf", "FT"
        ) == "FT de 4401 Interfolha Orca branco"

    def test_ft_sozinho_como_prefixo(self) -> None:
        assert formatar_fonte(
            "FT Produto X.pdf", "FT"
        ) == "FT de Produto X"


class TestLaudo:
    def test_prefixo_ausente(self) -> None:
        # Caso real: nome so com codigo, tipo=LAUDO via texto
        assert formatar_fonte(
            "512-10-XY - macia especial.pdf", "LAUDO"
        ) == "laudo de 512-10-XY - macia especial"

    def test_prefixo_presente(self) -> None:
        assert formatar_fonte(
            "Laudo 001-2025.pdf", "LAUDO"
        ) == "laudo de 001-2025"


class TestSemTipo:
    def test_documento_generico(self) -> None:
        assert formatar_fonte(
            "INFORMAÇÕES DOMESTICA SOFT E ESPECIAL 19-04.pdf", None
        ) == "documento INFORMAÇÕES DOMESTICA SOFT E ESPECIAL 19-04"

    def test_nome_vazio_com_none(self) -> None:
        assert formatar_fonte("", None) == "documento"


class TestNormalizacao:
    def test_extensao_maiuscula_removida(self) -> None:
        assert formatar_fonte("Arquivo.PDF", "FT") == "FT de Arquivo"

    def test_espacos_extras_normalizados(self) -> None:
        assert formatar_fonte(
            "FDS   Detergente   Neutro.pdf", "FDS"
        ) == "FDS de Detergente Neutro"


class TestBordas:
    def test_nome_e_apenas_o_prefixo_fds(self) -> None:
        # Nome patologico: nao ha o que citar alem do tipo.
        assert formatar_fonte("FDS.pdf", "FDS") == "FDS"

    def test_nome_e_apenas_o_prefixo_ft(self) -> None:
        assert formatar_fonte("FT.pdf", "FT") == "FT"
        
class TestTipoDesconhecido:
    def test_tipo_fora_do_vocabulario_cai_no_fallback(self) -> None:
        """Vocabulario fechado: qualquer coisa fora de FT/FDS/LAUDO vira 'documento X'."""
        assert formatar_fonte(
            "Manual Instalacao.pdf", "MANUAL"
        ) == "documento Manual Instalacao"


class TestNomeNone:
    """Defesa contra documentos.nome_arquivo = NULL vindo do banco."""

    def test_none_com_tipo_conhecido(self) -> None:
        assert formatar_fonte(None, "FDS") == "FDS"

    def test_none_sem_tipo(self) -> None:
        assert formatar_fonte(None, None) == "documento"


class TestSeparadoresNoPrefixo:
    """A regex do prefixo aceita espaco, hifen e underscore."""

    def test_prefixo_com_underscore(self) -> None:
        assert formatar_fonte(
            "FDS_Detergente Neutro.pdf", "FDS"
        ) == "FDS de Detergente Neutro"