"""Revoga as copias repetidas de um mesmo PDF (mesmo hash de conteudo).

Por que existe:
    O mesmo arquivo entrou mais de uma vez: uma ingestao local de amostra
    (pasta `local_amostra`, drive_file_id "local:...") repete fichas que
    tambem vieram do Drive, e algumas pastas do Drive tem o mesmo PDF duas
    vezes ("ficha.pdf" e "ficha (1).pdf"). Cada copia carrega os proprios
    chunks e propriedades: a busca devolve o mesmo trecho em dobro.

O que faz, por grupo de documentos com o mesmo hash:
    1. escolhe a copia que fica: fora de local_amostra > mais vinculos
       revisados > mais vinculos > nome sem "(1)" > a mais antiga;
    2. so revoga uma copia quando a duplicata e inequivoca: ela esta em
       local_amostra, ou o nome e o mesmo tirando "(1)", "(2)". Mesmo
       conteudo com NOME DE PRODUTO diferente ("Interfolha Slim" x
       "Interfolha Soft") pode ser arquivo trocado pelo fornecedor: esses
       casos sao so listados, para decisao humana;
    3. copia para a que fica os vinculos que so a revogada tinha (com o
       mesmo metodo, confianca e revisao) -- o bot nao perde documento;
    4. revoga pelo soft delete do schema (revogado_em, motivo_revogacao,
       substituido_por_id). Nada e apagado.

Desfazer:
    UPDATE documentos SET revogado_em = NULL, motivo_revogacao = NULL,
        substituido_por_id = NULL
    WHERE motivo_revogacao LIKE 'duplicata%';

A ingestao nao mexe em revogado_em, entao a revogacao sobrevive a uma
nova sincronizacao com o Drive.

Uso:
    python -m scripts.revogar_duplicatas            # mostra o que faria
    python -m scripts.revogar_duplicatas --aplicar  # grava
"""

import argparse
import re
import unicodedata

from src.config import DATABASE_URL_ADMIN
from src.db import get_connection

SQL_GRUPOS = """
    SELECT d.hash_conteudo,
           d.id::text,
           d.nome_arquivo,
           d.marca_pasta,
           count(pd.id) FILTER (WHERE pd.revisado_humano) AS revisados,
           count(pd.id) AS vinculos
    FROM documentos d
    LEFT JOIN produto_documento pd ON pd.documento_id = d.id
    WHERE d.revogado_em IS NULL
      AND d.hash_conteudo IN (
          SELECT hash_conteudo FROM documentos
          WHERE revogado_em IS NULL AND hash_conteudo IS NOT NULL
          GROUP BY 1 HAVING count(*) > 1)
    GROUP BY d.hash_conteudo, d.id
    ORDER BY d.hash_conteudo,
             d.marca_pasta = 'local_amostra',
             count(pd.id) FILTER (WHERE pd.revisado_humano) DESC,
             count(pd.id) DESC,
             d.nome_arquivo ~ '\\(\\d+\\)',
             length(d.nome_arquivo),
             d.created_at
"""

SQL_HERDAR_VINCULOS = """
    INSERT INTO produto_documento
        (produto_codigo, documento_id, confianca_match, metodo_match,
         revisado_humano, revisor, revisado_em)
    SELECT produto_codigo, %(fica)s, confianca_match, metodo_match,
           revisado_humano, revisor, revisado_em
    FROM produto_documento
    WHERE documento_id = %(sai)s
    ON CONFLICT (produto_codigo, documento_id) DO NOTHING
"""

SQL_REVOGAR = """
    UPDATE documentos
    SET revogado_em = NOW(),
        motivo_revogacao = %(motivo)s,
        substituido_por_id = %(fica)s
    WHERE id = %(sai)s
"""


def nome_base(nome: str) -> str:
    """Nome sem extensao, acento, caixa e sufixo de copia "(1)", "(2)"."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    sem_extensao = re.sub(r"\.pdf\s*$", "", sem_acento)
    sem_copia = re.sub(r"\s*\(\d+\)", "", sem_extensao)
    return " ".join(sem_copia.split())


def duplicata_inequivoca(fica_nome: str, sai_nome: str, sai_pasta: str) -> bool:
    return sai_pasta == "local_amostra" or nome_base(fica_nome) == nome_base(sai_nome)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aplicar", action="store_true", help="Grava (sem isto, so mostra).")
    args = parser.parse_args()

    # Grava em documentos e produto_documento: roda como botuser. A role da
    # aplicacao (bot_app) so le o acervo -- ver db/provisioning/001b.
    with get_connection(DATABASE_URL_ADMIN) as conexao, conexao.cursor() as cur:
        cur.execute(SQL_GRUPOS)
        grupos: dict[str, list[tuple]] = {}
        for hash_, *resto in cur.fetchall():
            grupos.setdefault(hash_, []).append(tuple(resto))

        revogados = herdados = 0
        para_revisar = []
        for copias in grupos.values():
            fica_id, fica_nome, fica_pasta, _, _ = copias[0]
            print(f"fica   [{fica_pasta}] {fica_nome}")
            for sai_id, sai_nome, sai_pasta, _, sai_vinculos in copias[1:]:
                if not duplicata_inequivoca(fica_nome, sai_nome, sai_pasta):
                    print(f"  REVISAR [{sai_pasta}] {sai_nome} -- mesmo conteudo, outro nome")
                    para_revisar.append((fica_pasta, fica_nome, sai_nome))
                    continue
                print(f"  sai  [{sai_pasta}] {sai_nome} ({sai_vinculos} vinculos)")
                if args.aplicar:
                    cur.execute(SQL_HERDAR_VINCULOS, {"fica": fica_id, "sai": sai_id})
                    herdados += cur.rowcount
                    cur.execute(SQL_REVOGAR, {
                        "fica": fica_id,
                        "sai": sai_id,
                        "motivo": f"duplicata de '{fica_nome}' [{fica_pasta}] (mesmo conteudo)",
                    })
                revogados += 1

        if not args.aplicar:
            conexao.rollback()

    print(f"\n{len(grupos)} grupos, {revogados} copias a revogar", end="")
    if args.aplicar:
        print(f" -- revogadas; {herdados} vinculos passaram para a copia que fica")
    else:
        print(" -- nada gravado (rode com --aplicar)")
    if para_revisar:
        print(f"\n{len(para_revisar)} copias com o mesmo conteudo e outro nome, NAO revogadas:")
        for pasta, fica_nome, sai_nome in para_revisar:
            print(f"  [{pasta}] '{fica_nome}' = '{sai_nome}'")


if __name__ == "__main__":
    main()
