"""
Classificador de intencao da pergunta do vendedor (Fase 4).

Recebe a pergunta em linguagem natural e a classifica em uma das
intencoes do design original, extraindo os parametros necessarios
para a busca (termo do produto, campo, valor).

Arquitetura identica a da extracao (Fase 3): prompt orienta, codigo
valida. Vocabulario fechado de intencoes e campos; classificacao
invalida ou erro de API cai no fallback seguro (pergunta_aberta),
cuja busca vetorial com limiar nunca inventa resposta.
"""

import json
import logging
from dataclasses import dataclass

from src.config import LLM_MODEL_RESPONSE
from src.extraction import CAMPOS_VALIDOS
from src.openai_client import get_client

logger = logging.getLogger(__name__)

INTENCOES_VALIDAS = frozenset(
    {
        "busca_caracteristica",   # "quais produtos contem hipoclorito?"
        "propriedades_produto",   # "qual o pH da agua sanitaria limpax?"
        "fetch_documento",        # "me manda a FISPQ da agua sanitaria"
        "pergunta_aberta",        # "como armazenar agua sanitaria?"
        "fora_escopo",            # "qual a previsao do tempo?"
    }
)

# Fallback seguro: a rota mais tolerante a erro de roteamento.
INTENCAO_FALLBACK = "pergunta_aberta"

# Tipos de documento que o vendedor pode pedir nominalmente. Espelha
# `documentos.tipo`, menos OUTRO/INDEFINIDO: ninguem pede "me manda o
# INDEFINIDO do produto X".
TIPOS_DOCUMENTO_VALIDOS = frozenset({"FT", "FDS", "LAUDO"})

SYSTEM_PROMPT = f"""Voce e o roteador de perguntas de um assistente interno para vendedores de uma revenda de produtos de limpeza e descartaveis. A base de conhecimento contem fichas tecnicas, FISPQ/FDS e propriedades estruturadas dos produtos do catalogo.

Classifique a pergunta do vendedor em UMA intencao e extraia os parametros.

Intencoes:
- "busca_caracteristica": o vendedor busca PRODUTOS que tenham uma caracteristica ("quais produtos contem hipoclorito?", "tem algum detergente com pH neutro?").
- "propriedades_produto": o vendedor pergunta caracteristicas de UM produto especifico ("qual o pH da agua sanitaria limpax?", "qual a gramatura da toalha folhabela?").
- "fetch_documento": o vendedor quer o ARQUIVO/ficha/FISPQ/laudo de um produto ("me manda a ficha da agua sanitaria", "tem a FISPQ do detergente limpax?").
- "pergunta_aberta": pergunta sobre uso, aplicacao, seguranca, armazenamento ou qualquer duvida que exige ler o conteudo das fichas ("como armazenar?", "pode usar em cozinha?", "o que fazer em caso de contato com os olhos?").
- "fora_escopo": nada a ver com os produtos ou a base ("previsao do tempo", "conte uma piada", perguntas sobre precos/estoque/pedidos, que NAO estao na base).

Campos estruturados existentes (para "campo" em busca_caracteristica):
{chr(10).join(f"- {campo}" for campo in sorted(CAMPOS_VALIDOS))}

Responda APENAS com JSON valido:
{{"intencao": "...", "produto_termo": null, "campo": null, "valor_busca": null, "tipo_documento": null}}

Regras:
1. "produto_termo": o nome/codigo do produto EXATAMENTE como o vendedor falou (ex.: "agua sanitaria limpax", "9000001"). null se a pergunta nao cita produto especifico.
2. "campo": para busca_caracteristica, o campo do vocabulario que melhor corresponde. Para propriedades_produto, o campo perguntado se for um so; null se a pergunta e geral ("me fala sobre o produto X").
3. "valor_busca": para busca_caracteristica, o valor procurado (ex.: "hipoclorito"). null nas demais.
4. "tipo_documento": SO para fetch_documento, qual documento o vendedor pediu:
   - "FT" para ficha tecnica, ficha do produto, boletim tecnico
   - "FDS" para FISPQ, FDS, ficha de seguranca, MSDS
   - "LAUDO" para laudo, certificado, analise, ANVISA
   - null quando ele disse apenas "a ficha" / "os documentos" sem especificar qual, ou quando pediu tudo. NAO adivinhe: "ficha" sozinho e ambiguo entre FT e FDS.
5. Precos, estoque, pedidos e prazos de entrega NAO estao na base: classifique como fora_escopo.
6. Na duvida entre intencoes, prefira "pergunta_aberta"."""


@dataclass(frozen=True)
class Classificacao:
    """Resultado do roteamento de uma pergunta."""

    intencao: str
    produto_termo: str | None
    campo: str | None
    valor_busca: str | None
    tipo_documento: str | None = None  # "FT" | "FDS" | "LAUDO" | None
    fallback: bool = False  # True quando a classificacao veio do fallback


def _fallback(motivo: str) -> Classificacao:
    logger.warning("Classificacao caiu no fallback: %s", motivo)
    return Classificacao(
        intencao=INTENCAO_FALLBACK,
        produto_termo=None,
        campo=None,
        valor_busca=None,
        fallback=True,
    )


def parse_and_validate(raw_json: str) -> Classificacao:
    """
    Valida a resposta do LLM contra os vocabularios fechados.

    Qualquer inconsistencia -> fallback seguro (nunca levanta excecao
    para o chamador: roteamento sempre produz uma rota).
    """
    cleaned = raw_json.strip().removeprefix("```json").removesuffix("```").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return _fallback(f"JSON invalido: {exc}")

    intencao = data.get("intencao")
    if intencao not in INTENCOES_VALIDAS:
        return _fallback(f"intencao fora do vocabulario: {intencao!r}")

    campo = data.get("campo")
    if campo is not None and campo not in CAMPOS_VALIDOS:
        logger.warning("Campo fora do vocabulario descartado: %r", campo)
        campo = None

    produto_termo = data.get("produto_termo")
    produto_termo = str(produto_termo).strip() if produto_termo else None

    valor_busca = data.get("valor_busca")
    valor_busca = str(valor_busca).strip() if valor_busca else None

    # Tipo fora do vocabulario e descartado, nao vira fallback: sem o
    # filtro a resposta e mais ampla, nunca errada.
    tipo_documento = data.get("tipo_documento")
    if tipo_documento is not None:
        tipo_documento = str(tipo_documento).strip().upper()
        if tipo_documento not in TIPOS_DOCUMENTO_VALIDOS:
            logger.warning(
                "Tipo de documento fora do vocabulario descartado: %r",
                tipo_documento,
            )
            tipo_documento = None

    # busca_caracteristica sem valor de busca nao e executavel:
    # rebaixa para pergunta_aberta (a rota tolerante).
    if intencao == "busca_caracteristica" and not valor_busca:
        return _fallback("busca_caracteristica sem valor_busca")

    return Classificacao(
        intencao=intencao,
        produto_termo=produto_termo,
        campo=campo,
        valor_busca=valor_busca,
        tipo_documento=tipo_documento,
    )


def classificar(pergunta: str) -> Classificacao:
    """
    Classifica a pergunta do vendedor.

    Nunca levanta excecao: erro de API ou resposta invalida caem no
    fallback seguro (pergunta_aberta).
    """
    pergunta = (pergunta or "").strip()
    if not pergunta:
        return _fallback("pergunta vazia")

    try:
        client = get_client()
        response = client.chat.completions.create(
            model=LLM_MODEL_RESPONSE,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": pergunta},
            ],
        )
        raw = response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 — roteamento nunca pode explodir
        return _fallback(f"erro na chamada ao LLM: {exc}")

    return parse_and_validate(raw)