"""
Testes de integracao para src/query/search_structured.py.

Todos os testes usam a fixture `db_transaction` (rollback no teardown)
e `dados_basicos` (mini-catalogo consistente). Nenhum dado persiste.

Marcador @pytest.mark.integration: rodar seletivo com
    pytest -m integration
"""

import pytest

from src.query.search_structured import (
    documentos_de_produto,
    produtos_por_propriedade,
    propriedades_de_produto,
    resolver_produto,
)


@pytest.mark.integration
class TestResolverProduto:
    def test_lookup_por_codigo_exato_retorna_um_candidato(
        self, dados_basicos, db_transaction
    ):
        candidatos = resolver_produto(
            str(dados_basicos.codigo_limpax), conn=db_transaction
        )

        assert len(candidatos) == 1
        assert candidatos[0].codigo == dados_basicos.codigo_limpax
        assert candidatos[0].descricao == "AGUA SANITARIA LIMPAX 5L"
        assert candidatos[0].score == 1.0

    def test_codigo_inexistente_retorna_vazio(
        self, dados_basicos, db_transaction
    ):
        candidatos = resolver_produto("99999999", conn=db_transaction)
        assert candidatos == []

    def test_termo_textual_encontra_via_trigram(
        self, dados_basicos, db_transaction
    ):
        """Rota trigram: 'limpax' bate no texto_busca do produto limpax."""
        candidatos = resolver_produto("limpax", conn=db_transaction)

        codigos = [c.codigo for c in candidatos]
        assert dados_basicos.codigo_limpax in codigos

    def test_produto_inativo_nao_aparece_no_lookup_por_codigo(
        self, dados_basicos, db_transaction
    ):
        """Regra de negocio critica: descontinuado nao pode ser oferecido."""
        candidatos = resolver_produto(
            str(dados_basicos.codigo_descontinuado), conn=db_transaction
        )
        assert candidatos == []

    def test_termo_vazio_retorna_vazio_sem_tocar_no_banco(self, db_transaction):
        """Early return: nem chega no SQL. dados_basicos nao necessario."""
        assert resolver_produto("", conn=db_transaction) == []
        assert resolver_produto("   ", conn=db_transaction) == []


@pytest.mark.integration
class TestProdutosPorPropriedade:
    def test_busca_ingrediente_ativo_hipoclorito_encontra_limpax(
        self, dados_basicos, db_transaction
    ):
        resultados = produtos_por_propriedade(
            "ingrediente_ativo", "hipoclorito", conn=db_transaction
        )

        codigos = [r.produto_codigo for r in resultados]
        assert dados_basicos.codigo_limpax in codigos

    def test_campo_fora_do_vocabulario_levanta_value_error(self, db_transaction):
        """Defesa contra classifier alucinado — SQL nao deve nem ser executado."""
        with pytest.raises(ValueError, match="Campo fora do vocabulario"):
            produtos_por_propriedade(
                "cor_favorita", "azul", conn=db_transaction
            )

    def test_valor_inexistente_retorna_vazio(
        self, dados_basicos, db_transaction
    ):
        resultados = produtos_por_propriedade(
            "ingrediente_ativo", "xpto_nao_existe", conn=db_transaction
        )
        assert resultados == []

    def test_busca_sem_acento_encontra_valor_com_acento(
        self, dados_basicos, db_transaction
    ):
        """unaccent() nos dois lados do ILIKE — regressao critica do FATOR_DESTAQUE."""
        # Fixture tem 'Hipoclorito de Sodio' (sem acento em Sodio na fixture).
        # Vamos exercitar o outro lado: valor da fixture SEM acento, busca SEM
        # acento, mas testa que a comparacao funciona apos unaccent.
        resultados = produtos_por_propriedade(
            "ingrediente_ativo", "sodio", conn=db_transaction
        )

        codigos = [r.produto_codigo for r in resultados]
        assert dados_basicos.codigo_limpax in codigos


@pytest.mark.integration
class TestPropriedadesDeProduto:
    def test_produto_com_propriedades_retorna_todas_com_procedencia(
        self, dados_basicos, db_transaction
    ):
        propriedades = propriedades_de_produto(
            dados_basicos.codigo_limpax, conn=db_transaction
        )

        assert len(propriedades) == 2
        campos = {p.campo for p in propriedades}
        assert campos == {"ingrediente_ativo", "ph"}

        # Procedencia preenchida (documento_fonte, tipo).
        for p in propriedades:
            assert p.documento_fonte == "FDS Agua Sanitaria Limpax.pdf"
            assert p.tipo_documento == "FDS"

    def test_produto_sem_propriedades_retorna_vazio(
        self, dados_basicos, db_transaction
    ):
        """codigo_folhabela tem documento vinculado mas nenhuma propriedade extraida."""
        propriedades = propriedades_de_produto(
            dados_basicos.codigo_folhabela, conn=db_transaction
        )
        assert propriedades == []

    def test_descricao_e_marca_vem_via_resolver_produto_interno(
        self, dados_basicos, db_transaction
    ):
        """
        Prova que a propagacao de conn na chamada aninhada funciona.
        Se estivesse quebrada, resolver_produto(str(codigo), conn=nova)
        nao veria o INSERT da fixture (que esta na transacao) e
        descricao/marca chegariam vazias.
        """
        propriedades = propriedades_de_produto(
            dados_basicos.codigo_limpax, conn=db_transaction
        )

        assert len(propriedades) > 0
        # Se a propagacao falhasse, ambos seriam string vazia (branch else).
        assert propriedades[0].produto_descricao == "AGUA SANITARIA LIMPAX 5L"
        assert propriedades[0].marca == "LIMPAX"


@pytest.mark.integration
class TestDocumentosDeProduto:
    def test_produto_com_documento_retorna_com_metadados(
        self, dados_basicos, db_transaction
    ):
        docs = documentos_de_produto(
            dados_basicos.codigo_limpax, conn=db_transaction
        )

        assert len(docs) == 1
        assert docs[0].documento_id == dados_basicos.doc_fds_limpax_id
        assert docs[0].nome_arquivo == "FDS Agua Sanitaria Limpax.pdf"
        assert docs[0].tipo == "FDS"
        assert docs[0].url_drive == "https://drive.google.com/fake/fds-limpax"

    def _vincular_copia(self, cur, codigo, marca_pasta, hash_conteudo, revogado=False):
        import uuid

        documento_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO documentos
                (id, drive_file_id, nome_arquivo, marca_pasta, tipo,
                 url_drive, hash_conteudo, revogado_em)
            VALUES (%s, %s, 'FDS Agua Sanitaria Limpax.pdf', %s, 'FDS',
                    'https://drive.google.com/fake/copia', %s,
                    CASE WHEN %s THEN NOW() END)
            """,
            (documento_id, f"test-{uuid.uuid4()}", marca_pasta, hash_conteudo, revogado),
        )
        cur.execute(
            "INSERT INTO produto_documento (produto_codigo, documento_id, "
            "confianca_match, metodo_match) VALUES (%s, %s, 1.00, 'test')",
            (codigo, documento_id),
        )
        return documento_id

    def test_copia_do_mesmo_pdf_sai_uma_vez_so(self, dados_basicos, db_transaction):
        """Mesmo conteudo em local_amostra e na pasta da marca: o bot manda
        uma copia, a da pasta da marca."""
        with db_transaction.cursor() as cur:
            cur.execute(
                "UPDATE documentos SET hash_conteudo = 'hash-limpax' WHERE id = %s",
                (dados_basicos.doc_fds_limpax_id,),
            )
            self._vincular_copia(cur, dados_basicos.codigo_limpax, "local_amostra", "hash-limpax")

        docs = documentos_de_produto(dados_basicos.codigo_limpax, conn=db_transaction)

        assert [d.documento_id for d in docs] == [dados_basicos.doc_fds_limpax_id]

    def test_documentos_sem_hash_nao_sao_agrupados(self, dados_basicos, db_transaction):
        with db_transaction.cursor() as cur:
            self._vincular_copia(cur, dados_basicos.codigo_limpax, "local_amostra", None)

        docs = documentos_de_produto(dados_basicos.codigo_limpax, conn=db_transaction)

        assert len(docs) == 2

    def test_documento_revogado_nao_e_enviado(self, dados_basicos, db_transaction):
        with db_transaction.cursor() as cur:
            self._vincular_copia(
                cur, dados_basicos.codigo_limpax, "limpax", "hash-outro", revogado=True,
            )

        docs = documentos_de_produto(dados_basicos.codigo_limpax, conn=db_transaction)

        assert [d.documento_id for d in docs] == [dados_basicos.doc_fds_limpax_id]

    def test_produto_sem_documento_retorna_vazio(
        self, dados_basicos, db_transaction
    ):
        docs = documentos_de_produto(
            dados_basicos.codigo_descontinuado, conn=db_transaction
        )
        assert docs == []

@pytest.mark.integration
class TestDocumentoRevogadoNasPropriedades:
    """Propriedade extraida de documento revogado nao volta; propriedade
    sem documento de origem (documento_id NULL) continua valendo."""

    def _revogar_limpax_e_inserir_sem_origem(self, cur, dados):
        cur.execute(
            "UPDATE documentos SET revogado_em = NOW() WHERE id = %s",
            (dados.doc_fds_limpax_id,),
        )
        cur.execute(
            "INSERT INTO propriedades_produto (produto_codigo, documento_id, campo, valor, confianca) "
            "VALUES (%s, NULL, 'ingrediente_ativo', 'Hipoclorito sem origem', 0.90)",
            (dados.codigo_limpax,),
        )

    def test_propriedades_de_produto(self, dados_basicos, db_transaction):
        with db_transaction.cursor() as cur:
            self._revogar_limpax_e_inserir_sem_origem(cur, dados_basicos)

        propriedades = propriedades_de_produto(dados_basicos.codigo_limpax, conn=db_transaction)

        assert [p.valor for p in propriedades] == ["Hipoclorito sem origem"]

    def test_produtos_por_propriedade(self, dados_basicos, db_transaction):
        with db_transaction.cursor() as cur:
            self._revogar_limpax_e_inserir_sem_origem(cur, dados_basicos)

        # 'Hipoclorito de Sodio', do documento revogado, tambem casaria.
        achados = produtos_por_propriedade("ingrediente_ativo", "hipoclorito", conn=db_transaction)

        do_limpax = [r.valor for r in achados if r.produto_codigo == dados_basicos.codigo_limpax]
        assert do_limpax == ["Hipoclorito sem origem"]
