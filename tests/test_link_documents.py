"""Testes de link_documents: extracao de codigo (Camada 0), normalizacao
de nome, filtro regulatorio e vinculacao por marca+trigram (Camada 1)."""

import pytest

from src.link_documents import (
    FATOR_DESTAQUE_LINK,
    PREFIXO_TIPO_REGEX,
    REGULATORIO_REGEX,
    SCORE_MINIMO_TRIGRAM,
    Candidato,
    _extrair_miolo,
    decidir_camada1,
    executar_camada1,
    extract_codigo_prefixo,
)


# --- Camada 0: extract_codigo_prefixo (ja existiam) ---

def test_codigo_simples_no_prefixo():
    assert extract_codigo_prefixo("9000004 - P. HIG ROLAO.pdf") == 9000004


def test_dois_numeros_pega_o_primeiro():
    assert extract_codigo_prefixo("9000007 - 6775 - Toalha Bobina.pdf") == 9000007


def test_sem_numero_retorna_none():
    assert extract_codigo_prefixo("FDS Aromatizante 120ml Aromar.pdf") is None


def test_numero_curto_nao_e_codigo():
    assert extract_codigo_prefixo("4401 Interfolha Orca branco.pdf") is None


def test_numero_no_meio_do_nome_nao_conta():
    assert extract_codigo_prefixo("FDS-NBR-14725-PT-BRILHO-INOX.pdf") is None


def test_codigo_com_espacos_a_esquerda():
    assert extract_codigo_prefixo("  9000010 - P. HIG ROLAO.pdf") == 9000010


def test_string_vazia_retorna_none():
    assert extract_codigo_prefixo("") is None


# --- Camada 1: _extrair_miolo (puro) ---

class TestExtrairMiolo:
    def test_remove_prefixo_fispq_com_hifen(self):
        assert _extrair_miolo("FISPQ - FORNO EXPRESS.pdf") == "FORNO EXPRESS"

    def test_remove_prefixo_ficha_tecnica_acentuada(self):
        assert _extrair_miolo("FICHA TÉCNICA - BACTERIX A.pdf") == "BACTERIX A"

    def test_remove_prefixo_bt_sem_hifen(self):
        assert _extrair_miolo("BT Ceramax RC Acrilica.pdf") == "Ceramax RC Acrilica"

    def test_remove_prefixo_fds(self):
        assert _extrair_miolo("FDS SABAO EM PO LAVITA.pdf") == "SABAO EM PO LAVITA"

    def test_remove_extensao_maiuscula(self):
        assert _extrair_miolo("FT - DETERGENTE.PDF") == "DETERGENTE"

    def test_colapsa_espacos_multiplos(self):
        assert _extrair_miolo("FISPQ -   KRISTAL   LIMPA  ALUMINIO.pdf") == "KRISTAL LIMPA ALUMINIO"

    def test_nome_sem_prefixo_permanece_intacto(self):
        assert _extrair_miolo("INOVA MAX 5L.pdf") == "INOVA MAX 5L"

    def test_nome_vazio_retorna_vazio(self):
        assert _extrair_miolo("") == ""

    def test_nome_so_com_prefixo_e_extensao_retorna_vazio(self):
        assert _extrair_miolo("FISPQ.pdf") == ""

    def test_codigo_no_nome_e_preservado(self):
        # Codigo NAO e prefixo de tipo — deve ficar no miolo.
        assert _extrair_miolo("FT - 0812204004 - PAPELTEC COLETOR.pdf") == "0812204004 - PAPELTEC COLETOR"


# --- Camada 1: REGULATORIO_REGEX (puro) ---

class TestRegulatorioRegex:
    @pytest.mark.parametrize("nome", [
        "ALVARA COMERCIO 2026.pdf",
        "ANVISA Bacterix BG.pdf",
        "certificado de AFE SANEANTES.pdf",
        "CRQ MATRIZ Val 2027.pdf",
        "NOTIFICAÇÃO ALCOOL 46.pdf",
        "REGISTRO GEL 70.pdf",
        "Licenca de Funcionamento.pdf",
        "Laudo de Analise Microbiologica.pdf",
    ])
    def test_bate_em_documento_regulatorio(self, nome):
        assert REGULATORIO_REGEX.search(nome) is not None

    @pytest.mark.parametrize("nome", [
        "FISPQ - FORNO EXPRESS.pdf",
        "FT - DETERGENTE NEUTRO.pdf",
        "FDS SABAO EM PO.pdf",
        "BT Ceramax Acrilica.pdf",
    ])
    def test_nao_bate_em_ficha_de_produto(self, nome):
        assert REGULATORIO_REGEX.search(nome) is None


# --- Camada 1: constantes calibradas (guarda) ---

def test_limiares_camada1_sao_conservadores():
    """Salvaguarda: os limiares foram calibrados para PRECISAO contra o
    golden links_v1. Mudanca silenciosa deve disparar este teste; mudanca
    deliberada passa antes por `python -m scripts.eval_camada1`."""
    assert SCORE_MINIMO_TRIGRAM == 0.35
    assert FATOR_DESTAQUE_LINK == 1.3


# --- Camada 1: decidir_camada1 (logica pura) ---

def _c(codigo, descricao, score):
    return Candidato(codigo, descricao, score)


class TestDecidirCamada1:
    def test_sem_candidatos_e_sem_match(self):
        assert decidir_camada1("QUALQUER", []).categoria == "sem_match"

    def test_score_abaixo_do_minimo_e_sem_match(self):
        decisao = decidir_camada1("X", [_c(1, "PRODUTO A", 0.30)])
        assert decisao.categoria == "sem_match"

    def test_candidato_unico_acima_do_minimo_vincula(self):
        decisao = decidir_camada1("X", [_c(1, "PRODUTO A", 0.40)])
        assert [v.codigo for v in decisao.vinculos] == [1]

    def test_top2_proximo_e_ambiguo(self):
        decisao = decidir_camada1("CLORO", [
            _c(1, "CLORO LIQUIDO 5% 5L", 0.50),
            _c(2, "CLORO LIQUIDO 1% 5L", 0.45),
        ])
        assert decisao.categoria == "ambiguo"

    def test_variantes_de_tamanho_vinculam_juntas(self):
        """FISPQ sem tamanho no nome vale para 1 L e 500 ML; o destaque e
        medido contra o proximo produto de verdade, nao contra o irmao."""
        decisao = decidir_camada1("Gel Antisseptico Higimax", [
            _c(1, "ALCOOL GEL ANTISSEPTICO 1 L HIGIMAX", 0.706),
            _c(2, "ALCOOL GEL ANTISSEPTICO 500 ML HIGIMAX", 0.649),
            _c(3, "REFIL ALCOOL SPRAY ANTISSEPTICO 400 ML HIGIMAX", 0.408),
        ])
        assert decisao.categoria == "vinculado"
        assert {v.codigo for v in decisao.vinculos} == {1, 2}

    def test_tamanho_no_nome_do_arquivo_desliga_agrupamento(self):
        """'200L' no nome: o tamanho e parte do pedido, 100L nao serve."""
        decisao = decidir_camada1("Saco de Lixo Infectante 200L", [
            _c(1, "SACO DE LIXO INFECTANTE 200L 4.0", 0.60),
            _c(2, "SACO DE LIXO INFECTANTE 100L 4.0", 0.58),
        ])
        assert decisao.categoria == "ambiguo"

    def test_contagem_de_unidades_nao_e_embalagem(self):
        """Guardanapo de 500 e de 1.000 unidades sao produtos distintos."""
        decisao = decidir_camada1("GUARDANAPO SACHE 28X21", [
            _c(1, "GUARDANAPO SACHE 28X21 HOSPIMAX - 1.000 UND", 0.60),
            _c(2, "GUARDANAPO SACHE 28X21 HOSPIMAX - 500 UND", 0.55),
        ])
        assert decisao.categoria == "ambiguo"

    def test_medida_que_define_o_produto_nao_agrupa(self):
        decisao = decidir_camada1("LENCOL HOSPIMAX BRANCO", [
            _c(1, "LENCOL HOSP. BRANCO 70X50CM 6RL HOSPIMAX", 0.58),
            _c(2, "LENCOL HOSP. BRANCO 50X50CM 6RL HOSPIMAX", 0.55),
        ])
        assert decisao.categoria == "ambiguo"


# --- Camada 1: executar_camada1 end-to-end (integracao) ---

@pytest.mark.integration
class TestExecutarCamada1:
    def _seed(self, cur):
        """Insere marca com produtos distintos e ambiguos para exercitar
        os tres caminhos: vinculo, ambiguo, regulatorio."""
        import uuid

        # Produtos da marca ACME: um alvo claro e dois parecidos (ambiguidade).
        cur.execute(
            """
            INSERT INTO produtos (codigo, descricao, marca, texto_busca, ativo)
            VALUES
                (900101, 'FORNO EXPRESS LIMPA FORNOS 5L', 'ACME',
                    'forno express limpa fornos 5l', TRUE),
                (900102, 'CLORO LIQUIDO 12 PORCENTO 5L', 'ACME',
                    'cloro liquido 12 5l', TRUE),
                (900103, 'CLORO LIQUIDO 1 PORCENTO 5L', 'ACME',
                    'cloro liquido 1 5l', TRUE)
            """
        )

        doc_limpo = str(uuid.uuid4())
        doc_ambiguo = str(uuid.uuid4())
        doc_regulatorio = str(uuid.uuid4())

        cur.execute(
            """
            INSERT INTO documentos
                (id, drive_file_id, nome_arquivo, marca_pasta, tipo)
            VALUES
                (%s, %s, 'FISPQ - FORNO EXPRESS.pdf', 'ACME', 'FDS'),
                (%s, %s, 'FDS CLORO LIQUIDO.pdf', 'ACME', 'FDS'),
                (%s, %s, 'ANVISA ACME registro.pdf', 'ACME', NULL)
            """,
            (
                doc_limpo, f"t-{uuid.uuid4()}",
                doc_ambiguo, f"t-{uuid.uuid4()}",
                doc_regulatorio, f"t-{uuid.uuid4()}",
            ),
        )
        return doc_limpo, doc_ambiguo, doc_regulatorio

    def test_vincula_limpo_manda_ambiguo_para_revisao_pula_regulatorio(
        self, db_transaction
    ):
        with db_transaction.cursor() as cur:
            doc_limpo, doc_ambiguo, doc_regulatorio = self._seed(cur)

            resultado = executar_camada1(cur, dry_run=False)

            # 1. Regulatorio foi pulado.
            assert resultado.regulatorios >= 1

            # 2. O doc limpo ("FORNO EXPRESS") vinculou ao produto certo.
            cur.execute(
                "SELECT produto_codigo FROM produto_documento "
                "WHERE documento_id = %s",
                (doc_limpo,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 900101

            # 3. O doc ambiguo ("CLORO LIQUIDO" bate em 12% e 1% igualmente)
            #    NAO vinculou — foi para revisao.
            cur.execute(
                "SELECT COUNT(*) FROM produto_documento "
                "WHERE documento_id = %s",
                (doc_ambiguo,),
            )
            assert cur.fetchone()[0] == 0
            assert any("CLORO" in a for a in resultado.ambiguos)

    def test_dry_run_nao_grava(self, db_transaction):
        with db_transaction.cursor() as cur:
            doc_limpo, _, _ = self._seed(cur)

            resultado = executar_camada1(cur, dry_run=True)

            # Contabilizou o vinculo, mas NAO gravou.
            assert resultado.vinculados >= 1
            cur.execute(
                "SELECT COUNT(*) FROM produto_documento "
                "WHERE documento_id = %s",
                (doc_limpo,),
            )
            assert cur.fetchone()[0] == 0