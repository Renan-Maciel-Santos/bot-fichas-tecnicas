"""Testes unitarios do modulo de chunking."""

from src.chunking import Chunk, chunk_text, count_tokens
from src.config import CHUNK_SIZE, MIN_CHUNK_SIZE


def test_texto_vazio_retorna_lista_vazia():
    assert chunk_text("") == []


def test_texto_so_com_espacos_retorna_lista_vazia():
    assert chunk_text("   \n\n  \t ") == []


def test_texto_menor_que_min_chunk_size_e_descartado():
    texto = "Produto de limpeza."  # muito abaixo de MIN_CHUNK_SIZE tokens
    assert chunk_text(texto) == []


def test_texto_curto_mas_valido_gera_um_unico_chunk():
    # ~150 palavras -> acima de MIN_CHUNK_SIZE, abaixo de CHUNK_SIZE
    texto = " ".join(["detergente neutro concentrado"] * 50)
    chunks = chunk_text(texto)

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert MIN_CHUNK_SIZE <= chunks[0].num_tokens <= CHUNK_SIZE


def test_texto_longo_gera_multiplos_chunks_indexados():
    paragrafo = " ".join(["ficha tecnica do produto"] * 100)
    texto = "\n\n".join([paragrafo] * 10)  # bem acima de CHUNK_SIZE
    chunks = chunk_text(texto)

    assert len(chunks) > 1
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_nenhum_chunk_excede_chunk_size():
    paragrafo = " ".join(["hipoclorito de sodio solucao"] * 200)
    texto = "\n\n".join([paragrafo] * 5)
    chunks = chunk_text(texto)

    assert all(c.num_tokens <= CHUNK_SIZE for c in chunks)


def test_chunks_sao_imutaveis():
    texto = " ".join(["peroxido de hidrogenio"] * 80)
    chunk = chunk_text(texto)[0]

    try:
        chunk.texto = "alterado"
        assert False, "Chunk deveria ser imutavel"
    except AttributeError:
        pass


def test_count_tokens_retorna_inteiro_positivo():
    assert count_tokens("agua sanitaria") > 0