"""Testes unitarios da validacao de propriedades extraidas (sem chamadas reais)."""

import pytest

from src.extraction import PropriedadeExtraida, parse_and_validate


def _json_com(props: str) -> str:
    return '{"propriedades": [' + props + "]}"


PROP_VALIDA = '{"campo": "ph", "valor": "10 - 13", "valor_numerico": null, "unidade": null, "confianca": 0.9}'


def test_propriedade_valida_e_aceita():
    resultado = parse_and_validate(_json_com(PROP_VALIDA))

    assert len(resultado) == 1
    assert resultado[0] == PropriedadeExtraida(
        campo="ph", valor="10 - 13", valor_numerico=None, unidade=None, confianca=0.9
    )


def test_json_invalido_levanta_value_error():
    with pytest.raises(ValueError):
        parse_and_validate("isto nao e json")


def test_json_sem_chave_propriedades_levanta_value_error():
    with pytest.raises(ValueError):
        parse_and_validate('{"outra_chave": []}')


def test_campo_fora_do_vocabulario_e_descartado():
    prop = '{"campo": "cor_favorita", "valor": "azul", "valor_numerico": null, "unidade": null, "confianca": 0.9}'
    assert parse_and_validate(_json_com(prop)) == []


def test_confianca_abaixo_de_meio_e_descartada():
    prop = '{"campo": "ph", "valor": "10", "valor_numerico": 10, "unidade": null, "confianca": 0.4}'
    assert parse_and_validate(_json_com(prop)) == []


def test_valor_vazio_e_descartado():
    prop = '{"campo": "ph", "valor": "  ", "valor_numerico": null, "unidade": null, "confianca": 0.9}'
    assert parse_and_validate(_json_com(prop)) == []


def test_valor_numerico_e_convertido_para_float():
    prop = '{"campo": "gramatura", "valor": "16 g/m2", "valor_numerico": 16, "unidade": "g/m2", "confianca": 0.95}'
    resultado = parse_and_validate(_json_com(prop))

    assert resultado[0].valor_numerico == 16.0
    assert isinstance(resultado[0].valor_numerico, float)


def test_valor_numerico_invalido_vira_none_sem_descartar():
    prop = '{"campo": "gramatura", "valor": "16", "valor_numerico": "dezesseis", "unidade": null, "confianca": 0.9}'
    resultado = parse_and_validate(_json_com(prop))

    assert len(resultado) == 1
    assert resultado[0].valor_numerico is None


def test_mesmo_campo_pode_repetir():
    props = (
        '{"campo": "ingrediente_ativo", "valor": "Hipoclorito de Sodio", "valor_numerico": null, "unidade": null, "confianca": 0.9}, '
        '{"campo": "ingrediente_ativo", "valor": "Peroxido de Hidrogenio", "valor_numerico": null, "unidade": null, "confianca": 0.8}'
    )
    assert len(parse_and_validate(_json_com(props))) == 2


def test_cerca_de_markdown_e_removida():
    raw = "```json\n" + _json_com(PROP_VALIDA) + "\n```"
    assert len(parse_and_validate(raw)) == 1


def test_lista_vazia_retorna_vazio():
    assert parse_and_validate('{"propriedades": []}') == []
    
def test_classificacao_abnt_sem_classe_e_reprovada_pelo_validador():
    prop = '{"campo": "classificacao_abnt", "valor": "Norma ABNT-NBR 14725-1", "valor_numerico": null, "unidade": null, "confianca": 0.9}'
    assert parse_and_validate(_json_com(prop)) == []


def test_classificacao_abnt_com_classe_passa_no_validador():
    prop = '{"campo": "classificacao_abnt", "valor": "Classe 1 - ABNT 15464-11", "valor_numerico": null, "unidade": null, "confianca": 0.9}'
    assert len(parse_and_validate(_json_com(prop))) == 1

def test_classificacao_abnt_sintetizada_sem_norma_15464_e_reprovada():
    prop = '{"campo": "classificacao_abnt", "valor": "Classe 5 - NBR 14725", "valor_numerico": null, "unidade": null, "confianca": 0.8}'
    assert parse_and_validate(_json_com(prop)) == []