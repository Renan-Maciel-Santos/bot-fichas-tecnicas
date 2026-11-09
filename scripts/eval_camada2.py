"""Mede a Camada 2 (LLM) contra o golden set do cliente.

Roda decidir_llm sobre os documentos de tests/golden/links_v1.csv que as
Camadas 0 e 1 deixam sem vinculo -- exatamente o que a Camada 2 recebe em
producao. Le o banco e chama a API da OpenAI; nao grava nada no banco.

Classificacao por documento:
    certo           todos os codigos vinculados estao no gabarito
    certo (parcial) idem, mas o gabarito tinha mais produtos
    ERRADO          vinculou ao menos um codigo fora do gabarito
    nenhum/duvida   nao vinculou (correto quando o gabarito e NENHUM)

Uso:
    python -m scripts.eval_camada2
"""

import json
from pathlib import Path

from scripts.eval_camada1 import ATUAL, carregar_casos, vinculos
from src.db import get_connection
from src.link_llm import LLM_MODEL_LINK, buscar_candidatos_llm, decidir_llm

SAIDA = Path("data/analise/eval_camada2.json")

SQL_DOCUMENTO = """
    SELECT d.marca_pasta, coalesce(d.texto_extraido, '')
    FROM documentos d
    WHERE d.nome_arquivo = %(nome)s
      AND EXISTS (
          SELECT 1 FROM produtos p
          WHERE unaccent(lower(p.marca)) = unaccent(lower(d.marca_pasta))
      )
    LIMIT 1
"""


def main() -> None:
    with get_connection() as conexao, conexao.cursor() as cur:
        casos = [caso for caso in carregar_casos(cur) if not vinculos(caso, ATUAL)]
        entradas = []
        for caso in casos:
            cur.execute(SQL_DOCUMENTO, {"nome": caso.arquivo})
            linha = cur.fetchone()
            if linha is None:
                continue  # pasta sem marca no catalogo: fora do alcance da Camada 2
            marca, texto = linha
            entradas.append((caso, texto, buscar_candidatos_llm(cur, caso.arquivo, marca)))

    print(f"Modelo: {LLM_MODEL_LINK}")
    print(f"{len(entradas)} documentos do golden chegam a Camada 2\n")

    contagem = {"certo": 0, "certo (parcial)": 0, "ERRADO": 0, "nao vinculou": 0}
    registros = []
    for caso, texto, candidatos in entradas:
        decisao = decidir_llm(caso.arquivo, texto, candidatos)
        feitos = {str(c) for c in decisao.codigos}
        if not feitos:
            status = "nao vinculou"
        elif feitos <= caso.verdade:
            status = "certo" if feitos == caso.verdade else "certo (parcial)"
        else:
            status = "ERRADO"
        contagem[status] += 1

        verdade = ";".join(sorted(caso.verdade)) or "NENHUM"
        print(f"  #{caso.numero:>2} {status:<16} {decisao.decisao:<9} {caso.arquivo[:55]}")
        if status == "ERRADO" or (status == "nao vinculou" and caso.verdade):
            descricoes = dict(candidatos)
            print(f"        gabarito: {verdade} | llm: {sorted(feitos) or '-'}")
            print(f"        llm escolheu: {[descricoes.get(int(c)) for c in feitos]}")
            print(f"        porque: {decisao.justificativa}")
        registros.append({
            "numero": caso.numero, "arquivo": caso.arquivo, "gabarito": verdade,
            "decisao": decisao.decisao, "codigos": sorted(feitos),
            "justificativa": decisao.justificativa, "status": status,
        })

    certos = contagem["certo"] + contagem["certo (parcial)"]
    com_produto = sum(1 for caso, _, _ in entradas if caso.verdade)
    print()
    for status, n in contagem.items():
        print(f"  {status:<16} {n}")
    if certos + contagem["ERRADO"]:
        print(f"\n  precisao  {certos / (certos + contagem['ERRADO']):.1%}")
    print(f"  cobertura {certos / com_produto:.1%} dos {com_produto} que tem produto")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
