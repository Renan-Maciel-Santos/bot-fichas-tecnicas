"""Testes unitarios da validacao do classificador (sem chamadas reais)."""

from src.query.classifier import Classificacao, parse_and_validate


def _json(intencao, produto=None, campo=None, valor=None, tipo=None):
    import json
    return json.dumps({
        "intencao": intencao,
        "produto_termo": produto,
        "campo": campo,
        "valor_busca": valor,
        "tipo_documento": tipo,
    })


def test_classificacao_valida_e_aceita():
    c = parse_and_validate(_json("propriedades_produto", produto="agua sanitaria limpax", campo="ph"))
    assert c == Classificacao(
        intencao="propriedades_produto",
        produto_termo="agua sanitaria limpax",
        campo="ph",
        valor_busca=None,
    )
    assert c.fallback is False


def test_json_invalido_cai_no_fallback():
    c = parse_and_validate("nao e json")
    assert c.intencao == "pergunta_aberta"
    assert c.fallback is True


def test_intencao_fora_do_vocabulario_cai_no_fallback():
    c = parse_and_validate(_json("comprar_produto"))
    assert c.fallback is True


def test_campo_invalido_e_descartado_sem_fallback():
    c = parse_and_validate(_json("propriedades_produto", produto="x", campo="cor_favorita"))
    assert c.campo is None
    assert c.fallback is False


def test_busca_caracteristica_sem_valor_cai_no_fallback():
    c = parse_and_validate(_json("busca_caracteristica", campo="ingrediente_ativo"))
    assert c.fallback is True


def test_busca_caracteristica_completa_e_aceita():
    c = parse_and_validate(_json("busca_caracteristica", campo="ingrediente_ativo", valor="hipoclorito"))
    assert c.intencao == "busca_caracteristica"
    assert c.valor_busca == "hipoclorito"


def test_fora_escopo_e_aceita():
    c = parse_and_validate(_json("fora_escopo"))
    assert c.intencao == "fora_escopo"
    assert c.fallback is False
    
def test_cerca_de_markdown_e_removida():
    """LLM as vezes envolve JSON em ```json ... ``` — o parser lida."""
    raw = "```json\n" + _json("fora_escopo") + "\n```"
    c = parse_and_validate(raw)
    assert c.intencao == "fora_escopo"
    assert c.fallback is False


def test_whitespace_em_produto_e_valor_e_removido():
    """produto_termo e valor_busca passam por strip antes de chegar no SQL."""
    c = parse_and_validate(_json(
        "busca_caracteristica",
        campo="ingrediente_ativo",
        valor="  hipoclorito  ",
    ))
    assert c.valor_busca == "hipoclorito"


def test_produto_termo_string_vazia_vira_none():
    """LLM pode devolver "" em vez de null — normalizamos para None."""
    c = parse_and_validate(_json("propriedades_produto", produto=""))
    assert c.produto_termo is None


def test_intencao_ausente_do_json_cai_no_fallback():
    """Distinto de intent fora do vocabulario: aqui a chave nem existe."""
    import json
    raw = json.dumps({"produto_termo": "x", "campo": None, "valor_busca": None})
    c = parse_and_validate(raw)
    assert c.fallback is True
    assert c.intencao == "pergunta_aberta"


# --- tipo_documento --------------------------------------------------

def test_tipo_documento_valido_e_aceito():
    c = parse_and_validate(_json("fetch_documento", produto="agua sanitaria", tipo="FT"))
    assert c.tipo_documento == "FT"


def test_tipo_documento_e_normalizado_para_maiuscula():
    """LLM as vezes devolve 'ft'; o filtro compara com documentos.tipo."""
    c = parse_and_validate(_json("fetch_documento", produto="x", tipo="fds"))
    assert c.tipo_documento == "FDS"


def test_tipo_documento_invalido_e_descartado_sem_fallback():
    """
    Sem o filtro a resposta e mais ampla, nunca errada — nao vale
    derrubar a classificacao inteira por causa dele.
    """
    c = parse_and_validate(_json("fetch_documento", produto="x", tipo="MANUAL"))
    assert c.tipo_documento is None
    assert c.fallback is False


def test_tipo_documento_ausente_vira_none():
    """"manda os documentos do X" nao especifica tipo: nao inventar."""
    c = parse_and_validate(_json("fetch_documento", produto="x"))
    assert c.tipo_documento is None