"""
Fixtures compartilhadas do pytest.

Estrategia de teste de integracao com banco:
- Cada teste que precisa de banco pede a fixture `db_transaction`.
- A fixture abre uma conexao, inicia transacao, entrega para o teste.
- No teardown a transacao e sempre revertida (ROLLBACK).
- Nenhum dado de teste persiste — o banco de dev nunca e sujado.

Fixture `dados_basicos`: mini catalogo consistente (produtos + documentos
+ propriedades + chunks) para testes que precisam de dados. Retorna
um `dataclass` com os IDs gerados para os testes referenciarem sem
adivinhar UUIDs.

Marker `integration`: testes que precisam de banco recebem
`@pytest.mark.integration`. Permite rodar em modos separados:
    pytest -m "not integration"   # so unit tests puros (rapido, offline)
    pytest -m integration         # so testes de banco
    pytest                        # tudo
"""

import uuid
from dataclasses import dataclass

import psycopg2
import pytest

from src import config


def pytest_configure(config):  # noqa: A002 — nome do hook do pytest
    """Registra o marker 'integration' para o pytest nao emitir warning."""
    config.addinivalue_line(
        "markers",
        "integration: teste que requer banco de dados (rollback por transacao)",
    )


@pytest.fixture
def db_transaction():
    """
    Abre conexao, inicia transacao, entrega para o teste, faz ROLLBACK.

    Cada teste recebe uma conexao "limpa" no sentido de: qualquer coisa
    que ele insira sera revertida no teardown. Testes podem chamar
    conn.commit() a vontade — o savepoint interno segura tudo ate o
    ROLLBACK final.
    """
    # Perfil admin: a fixture MONTA cenario (INSERT em produtos, documentos,
    # chunks), o que e operacao de manutencao, nao de runtime. A aplicacao
    # roda com bot_app (least privilege) e recebe esta conexao via injecao
    # (`conn=`), preservando o caminho real de leitura sob teste.
    conn = psycopg2.connect(config.DATABASE_URL_ADMIN)
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@dataclass(frozen=True)
class IdsFixture:
    """IDs gerados pela fixture dados_basicos, para os testes referenciarem."""

    codigo_limpax: int
    codigo_folhabela: int
    codigo_descontinuado: int
    doc_fds_limpax_id: str
    doc_ft_folhabela_id: str


@pytest.fixture
def dados_basicos(db_transaction) -> IdsFixture:
    """
    Insere um mini-catalogo consistente dentro da transacao do teste.

    Conteudo:
    - 3 produtos: 2 ativos (limpax, folhabela) + 1 descontinuado
    - 2 documentos: FDS da limpax e FT da folhabela
    - Vinculos produto_documento
    - 2 propriedades: ingrediente_ativo e ph da limpax
    - 2 chunks com embeddings faceis de reproduzir em teste

    Retorna os codigos/IDs gerados para o teste usar sem hardcode.
    """
    codigo_limpax = 800001
    codigo_folhabela = 800002
    codigo_descontinuado = 800003

    doc_fds_limpax_id = str(uuid.uuid4())
    doc_ft_folhabela_id = str(uuid.uuid4())

    with db_transaction.cursor() as cur:
        # --- produtos ---
        cur.execute(
            """
            INSERT INTO produtos (codigo, descricao, marca, texto_busca, ativo)
            VALUES
                (%s, 'AGUA SANITARIA LIMPAX 5L', 'LIMPAX',
                    'agua sanitaria limpax 5l', TRUE),
                (%s, 'PAPEL HIGIENICO FOLHABELA 300M', 'FOLHABELA',
                    'papel higienico folhabela 300m', TRUE),
                (%s, 'PRODUTO DESCONTINUADO XPTO', 'XPTO',
                    'produto descontinuado xpto', FALSE)
            """,
            (codigo_limpax, codigo_folhabela, codigo_descontinuado),
        )

        # --- documentos ---
        cur.execute(
            """
            INSERT INTO documentos
                (id, drive_file_id, nome_arquivo, marca_pasta, tipo, url_drive)
            VALUES
                (%s, %s, 'FDS Agua Sanitaria Limpax.pdf', 'limpax', 'FDS',
                    'https://drive.google.com/fake/fds-limpax'),
                (%s, %s, 'FT Papel Higienico Folhabela.pdf', 'folhabela', 'FT',
                    'https://drive.google.com/fake/ft-folhabela')
            """,
            (
                doc_fds_limpax_id, f"test-{uuid.uuid4()}",
                doc_ft_folhabela_id, f"test-{uuid.uuid4()}",
            ),
        )

        # --- vinculos produto_documento ---
        cur.execute(
            """
            INSERT INTO produto_documento
                (produto_codigo, documento_id, confianca_match, metodo_match)
            VALUES
                (%s, %s, 1.00, 'test-fixture'),
                (%s, %s, 1.00, 'test-fixture')
            """,
            (
                codigo_limpax, doc_fds_limpax_id,
                codigo_folhabela, doc_ft_folhabela_id,
            ),
        )

        # --- propriedades da agua sanitaria limpax ---
        cur.execute(
            """
            INSERT INTO propriedades_produto
                (produto_codigo, documento_id, campo, valor,
                 valor_numerico, unidade, confianca)
            VALUES
                (%s, %s, 'ingrediente_ativo', 'Hipoclorito de Sodio',
                    NULL, NULL, 0.95),
                (%s, %s, 'ph', '10 - 13',
                    NULL, NULL, 0.90)
            """,
            (
                codigo_limpax, doc_fds_limpax_id,
                codigo_limpax, doc_fds_limpax_id,
            ),
        )

        # --- chunks (embeddings determinsticos para o search_vector) ---
        # Vetor 1 nas primeiras 3 dimensoes, zero no resto — permite ao
        # teste construir queries com similaridade cosseno conhecida.
        emb_limpax = "[" + ",".join(["1"] * 3 + ["0"] * 1533) + "]"
        emb_folhabela = "[" + ",".join(["0"] * 3 + ["1"] * 3 + ["0"] * 1530) + "]"

        cur.execute(
            """
            INSERT INTO chunks
                (documento_id, produto_codigo, chunk_index, texto,
                 embedding, num_tokens)
            VALUES
                (%s, %s, 0,
                 'Em caso de contato com os olhos, lave com agua em '
                 'abundancia durante 15 minutos.',
                 %s::vector, 20),
                (%s, %s, 0,
                 'Papel higienico folha dupla, gramatura 16 g/m2, '
                 'metragem de 300 m por rolo.',
                 %s::vector, 20)
            """,
            (
                doc_fds_limpax_id, codigo_limpax, emb_limpax,
                doc_ft_folhabela_id, codigo_folhabela, emb_folhabela,
            ),
        )

    return IdsFixture(
        codigo_limpax=codigo_limpax,
        codigo_folhabela=codigo_folhabela,
        codigo_descontinuado=codigo_descontinuado,
        doc_fds_limpax_id=doc_fds_limpax_id,
        doc_ft_folhabela_id=doc_ft_folhabela_id,
    )