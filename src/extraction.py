"""
Extracao de propriedades estruturadas via LLM (Fase 3).

Recebe o texto completo de um documento (documentos.texto_extraido) e
extrai propriedades em formato estruturado, validadas contra um
vocabulario fechado de campos.

Decisoes de desenho:
- Extracao sobre o texto COMPLETO do documento (nao chunks): a extracao
  do pymupdf pode separar rotulo e valor; o contexto integral maximiza a
  chance de associacao correta.
- Vocabulario FECHADO: campos fora da lista sao descartados na validacao
  (o LLM nao pode inventar nomes de campo).
- Validadores sintaticos por campo: regras de formato que o prompt nao
  consegue garantir sao aplicadas deterministicamente no codigo.
- valor_numerico so quando o valor e um numero unico e inequivoco;
  faixas ("10 - 13") e comparadores ("> 85%") ficam so no valor texto.
- Em caso de duvida, OMITIR: dado faltando > dado errado.
"""

import json
import logging
import re
from dataclasses import dataclass

from src.config import LLM_MODEL_EXTRACTION
from src.openai_client import get_client

logger = logging.getLogger(__name__)

# Vocabulario fechado v1 — expandir aqui (e so aqui) quando necessario.
CAMPOS_VALIDOS = frozenset(
    {
        # Quimicos (FISPQ / FDS)
        "ingrediente_ativo",
        "numero_cas",
        "concentracao_ativo",
        "ph",
        "aspecto",
        "densidade",
        "solubilidade",
        "classe_risco_onu",
        "numero_onu",
        "palavra_advertencia",
        "incompatibilidades",
        "aplicacao",
        "diluicao",
        # Papel / descartaveis
        "composicao_material",
        "gramatura",
        "metragem_rolo",
        "largura",
        "folhas",
        "alvura",
        "classificacao_abnt",
        "unidades_por_fardo",
        "validade",
    }
)

# Validadores sintaticos por campo: regras deterministicas que o prompt
# nao consegue garantir. O valor que nao casa e descartado com log.
VALIDADORES_CAMPO = {
    # Classificacao de qualidade de papeis: exige "Classe N" E a norma
    # ABNT 15464 no valor. O numero da norma e o discriminador que
    # elimina sinteses e capturas erradas (14725, NR, risco ONU).
    "classificacao_abnt": re.compile(r"(?i)\bclasse\s*\d.*15464"),
}

SYSTEM_PROMPT = f"""Voce e um extrator de dados de fichas tecnicas e FISPQ/FDS de produtos de limpeza e descartaveis.

Extraia do texto as propriedades presentes, usando EXCLUSIVAMENTE estes nomes de campo:
{chr(10).join(f"- {campo}" for campo in sorted(CAMPOS_VALIDOS))}

Responda APENAS com JSON valido, sem markdown, sem comentarios, neste formato:
{{"propriedades": [{{"campo": "...", "valor": "...", "valor_numerico": null, "unidade": null, "confianca": 0.0}}]}}

Regras:
1. "valor": o texto original fiel do documento (ex.: "10 - 13", "> 85,0 %", "2,0-2,5% p/p"). Copie o valor EXATAMENTE como esta no texto — NUNCA resuma, arredonde ou converta um valor em outro formato (ex.: se o texto diz "10 - 13", o valor e "10 - 13", jamais "> 10").
2. "valor_numerico": SOMENTE se o valor for um numero unico e inequivoco (ex.: gramatura 16 -> 16). Para faixas, comparadores ou tolerancias, use null. Use ponto como separador decimal. Numeros unicos como numero_onu, gramatura ou peso DEVEM ter valor_numerico preenchido.
3. "unidade": a unidade de medida se houver (ex.: "g/m2", "%", "m"), senao null.
4. "confianca": de 0.0 a 1.0. Calibre de verdade — NAO use 1.0 como padrao. Use 1.0 apenas quando rotulo e valor estao explicitamente juntos no texto. O texto pode ter sido extraido de PDF fora de ordem (rotulos separados de valores): se voce precisou INFERIR a associacao entre um rotulo e um valor distantes no texto, use no maximo 0.8. Abaixo de 0.5, OMITA a propriedade.
5. Um mesmo campo pode aparecer mais de uma vez (ex.: dois ingredientes ativos).
6. NAO invente valores. Se uma propriedade nao esta no texto, nao a inclua.
7. Ignore propriedades que nao se encaixem no vocabulario.
8. IMPORTANTE: extraia apenas o que o produto E/CONTEM. Nao extraia como ingrediente_ativo substancias citadas apenas como incompativeis, perigosas em contato, ou produtos de decomposicao.
9. "classificacao_abnt": a classificacao de qualidade de papeis/descartaveis. O valor deve conter a classe do produto (palavra "Classe" seguida de numero) e a norma ABNT associada quando presente. Nao confundir com normas do documento (NBR 14725), risco de transporte ou normas trabalhistas.
10. E melhor omitir um campo do que preencher com informacao generica ou de outra natureza. Se o valor encontrado nao responde diretamente ao que o nome do campo significa, NAO o inclua.
11. Os exemplos destas instrucoes sao ilustrativos. NUNCA copie um valor destas instrucoes para a resposta — todo valor extraido deve existir literalmente no texto do documento fornecido."""


@dataclass(frozen=True)
class PropriedadeExtraida:
    """Propriedade validada, pronta para gravacao."""

    campo: str
    valor: str
    valor_numerico: float | None
    unidade: str | None
    confianca: float


def parse_and_validate(raw_json: str) -> list[PropriedadeExtraida]:
    """
    Converte e valida a resposta JSON do LLM.

    Validacoes:
    - JSON parseavel com chave "propriedades" (lista);
    - campo pertence ao vocabulario fechado (senao, descartado com log);
    - valor nao vazio;
    - validador sintatico do campo, quando existir;
    - confianca em [0.5, 1.0] (abaixo de 0.5, descartado);
    - valor_numerico convertido para float quando presente.

    Raises:
        ValueError: se o JSON for invalido ou a estrutura estiver errada.
    """
    cleaned = raw_json.strip().removeprefix("```json").removesuffix("```").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Resposta do LLM nao e JSON valido: {exc}") from exc

    propriedades_raw = data.get("propriedades")
    if not isinstance(propriedades_raw, list):
        raise ValueError('JSON sem a chave "propriedades" (lista).')

    validadas: list[PropriedadeExtraida] = []
    for item in propriedades_raw:
        campo = item.get("campo")
        valor = item.get("valor")
        confianca = item.get("confianca")

        if campo not in CAMPOS_VALIDOS:
            logger.warning("Campo fora do vocabulario descartado: %r", campo)
            continue

        if not valor or not str(valor).strip():
            logger.warning("Propriedade '%s' sem valor descartada", campo)
            continue

        validador = VALIDADORES_CAMPO.get(campo)
        if validador and not validador.search(str(valor)):
            logger.warning(
                "Propriedade '%s' reprovada no validador sintatico: %r",
                campo,
                valor,
            )
            continue

        if not isinstance(confianca, (int, float)) or not 0.5 <= confianca <= 1.0:
            logger.warning(
                "Propriedade '%s' com confianca invalida (%r) descartada",
                campo,
                confianca,
            )
            continue

        valor_numerico = item.get("valor_numerico")
        if valor_numerico is not None:
            try:
                valor_numerico = float(valor_numerico)
            except (TypeError, ValueError):
                logger.warning(
                    "valor_numerico invalido em '%s' (%r); mantendo apenas texto",
                    campo,
                    valor_numerico,
                )
                valor_numerico = None

        unidade = item.get("unidade")
        unidade = str(unidade).strip() if unidade else None

        validadas.append(
            PropriedadeExtraida(
                campo=campo,
                valor=str(valor).strip(),
                valor_numerico=valor_numerico,
                unidade=unidade,
                confianca=round(float(confianca), 2),
            )
        )

    return validadas


def extract_properties(texto_documento: str) -> list[PropriedadeExtraida]:
    """
    Extrai propriedades de um documento via LLM.

    Args:
        texto_documento: texto completo (documentos.texto_extraido).

    Returns:
        Lista de propriedades validadas (pode ser vazia).
    """
    if not texto_documento or not texto_documento.strip():
        return []

    client = get_client()
    response = client.chat.completions.create(
        model=LLM_MODEL_EXTRACTION,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": texto_documento},
        ],
    )

    raw = response.choices[0].message.content or ""
    return parse_and_validate(raw)