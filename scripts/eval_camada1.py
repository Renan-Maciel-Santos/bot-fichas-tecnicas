"""Mede a vinculacao documento->produto contra o golden set do cliente.

Por que existe:
    Ajustar limiar ou regra da Camada 1 sem medir e chute. Este script roda
    a logica REAL de src.link_documents (Camada 0 + decidir_camada1) sobre
    os 50 documentos de tests/golden/links_v1.csv, rotulados pelo cliente,
    e conta acertos e erros. Le o banco, nao grava nada.

Metricas (por documento):
    certo       todos os produtos vinculados estao no gabarito
    errado      vinculou ao menos um produto fora do gabarito -- o erro
                caro: FDS do produto errado e risco de seguranca quimica
    sem vinculo nao vinculou (vai para revisao humana)

    precisao  = certos / (certos + errados)
    cobertura = certos / documentos que tem produto no gabarito

Uso:
    python -m scripts.eval_camada1             # configuracao atual vs. a antiga
    python -m scripts.eval_camada1 --varrer    # grade de limiares
    python -m scripts.eval_camada1 --detalhe   # decisao documento a documento
"""

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from src.db import get_connection
from src.link_documents import (
    FATOR_DESTAQUE_LINK,
    PASTAS_SEM_MARCA,
    REGULATORIO_REGEX,
    SCORE_MINIMO_TRIGRAM,
    Candidato,
    _extrair_miolo,
    buscar_candidatos,
    decidir_camada1,
    extract_codigo_prefixo,
)

GOLDEN = Path("tests/golden/links_v1.csv")

# O mesmo arquivo pode existir em duas pastas (ex.: a da marca e a
# local_amostra). O golden nao guarda a pasta; vale a copia que a
# Camada 1 consegue processar.
SQL_DOCUMENTO = """
    SELECT d.marca_pasta,
           EXISTS (
               SELECT 1 FROM produtos p
               WHERE unaccent(lower(p.marca)) = unaccent(lower(d.marca_pasta))
           ) AS marca_no_catalogo
    FROM documentos d
    WHERE d.nome_arquivo = %(nome)s
    ORDER BY marca_no_catalogo DESC
    LIMIT 1
"""
SQL_PRODUTO_EXISTE = "SELECT 1 FROM produtos WHERE codigo = %(codigo)s"


@dataclass
class Caso:
    numero: str
    arquivo: str
    verdade: set[str]  # vazio = NENHUM (nao ha produto para vincular)
    codigo_camada0: int | None
    elegivel_camada1: bool
    miolo: str
    candidatos_marca: list[Candidato]
    candidatos: list[Candidato]  # com busca global nas pastas sem marca


@dataclass(frozen=True)
class Config:
    nome: str
    score_minimo: float
    fator_destaque: float
    agrupar_embalagem: bool
    busca_global: bool


ANTIGA = Config("antiga", 0.35, 1.5, agrupar_embalagem=False, busca_global=False)
ATUAL = Config(
    "atual", SCORE_MINIMO_TRIGRAM, FATOR_DESTAQUE_LINK,
    agrupar_embalagem=True, busca_global=True,
)


def carregar_casos(cur) -> list[Caso]:
    casos = []
    with GOLDEN.open(encoding="utf-8") as arquivo:
        for linha in csv.DictReader(arquivo):
            if linha["codigo_correto"] == "DUVIDA":
                continue
            verdade = set() if linha["codigo_correto"] == "NENHUM" else set(linha["codigo_correto"].split(";"))

            cur.execute(SQL_DOCUMENTO, {"nome": linha["arquivo"]})
            marca_pasta, marca_no_catalogo = cur.fetchone()

            codigo = extract_codigo_prefixo(linha["arquivo"])
            if codigo is not None:
                cur.execute(SQL_PRODUTO_EXISTE, {"codigo": codigo})
                codigo = codigo if cur.fetchone() else None

            miolo = _extrair_miolo(linha["arquivo"])
            elegivel = (
                codigo is None
                and marca_no_catalogo
                and bool(miolo)
                and not REGULATORIO_REGEX.search(linha["arquivo"])
            )
            casos.append(Caso(
                numero=linha["numero"],
                arquivo=linha["arquivo"],
                verdade=verdade,
                codigo_camada0=codigo,
                elegivel_camada1=elegivel,
                miolo=miolo,
                candidatos_marca=buscar_candidatos(cur, miolo, marca_pasta, busca_global=False) if elegivel else [],
                candidatos=buscar_candidatos(cur, miolo, marca_pasta) if elegivel else [],
            ))
    return casos


def vinculos(caso: Caso, config: Config) -> set[str]:
    """O que o pipeline vincularia para este documento sob `config`."""
    if caso.codigo_camada0 is not None:
        return {str(caso.codigo_camada0)}
    if not caso.elegivel_camada1:
        return set()
    decisao = decidir_camada1(
        caso.miolo,
        caso.candidatos if config.busca_global else caso.candidatos_marca,
        score_minimo=config.score_minimo,
        fator_destaque=config.fator_destaque,
        agrupar_embalagem=config.agrupar_embalagem,
    )
    return {str(c.codigo) for c in decisao.vinculos}


def avaliar(casos: list[Caso], config: Config) -> dict[str, float]:
    certos = errados = produtos_certos = 0
    for caso in casos:
        feitos = vinculos(caso, config)
        if not feitos:
            continue
        if feitos <= caso.verdade:
            certos += 1
            produtos_certos += len(feitos)
        else:
            errados += 1
    com_produto = sum(1 for caso in casos if caso.verdade)
    produtos_verdade = sum(len(caso.verdade) for caso in casos)
    return {
        "certos": certos,
        "errados": errados,
        "precisao": certos / (certos + errados) if certos + errados else 1.0,
        "cobertura": certos / com_produto,
        "cobertura_produtos": produtos_certos / produtos_verdade,
    }


def _linha(config: Config, m: dict[str, float]) -> str:
    return (
        f"{config.nome:<28} certos {m['certos']:>2}  errados {m['errados']:>2}  "
        f"precisao {m['precisao']:6.1%}  cobertura {m['cobertura']:6.1%}  "
        f"produtos {m['cobertura_produtos']:6.1%}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--varrer", action="store_true")
    parser.add_argument("--detalhe", action="store_true")
    args = parser.parse_args()

    with get_connection() as conexao, conexao.cursor() as cur:
        casos = carregar_casos(cur)

    print(f"{len(casos)} documentos; {sum(1 for c in casos if c.verdade)} com produto no gabarito\n")
    for config in (ANTIGA, ATUAL):
        print(_linha(config, avaliar(casos, config)))

    if args.varrer:
        print("\nGrade (agrupamento + busca global ligados):")
        for score in (0.20, 0.25, 0.30, 0.35):
            for fator in (1.1, 1.15, 1.2, 1.25, 1.3, 1.4, 1.5):
                config = Config(f"score {score:.2f} fator {fator:.2f}", score, fator, True, True)
                print(_linha(config, avaliar(casos, config)))

    if args.detalhe:
        print("\nDocumento a documento (configuracao atual):")
        for caso in casos:
            feitos = vinculos(caso, ATUAL)
            if not feitos:
                status = "sem vinculo"
            elif feitos <= caso.verdade:
                status = "certo" if feitos == caso.verdade else "certo (parcial)"
            else:
                status = "ERRADO"
            print(f"  #{caso.numero:>2} {status:<16} {caso.arquivo[:60]}")


if __name__ == "__main__":
    main()
