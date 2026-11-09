"""
Politica de citacao de documentos fonte.

Constroi uma string legivel para o vendedor com base no nome do
arquivo e no tipo classificado (FT/FDS/LAUDO).

Motivacao:
- Ate a Fase 4 Bloco A, as respostas citavam apenas o nome do arquivo:
  "Conforme FDS Aromatizante 120ml Aromar.pdf". Ruidoso e ilegivel.
- Com documentos.tipo populado, podemos formatar
  "FDS de Aromatizante 120ml Aromar" - o vendedor sabe o tipo de
  fonte imediatamente.

Esta funcao e o unico lugar onde a decisao de formato de citacao vive.
Se um dia quisermos "Conforme a FISPQ de X (rev. 2024)" ou outro
estilo, mexe aqui e em nenhum outro lugar do responder.
"""

import re

# Extensao .pdf ao final (case-insensitive)
_RE_EXTENSAO = re.compile(r"\.pdf$", re.IGNORECASE)

# Colapsar espacos multiplos em um so
_RE_ESPACOS = re.compile(r"\s+")

# Prefixos redundantes por tipo. Cada padrao tenta remover a mencao ao
# tipo no inicio do nome, para evitar "FDS de FDS Aromatizante...".
# Cada tipo inclui um padrao "nome e SO o prefixo" para casos
# patologicos: "FDS.pdf" -> remainder vazio -> retorna so o rotulo.
_PREFIXOS_POR_TIPO: dict[str, list[re.Pattern[str]]] = {
    "FDS": [
        re.compile(r"^\s*FDS[\s\-_]+", re.IGNORECASE),
        re.compile(r"^\s*FDS\s*$", re.IGNORECASE),
    ],
    "FT": [
        # "FICHA TECNICA" / "ficha tecnica" (com/sem acento, com separadores)
        re.compile(r"^\s*ficha[\s_-]*t[eé]cnica[\s\-_]*", re.IGNORECASE),
        re.compile(r"^\s*FT[\s\-_]+", re.IGNORECASE),
        re.compile(r"^\s*FT\s*$", re.IGNORECASE),
    ],
    "LAUDO": [
        re.compile(r"^\s*laudo[\s\-_]+", re.IGNORECASE),
        re.compile(r"^\s*laudo\s*$", re.IGNORECASE),
    ],
}

# Rotulo final na saida por tipo. FDS/FT sao siglas (maiusculo);
# "laudo" em minuscula soa melhor em PT ("conforme o laudo de X").
_ROTULO_POR_TIPO: dict[str, str] = {
    "FDS": "FDS",
    "FT": "FT",
    "LAUDO": "laudo",
}

# Nome por extenso, para prosa dirigida ao vendedor ("nao encontrei a
# ficha tecnica de X"). Distinto do rotulo de citacao, que e compacto
# porque aparece encaixado no meio da resposta.
_NOME_EXTENSO_POR_TIPO: dict[str, str] = {
    "FDS": "a FDS (ficha de segurança)",
    "FT": "a ficha técnica",
    "LAUDO": "o laudo",
}


def nome_do_tipo(tipo: str | None) -> str:
    """
    Nome por extenso do tipo, com artigo, para usar em frase.

    Exemplos:
        >>> nome_do_tipo("FT")
        'a ficha técnica'
        >>> nome_do_tipo(None)
        'o documento'
    """
    return _NOME_EXTENSO_POR_TIPO.get(tipo or "", "o documento")


def formatar_fonte(nome_arquivo: str, tipo: str | None) -> str:
    """Formata uma referencia legivel para um documento fonte.

    Args:
        nome_arquivo: nome do arquivo como salvo em `documentos.nome_arquivo`.
        tipo: tipo classificado ("FT", "FDS", "LAUDO" ou None).

    Returns:
        String pronta para colocar em citacao. Nunca inclui ".pdf".
        Nunca duplica o rotulo do tipo.

    Exemplos:
        >>> formatar_fonte("FDS Aromatizante 120ml Aromar.pdf", "FDS")
        'FDS de Aromatizante 120ml Aromar'
        >>> formatar_fonte("4401 Interfolha Orca.pdf", "FT")
        'FT de 4401 Interfolha Orca'
        >>> formatar_fonte("Doc.pdf", None)
        'documento Doc'
    """
    nome_sem_ext = _RE_EXTENSAO.sub("", (nome_arquivo or "").strip())

    if tipo is None or tipo not in _ROTULO_POR_TIPO:
        return f"documento {nome_sem_ext}".strip() if nome_sem_ext else "documento"

    # Remove prefixo redundante (o rotulo do tipo ja aparecera na saida).
    remainder = nome_sem_ext
    for padrao in _PREFIXOS_POR_TIPO[tipo]:
        aplicado = padrao.sub("", remainder, count=1)
        if aplicado != remainder:
            remainder = aplicado
            break

    remainder = _RE_ESPACOS.sub(" ", remainder).strip()
    rotulo = _ROTULO_POR_TIPO[tipo]

    # Nome patologico: apos remover prefixo nao sobrou nada.
    if not remainder:
        return rotulo

    return f"{rotulo} de {remainder}"