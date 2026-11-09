"""Grava no banco, como vinculos revisados, as respostas do golden do cliente.

Por que existe:
    O cliente conferiu 50 documentos (tests/golden/links_v1.csv). Alem de
    medir as camadas, essas respostas sao verdade: o vinculo confirmado
    deve existir em produto_documento e sair da fila de revisao.

O que faz, para cada documento com produto no gabarito:
    - insere o vinculo que falta (metodo 'manual', confianca 1.00);
    - marca como revisado o vinculo que ja existia e o cliente confirmou.
    A resposta vale para todas as copias do arquivo (ex.: a da pasta da
    marca e a de local_amostra), desde que tenham o mesmo conteudo.
    Vinculo existente que o cliente NAO confirmou e apenas listado:
    remover e decisao a tomar a mao.

Uso:
    python -m scripts.gravar_golden            # mostra o que faria
    python -m scripts.gravar_golden --aplicar  # grava
"""

import argparse
import csv
from pathlib import Path

from src.config import DATABASE_URL_ADMIN
from src.db import get_connection

GOLDEN = Path("tests/golden/links_v1.csv")
REVISOR = "cliente (conferencia golden links_v1)"

SQL_COPIAS = """
    SELECT d.id::text, d.marca_pasta, d.hash_conteudo
    FROM documentos d
    WHERE d.nome_arquivo = %(nome)s AND d.revogado_em IS NULL
"""

SQL_VINCULOS_DO_DOC = """
    SELECT produto_codigo, revisado_humano
    FROM produto_documento WHERE documento_id = %(documento_id)s
"""

SQL_GRAVAR = """
    INSERT INTO produto_documento
        (produto_codigo, documento_id, confianca_match, metodo_match,
         revisado_humano, revisor, revisado_em)
    VALUES (%(codigo)s, %(documento_id)s, 1.00, 'manual', TRUE, %(revisor)s, NOW())
    ON CONFLICT (produto_codigo, documento_id) DO UPDATE
        SET revisado_humano = TRUE,
            revisor = EXCLUDED.revisor,
            revisado_em = EXCLUDED.revisado_em
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aplicar", action="store_true", help="Grava (sem isto, so mostra).")
    args = parser.parse_args()

    with GOLDEN.open(encoding="utf-8") as arquivo:
        linhas = list(csv.DictReader(arquivo))

    inserir = confirmar = 0
    # Grava em produto_documento: roda como botuser. A role da aplicacao
    # (bot_app) so le o acervo -- ver db/provisioning/001b_bot_app_grants.sql.
    with get_connection(DATABASE_URL_ADMIN) as conexao, conexao.cursor() as cur:
        for linha in linhas:
            if linha["codigo_correto"] in ("NENHUM", "DUVIDA"):
                continue
            verdade = {int(c) for c in linha["codigo_correto"].split(";")}

            cur.execute(SQL_COPIAS, {"nome": linha["arquivo"]})
            copias = cur.fetchall()
            if len({hash_ for _, _, hash_ in copias}) > 1:
                print(f"  #{linha['numero']} PULADO: copias com conteudo diferente {[m for _, m, _ in copias]}")
                continue

            for documento_id, marca, _ in copias:
                cur.execute(SQL_VINCULOS_DO_DOC, {"documento_id": documento_id})
                existentes = dict(cur.fetchall())
                for codigo in sorted(verdade):
                    if codigo not in existentes:
                        acao, inserir = "inserir", inserir + 1
                    elif not existentes[codigo]:
                        acao, confirmar = "confirmar", confirmar + 1
                    else:
                        continue
                    print(f"  #{linha['numero']:>2} {acao:<9} {codigo} [{marca}] {linha['arquivo'][:50]}")
                    if args.aplicar:
                        cur.execute(SQL_GRAVAR, {
                            "codigo": codigo, "documento_id": documento_id, "revisor": REVISOR,
                        })
                for codigo in sorted(set(existentes) - verdade):
                    print(f"  #{linha['numero']:>2} ATENCAO   {codigo} vinculado mas fora do gabarito [{marca}]")

        if not args.aplicar:
            conexao.rollback()

    modo = "gravados" if args.aplicar else "a gravar (rode com --aplicar)"
    print(f"\n{inserir} vinculos novos e {confirmar} confirmados -- {modo}")


if __name__ == "__main__":
    main()
