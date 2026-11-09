"""Testes unitarios do modulo de embeddings (com mocks, sem chamadas reais)."""

from unittest.mock import MagicMock, patch

import pytest

from src.embeddings import BATCH_SIZE, embed_texts


def _fake_response(num_items: int) -> MagicMock:
    """Cria uma resposta simulada da API de embeddings."""
    response = MagicMock()
    response.data = [
        MagicMock(index=i, embedding=[float(i)] * 1536) for i in range(num_items)
    ]
    return response


def test_lista_vazia_retorna_lista_vazia_sem_chamar_api():
    with patch("src.embeddings.get_client") as mock_get_client:
        assert embed_texts([]) == []
        mock_get_client.assert_not_called()


def test_texto_vazio_levanta_value_error():
    with pytest.raises(ValueError):
        embed_texts(["texto valido", "   "])


def test_retorna_um_vetor_por_texto_na_mesma_ordem():
    textos = ["detergente", "desinfetante", "alvejante"]

    with patch("src.embeddings.get_client") as mock_get_client:
        client = MagicMock()
        client.embeddings.create.return_value = _fake_response(len(textos))
        mock_get_client.return_value = client

        resultado = embed_texts(textos)

    assert len(resultado) == 3
    assert resultado[0] == [0.0] * 1536
    assert resultado[2] == [2.0] * 1536


def test_lotes_respeitam_batch_size():
    textos = [f"texto {i}" for i in range(BATCH_SIZE + 5)]

    with patch("src.embeddings.get_client") as mock_get_client:
        client = MagicMock()
        client.embeddings.create.side_effect = [
            _fake_response(BATCH_SIZE),
            _fake_response(5),
        ]
        mock_get_client.return_value = client

        resultado = embed_texts(textos)

    assert client.embeddings.create.call_count == 2
    assert len(resultado) == BATCH_SIZE + 5


def test_retry_em_erro_transitorio(monkeypatch):
    from openai import RateLimitError

    # Elimina a espera real do backoff no teste
    monkeypatch.setattr("src.embeddings.time.sleep", lambda _: None)

    rate_limit_error = RateLimitError(
        message="rate limited",
        response=MagicMock(status_code=429, headers={}),
        body=None,
    )

    with patch("src.embeddings.get_client") as mock_get_client:
        client = MagicMock()
        # Falha 2x, sucesso na 3a tentativa
        client.embeddings.create.side_effect = [
            rate_limit_error,
            rate_limit_error,
            _fake_response(1),
        ]
        mock_get_client.return_value = client

        resultado = embed_texts(["agua sanitaria"])

    assert client.embeddings.create.call_count == 3
    assert len(resultado) == 1