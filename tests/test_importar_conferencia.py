"""
Testes da leitura das respostas do cliente na planilha de conferência.

Foco no texto livre, onde o cliente escreve do jeito que quiser: o "e"
de "D e E" é conjunção, não a opção E, e o nome do produto certo pode
vir em qualquer das duas colunas livres, com espaçamento diferente do
catálogo.
"""

import pytest

from scripts.importar_conferencia import interpretar_resposta

OPCOES = {"A": 101, "B": 102, "C": 103, "D": 104, "E": 105}
CATALOGO = {"REMOVEDORPERFUMADO1LFORTEX": 900}


def _interpretar(resposta, quais=None, observacao=None):
    return interpretar_resposta(resposta, quais, observacao, OPCOES, CATALOGO)


def test_letra_unica():
    assert _interpretar("C") == ("103", False)


@pytest.mark.parametrize("quais, esperado", [
    ("D e E", "104;105"),
    ("A e D", "101;104"),
    ("A, B, C e D", "101;102;103;104"),
    ("c, d e e", "103;104;105"),
])
def test_mais_de_um_nao_confunde_conjuncao_com_opcao(quais, esperado):
    assert _interpretar("MAIS DE UM", quais) == (esperado, False)


def test_mais_de_um_sem_letras_fica_pendente():
    assert _interpretar("MAIS DE UM", None) == ("DUVIDA", True)


def test_nenhum_sem_texto():
    assert _interpretar("NENHUM") == ("NENHUM", False)


def test_nenhum_com_nome_na_coluna_de_observacao():
    assert _interpretar("NENHUM", None, "REMOVEDOR PERFUMADO 1 L FORTEX") == ("900", False)


def test_nenhum_com_nome_fora_do_catalogo_fica_pendente():
    codigo, pendente = _interpretar(
        "NENHUM", "REMOVEDOR PERFUMADO 1L FORTEX e PRODUTO QUE NAO EXISTE",
    )
    assert (codigo, pendente) == ("900", True)


def test_nao_sei_vira_duvida():
    assert _interpretar("Nao sei") == ("DUVIDA", False)
