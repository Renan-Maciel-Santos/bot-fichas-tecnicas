"""
Testes de integracao para src/query/search_vector.py.

Estrategia:
- Banco real via fixture db_transaction (rollback no teardown).
- Dados via fixture dados_basicos (mini-catalogo com 2 chunks de
  embeddings deterministicos).
- embed_texts e mockado: o objetivo aqui e validar o SQL da busca
  vetorial, nao a qualidade do embedding. O mock devolve vetores
  previsiveis para permitir asserts sobre similaridade calculada
  pelo pgvector.

Contrato dos vetores da fixture dados_basicos:
- Chunk do produto Limpax: [1,1,1, 0,0,...,0]
- Chunk do produto Folhabela: [0,0,0, 1,1,1, 0,...,0]
- Sao ortogonais entre si (similaridade cosseno = 0) e cada um retorna
  similaridade = 1 quando a query for identico a ele.
"""

import pytest

from src.query import search_vector
from src.query.search_vector import LIMIAR_SIMILARIDADE, buscar_chunks


# --- Helpers de vetor -------------------------------------------------

def _vetor_como_do_limpax() -> list[float]:
    """Mesmo vetor do chunk do Limpax na fixture. Similaridade == 1.0."""
    return [1.0, 1.0, 1.0] + [0.0] * 1533


def _vetor_ortogonal_a_tudo() -> list[float]:
    """Vetor unitario numa dimensao que nao aparece em nenhum chunk."""
    v = [0.0] * 1536
    v[100] = 1.0  # dimensao 100 esta zerada em ambos os chunks da fixture
    return v


# --- Testes ------------------------------------------------------------

@pytest.mark.integration
class TestBuscarChunks:
    def test_query_identica_a_chunk_retorna_similaridade_alta(
        self, dados_basicos, db_transaction, monkeypatch
    ):
        """Query = vetor do chunk do Limpax -> cosseno = 1.0."""
        monkeypatch.setattr(
            search_vector, "embed_texts",
            lambda textos: [_vetor_como_do_limpax()],
        )

        chunks = buscar_chunks("qualquer pergunta", conn=db_transaction)

        assert len(chunks) >= 1
        # Chunk do limpax deve estar em primeiro, similaridade proxima de 1.
        assert chunks[0].produto_codigo == dados_basicos.codigo_limpax
        assert chunks[0].similaridade > 0.99

    def test_query_ortogonal_a_todos_os_chunks_retorna_vazio(
        self, dados_basicos, db_transaction, monkeypatch
    ):
        """Similaridade zero em todos -> nenhum passa o limiar 0.50."""
        monkeypatch.setattr(
            search_vector, "embed_texts",
            lambda textos: [_vetor_ortogonal_a_tudo()],
        )

        chunks = buscar_chunks("qualquer pergunta", conn=db_transaction)
        assert chunks == []

    def test_filtro_por_produto_codigo_restringe_resultado(
        self, dados_basicos, db_transaction, monkeypatch
    ):
        """Filtro produto_codigo=folhabela -> nao retorna chunk do limpax."""
        # Mock devolve o vetor do limpax: sem filtro, retornaria o chunk
        # do limpax. Com filtro pelo folhabela, nao pode voltar nada dele.
        monkeypatch.setattr(
            search_vector, "embed_texts",
            lambda textos: [_vetor_como_do_limpax()],
        )

        chunks = buscar_chunks(
            "qualquer pergunta",
            filtro_produto_codigo=dados_basicos.codigo_folhabela,
            conn=db_transaction,
        )

        # Chunk do folhabela tem similaridade 0 com o vetor da query,
        # entao nao passa o limiar. Resultado esperado: vazio.
        for c in chunks:
            assert c.produto_codigo == dados_basicos.codigo_folhabela

    def test_pergunta_vazia_nao_chama_embed_texts_nem_banco(
        self, db_transaction, monkeypatch
    ):
        """Early return: string vazia devolve [] sem tocar nada."""
        chamadas: list[str] = []
        monkeypatch.setattr(
            search_vector, "embed_texts",
            lambda textos: chamadas.append(textos) or [_vetor_como_do_limpax()],
        )

        assert buscar_chunks("", conn=db_transaction) == []
        assert buscar_chunks("   ", conn=db_transaction) == []
        assert chamadas == []  # nunca chamou embed_texts

    def test_top_k_limita_quantidade_de_chunks_retornados(
        self, dados_basicos, db_transaction, monkeypatch
    ):
        """Com top_k=1, retorna no maximo 1 chunk mesmo se varios batem."""
        # Vetor que bate com AMBOS os chunks (medio termo entre eles).
        # 3 primeiras dimensoes = 1 (bate limpax), 3 seguintes = 1 (bate folhabela).
        vetor_hibrido = [1.0] * 6 + [0.0] * 1530
        monkeypatch.setattr(
            search_vector, "embed_texts",
            lambda textos: [vetor_hibrido],
        )

        chunks = buscar_chunks(
            "qualquer", top_k=1, conn=db_transaction
        )

        assert len(chunks) == 1

    def test_limiar_customizado_filtra_agressivamente(
        self, dados_basicos, db_transaction, monkeypatch
    ):
        """Com limiar 0.99, so passa quem for quase identico."""
        # Vetor com similaridade ~0.7 com o do Limpax (nao chega a 0.99).
        # Coeficiente escolhido para dar cosseno abaixo do limiar apertado.
        v = [0.7, 0.7, 0.7, 0.7, 0.0, 0.0] + [0.0] * 1530
        monkeypatch.setattr(
            search_vector, "embed_texts",
            lambda textos: [v],
        )

        # Com limiar default (0.50), deveria passar. Com 0.99, nao.
        chunks_default = buscar_chunks("q", conn=db_transaction)
        chunks_estrito = buscar_chunks(
            "q", limiar=0.99, conn=db_transaction
        )

        assert len(chunks_default) >= 1
        assert chunks_estrito == []

    def test_limiar_default_confirma_constante_do_modulo(self):
        """Salvaguarda: se alguem mexer no LIMIAR_SIMILARIDADE, este teste avisa."""
        assert LIMIAR_SIMILARIDADE == 0.50

@pytest.mark.integration
def test_chunk_de_documento_revogado_nao_volta(dados_basicos, db_transaction, monkeypatch):
    """Documento revogado (ex.: copia duplicada) sai da busca por texto."""
    monkeypatch.setattr(search_vector, "embed_texts", lambda textos: [_vetor_como_do_limpax()])
    with db_transaction.cursor() as cur:
        cur.execute(
            "UPDATE documentos SET revogado_em = NOW() WHERE id = %s",
            (dados_basicos.doc_fds_limpax_id,),
        )

    chunks = buscar_chunks("qualquer pergunta", conn=db_transaction)

    assert all(c.documento_id != dados_basicos.doc_fds_limpax_id for c in chunks)
