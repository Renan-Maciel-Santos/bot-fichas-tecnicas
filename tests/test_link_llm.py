"""
Testes da validação da Camada 2 (LLM), sem chamar a API.

Foco no que impede um vínculo errado de ser gravado: código fora da
lista fechada, trecho citado que não existe no documento e respostas
fora do formato, que devem virar "duvida" e nunca "vincular".
"""

import json

from src.link_llm import interpretar_resposta

CANDIDATOS = {101, 102, 103}
FONTE = (
    "FDS-CASACLEAN-DESINFETANTE.pdf Ficha de dados de Segurança PRODUTO "
    "DESINFETANTE CASACLEAN PARA USO GERAL,\nFLORAL, LAVANDA, TALCO, CASA LIMPA"
)


def _resposta(decisao, produtos=(), justificativa="ok"):
    return json.dumps({"decisao": decisao, "produtos": list(produtos), "justificativa": justificativa})


def test_vincula_com_trecho_literal():
    raw = _resposta("vincular", [{"codigo": 101, "trecho": "DESINFETANTE CASACLEAN PARA USO GERAL"}])
    decisao = interpretar_resposta(raw, CANDIDATOS, FONTE)
    assert (decisao.decisao, decisao.codigos) == ("vincular", (101,))


def test_trecho_sobrevive_a_quebra_de_linha_acento_e_pontuacao():
    raw = _resposta("vincular", [{"codigo": 102, "trecho": "uso geral floral lavanda"}])
    assert interpretar_resposta(raw, CANDIDATOS, FONTE).codigos == (102,)


def test_trecho_inexistente_anula_a_resposta_inteira():
    raw = _resposta("vincular", [
        {"codigo": 101, "trecho": "DESINFETANTE CASACLEAN"},
        {"codigo": 102, "trecho": "DESINFETANTE EUCALIPTO"},
    ])
    decisao = interpretar_resposta(raw, CANDIDATOS, FONTE)
    assert (decisao.decisao, decisao.codigos) == ("duvida", ())


def test_codigo_fora_da_lista_vira_duvida():
    raw = _resposta("vincular", [{"codigo": 999, "trecho": "DESINFETANTE CASACLEAN"}])
    assert interpretar_resposta(raw, CANDIDATOS, FONTE).decisao == "duvida"


def test_trecho_curto_demais_nao_vale():
    raw = _resposta("vincular", [{"codigo": 101, "trecho": "de"}])
    assert interpretar_resposta(raw, CANDIDATOS, FONTE).decisao == "duvida"


def test_vincular_sem_produtos_vira_duvida():
    assert interpretar_resposta(_resposta("vincular"), CANDIDATOS, FONTE).decisao == "duvida"


def test_nenhum_e_duvida_passam_sem_codigos():
    for decisao in ("nenhum", "duvida"):
        resultado = interpretar_resposta(_resposta(decisao), CANDIDATOS, FONTE)
        assert (resultado.decisao, resultado.codigos) == (decisao, ())


def test_json_invalido_vira_duvida():
    assert interpretar_resposta("nao e json", CANDIDATOS, FONTE).decisao == "duvida"


def test_codigos_repetidos_saem_uma_vez():
    raw = _resposta("vincular", [
        {"codigo": 101, "trecho": "FLORAL"},
        {"codigo": 101, "trecho": "LAVANDA"},
    ])
    assert interpretar_resposta(raw, CANDIDATOS, FONTE).codigos == (101,)
