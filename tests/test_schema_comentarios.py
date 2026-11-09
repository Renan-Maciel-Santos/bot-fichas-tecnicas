"""
Faz valer a convenção de documentação do schema.

O README pede comentário nas tabelas e nas colunas não-óbvias. Isso
tinha se perdido: as explicações existiam como comentário `--` dentro
das migrations, invisíveis para quem inspeciona o banco com `\\d+` ou
information_schema — que é como alguém vai ler o schema na hora de
medir o piloto.

A lista `OBVIAS` é o outro lado da convenção: colunas que se explicam
sozinhas e cujo comentário seria ruído. Coluna nova entra numa das
duas categorias, e a escolha vira explícita em vez de acontecer por
esquecimento.
"""

import pytest

# Colunas dispensadas de comentário por serem auto-evidentes.
# Manter curta: cada entrada aqui é uma explicação que ninguém terá.
OBVIAS = {
    "id",
    "created_at",
    "updated_at",
    "criado_em",
    "atualizado_em",
    "revisado_em",
    # Conteúdo textual cujo nome já diz tudo.
    "nome",
    "descricao",
    "marca",
    "texto",
    "pergunta",
    "resposta",
    # Chaves estrangeiras diretas para tabelas que já têm comentário.
    "produto_codigo",
    "documento_id",
    # Metadados de arquivo, na unidade que o nome indica.
    "tamanho_bytes",
    "num_paginas",
}

SQL_TABELAS = """
    SELECT c.relname, obj_description(c.oid)
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
"""

SQL_COLUNAS = """
    SELECT c.relname, a.attname, col_description(c.oid, a.attnum)
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
                       AND a.attnum > 0
                       AND NOT a.attisdropped
    WHERE n.nspname = 'public' AND c.relkind = 'r'
"""


@pytest.fixture
def tabelas(db_transaction):
    with db_transaction.cursor() as cur:
        cur.execute(SQL_TABELAS)
        return cur.fetchall()


@pytest.fixture
def colunas(db_transaction):
    with db_transaction.cursor() as cur:
        cur.execute(SQL_COLUNAS)
        return cur.fetchall()


@pytest.mark.integration
class TestComentariosDoSchema:
    def test_toda_tabela_tem_comentario(self, tabelas):
        sem = sorted(nome for nome, comentario in tabelas if not comentario)
        assert sem == [], (
            "tabelas sem COMMENT ON TABLE: "
            + ", ".join(sem)
            + " — ver a convenção no README."
        )

    def test_toda_coluna_nao_obvia_tem_comentario(self, colunas):
        sem = sorted(
            f"{tabela}.{coluna}"
            for tabela, coluna, comentario in colunas
            if not comentario and coluna not in OBVIAS
        )
        assert sem == [], (
            "colunas sem COMMENT ON COLUMN: "
            + ", ".join(sem)
            + " — comente na migração, ou adicione a OBVIAS se o nome "
            "realmente bastar."
        )

    def test_lista_de_obvias_nao_esconde_coluna_inexistente(self, colunas):
        """
        Coluna renomeada ou removida deixa lixo em OBVIAS, que passa a
        dispensar do comentário uma coluna futura de mesmo nome.
        """
        existentes = {coluna for _, coluna, _ in colunas}
        orfas = sorted(OBVIAS - existentes)

        assert orfas == [], f"entradas de OBVIAS sem coluna correspondente: {orfas}"
