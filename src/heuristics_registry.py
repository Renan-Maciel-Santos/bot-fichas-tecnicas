"""
Registro central de conjuntos de heuristicas de dominio.

Ponto unico de verdade para mapear nomes de clientes -> lista de
detectores. Consumido pelo pipeline de ingestao (automatico) e pelo
script apply_doc_type (interativo).

Adicionar um novo cliente = uma nova chave neste dict. Nenhum outro
arquivo precisa mudar.
"""

from src.classify_doc_type import HeuristicDetector
from src.heuristics_reseller_a import detectar_sinais_reseller_a

CONJUNTOS_HEURISTICAS: dict[str, list[HeuristicDetector]] = {
    "reseller_a": [detectar_sinais_reseller_a],
}


def resolver_heuristicas(nome: str | None) -> list[HeuristicDetector] | None:
    """
    Traduz o nome de um conjunto na lista de detectores correspondente.

    Args:
        nome: chave em CONJUNTOS_HEURISTICAS, ou "none"/""/None para
            core puro (sem heuristicas).

    Raises:
        ValueError: se `nome` nao e vazio e nao esta registrado.
    """
    if not nome or nome.lower() in ("none", "off"):
        return None
    if nome not in CONJUNTOS_HEURISTICAS:
        raise ValueError(
            f"Conjunto de heuristicas desconhecido: {nome!r}. "
            f"Disponiveis: {list(CONJUNTOS_HEURISTICAS)}"
        )
    return CONJUNTOS_HEURISTICAS[nome]