"""
Testes do estado de conversa (desambiguacao de produto).

Duas camadas:
- `interpretar_selecao` e funcao pura e concentra a decisao de risco
  ("isto e uma escolha ou uma pergunta nova?"). Testada sem banco,
  com foco nos casos em que ela deve RECUSAR interpretar: responder
  sobre o produto errado e pior do que reclassificar a pergunta.
- salvar/buscar/limpar tocam o banco, com rollback por transacao.
"""

import pytest

from src.query.contexto import (
    CandidatoPendente,
    buscar_contexto,
    interpretar_selecao,
    limpar_contexto,
    salvar_contexto,
)

VENDEDOR = "5511900000777"

CANDIDATOS = [
    CandidatoPendente(ordem=1, codigo=9000008, descricao="AGUA SANITARIA 5L CASACLEAN", marca="CASACLEAN"),
    CandidatoPendente(ordem=2, codigo=9000001, descricao="AGUA SANITARIA LIMPAX 1L", marca="LIMPAX"),
    CandidatoPendente(ordem=3, codigo=9000009, descricao="AGUA SANITARIA 5L LARCLEAN", marca="LARCLEAN"),
]


# --- interpretar_selecao: aceita ------------------------------------

class TestSelecaoAceita:
    def test_ordinal_simples(self):
        assert interpretar_selecao("2", CANDIDATOS) == 9000001

    def test_ordinal_com_palavras(self):
        assert interpretar_selecao("quero o 2", CANDIDATOS) == 9000001

    def test_ordinal_com_pontuacao(self):
        assert interpretar_selecao("2.", CANDIDATOS) == 9000001

    def test_primeiro_e_ultimo_da_lista(self):
        assert interpretar_selecao("1", CANDIDATOS) == 9000008
        assert interpretar_selecao("3", CANDIDATOS) == 9000009

    def test_codigo_do_produto(self):
        assert interpretar_selecao("9000009", CANDIDATOS) == 9000009

    def test_codigo_com_prefixo(self):
        assert interpretar_selecao("codigo 9000001", CANDIDATOS) == 9000001

    def test_marca_isolada(self):
        assert interpretar_selecao("limpax", CANDIDATOS) == 9000001

    def test_marca_com_acento_e_maiuscula(self):
        """Vendedor digita como quiser; a comparacao normaliza."""
        candidatos = [
            CandidatoPendente(ordem=1, codigo=1, descricao="X", marca="LÍMPIDA"),
            CandidatoPendente(ordem=2, codigo=2, descricao="Y", marca="LIMPAX"),
        ]
        assert interpretar_selecao("Limpida", candidatos) == 1

    def test_codigo_vence_ordinal_quando_colidem(self):
        """
        Se um candidato tem codigo 2, "2" e o codigo dele, nao a posicao.
        Digitar o codigo e mais explicito do que contar a lista.
        """
        candidatos = [
            CandidatoPendente(ordem=1, codigo=10, descricao="A", marca="A"),
            CandidatoPendente(ordem=2, codigo=2, descricao="B", marca="B"),
            CandidatoPendente(ordem=3, codigo=30, descricao="C", marca="C"),
        ]
        assert interpretar_selecao("2", candidatos) == 2


# --- interpretar_selecao: recusa ------------------------------------

class TestSelecaoRecusada:
    def test_pergunta_nova_com_codigo_nao_e_escolha(self):
        """
        Pergunta completa passa pelo classificador, mesmo citando um
        codigo da lista: a intencao pode ter mudado.
        """
        assert interpretar_selecao(
            "qual o pH do produto 9000001 mesmo?", CANDIDATOS
        ) is None

    def test_numero_fora_da_faixa(self):
        assert interpretar_selecao("7", CANDIDATOS) is None

    def test_zero_nao_e_ordinal(self):
        assert interpretar_selecao("0", CANDIDATOS) is None

    def test_dois_numeros_e_ambiguo(self):
        assert interpretar_selecao("1 e 2", CANDIDATOS) is None

    def test_texto_sem_relacao(self):
        assert interpretar_selecao("obrigado", CANDIDATOS) is None

    def test_marca_que_nao_isola_candidato(self):
        """Duas opcoes da mesma marca: a marca nao decide, precisa do numero."""
        candidatos = [
            CandidatoPendente(ordem=1, codigo=1, descricao="LIMPAX 1L", marca="LIMPAX"),
            CandidatoPendente(ordem=2, codigo=2, descricao="LIMPAX 5L", marca="LIMPAX"),
        ]
        assert interpretar_selecao("limpax", candidatos) is None

    def test_mensagem_vazia(self):
        assert interpretar_selecao("", CANDIDATOS) is None

    def test_sem_candidatos(self):
        assert interpretar_selecao("2", []) is None


# --- persistencia ----------------------------------------------------

@pytest.mark.integration
class TestPersistencia:
    def test_salva_e_le_de_volta(self, db_transaction):
        salvar_contexto(
            vendedor_id=VENDEDOR,
            intencao="propriedades_produto",
            campo="ph",
            tipo_documento=None,
            pergunta_original="qual o pH da agua sanitaria?",
            candidatos=CANDIDATOS,
            conn=db_transaction,
        )

        ctx = buscar_contexto(VENDEDOR, conn=db_transaction)

        assert ctx is not None
        assert ctx.intencao == "propriedades_produto"
        assert ctx.campo == "ph"
        assert ctx.pergunta_original == "qual o pH da agua sanitaria?"
        assert [c.codigo for c in ctx.candidatos] == [9000008, 9000001, 9000009]
        assert [c.ordem for c in ctx.candidatos] == [1, 2, 3]

    def test_tipo_documento_sobrevive(self, db_transaction):
        """"manda a FT do X" -> "2" precisa lembrar que era FT, nao FDS."""
        salvar_contexto(
            vendedor_id=VENDEDOR,
            intencao="fetch_documento",
            campo=None,
            tipo_documento="FT",
            pergunta_original="manda a ficha tecnica da agua sanitaria",
            candidatos=CANDIDATOS,
            conn=db_transaction,
        )

        ctx = buscar_contexto(VENDEDOR, conn=db_transaction)

        assert ctx.tipo_documento == "FT"

    def test_pendencia_nova_substitui_a_anterior(self, db_transaction):
        """Uma linha por vendedor: a pergunta mais recente e a que vale."""
        salvar_contexto(
            VENDEDOR, "propriedades_produto", "ph", None, "primeira",
            CANDIDATOS, conn=db_transaction,
        )
        salvar_contexto(
            VENDEDOR, "fetch_documento", None, "FDS", "segunda",
            CANDIDATOS[:2], conn=db_transaction,
        )

        ctx = buscar_contexto(VENDEDOR, conn=db_transaction)

        assert ctx.intencao == "fetch_documento"
        assert ctx.pergunta_original == "segunda"
        assert len(ctx.candidatos) == 2

    def test_vendedor_sem_pendencia(self, db_transaction):
        assert buscar_contexto("5511900000000", conn=db_transaction) is None

    def test_vendedor_id_vazio_nao_consulta(self, db_transaction):
        assert buscar_contexto("", conn=db_transaction) is None

    def test_limpar_remove_a_pendencia(self, db_transaction):
        salvar_contexto(
            VENDEDOR, "fetch_documento", None, None, "p", CANDIDATOS,
            conn=db_transaction,
        )
        limpar_contexto(VENDEDOR, conn=db_transaction)

        assert buscar_contexto(VENDEDOR, conn=db_transaction) is None

    def test_pendencia_expirada_e_ignorada(self, db_transaction):
        """
        TTL: um "2" que chega muito depois nao se refere mais aquela
        lista. A pendencia velha nao pode capturar a mensagem nova.
        """
        salvar_contexto(
            VENDEDOR, "fetch_documento", None, None, "p", CANDIDATOS,
            conn=db_transaction,
        )
        with db_transaction.cursor() as cur:
            cur.execute(
                "UPDATE contexto_conversa SET criado_em = now() - interval '2 days' "
                "WHERE vendedor_id = %s",
                (VENDEDOR,),
            )

        assert buscar_contexto(VENDEDOR, conn=db_transaction) is None
