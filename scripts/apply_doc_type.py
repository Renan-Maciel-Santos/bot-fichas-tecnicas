"""
Backfill de `documentos.tipo` com o classificador.

Uso:
    # Dry-run (default): imprime classificacao proposta, nao escreve.
    python -m scripts.apply_doc_type

    # Com heuristicas de dominio da revenda A:
    python -m scripts.apply_doc_type --heuristicas reseller_a

    # Aplica as mudancas no banco (transacao unica):
    python -m scripts.apply_doc_type --heuristicas reseller_a --apply

Desenho:
- Safe by default: sem --apply, nada e escrito.
- Idempotente: UPDATE em coluna existente; rodar N vezes = mesmo estado.
- Transacional: get_connection() faz commit no sucesso e rollback em erro.
- Threshold e conjuntos de heuristicas vem de src/config.py e
  src/heuristics_registry.py, compartilhados com o pipeline de ingestao.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from dataclasses import dataclass

from src.classify_doc_type import (
    ClassificationResult,
    HeuristicDetector,
    classify_doc_type,
)
from src.config import CONFIANCA_MINIMA_TIPO
from src.config import DATABASE_URL_ADMIN
from src.db import get_connection
from src.heuristics_registry import CONJUNTOS_HEURISTICAS, resolver_heuristicas

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentoParaClassificar:
    id: str
    nome_arquivo: str
    texto_extraido: str | None
    tipo_atual: str | None


@dataclass(frozen=True)
class LinhaResultado:
    documento: DocumentoParaClassificar
    resultado: ClassificationResult

    @property
    def sera_gravado(self) -> bool:
        return (
            self.resultado.tipo is not None
            and self.resultado.confianca >= CONFIANCA_MINIMA_TIPO
        )

    @property
    def valor_para_gravar(self) -> str | None:
        return self.resultado.tipo if self.sera_gravado else None


def carregar_documentos(conn) -> list[DocumentoParaClassificar]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, nome_arquivo, texto_extraido, tipo
            FROM documentos
            ORDER BY nome_arquivo
            """
        )
        rows = cur.fetchall()
    return [
        DocumentoParaClassificar(
            id=str(row[0]),
            nome_arquivo=row[1],
            texto_extraido=row[2],
            tipo_atual=row[3],
        )
        for row in rows
    ]


def classificar_todos(
    documentos: list[DocumentoParaClassificar],
    heuristicas: list[HeuristicDetector] | None,
) -> list[LinhaResultado]:
    return [
        LinhaResultado(
            documento=doc,
            resultado=classify_doc_type(
                nome_arquivo=doc.nome_arquivo,
                texto_extraido=doc.texto_extraido,
                heuristicas=heuristicas,
            ),
        )
        for doc in documentos
    ]


def imprimir_relatorio(linhas: list[LinhaResultado]) -> None:
    print()
    print("=" * 100)
    print(f"{'ARQUIVO':<70} {'TIPO':<8} {'CONF':<6} {'GRAVA?':<8}")
    print("=" * 100)
    for linha in linhas:
        nome = linha.documento.nome_arquivo
        nome_curto = (nome[:67] + "...") if len(nome) > 70 else nome
        tipo = linha.resultado.tipo or "-"
        conf = f"{linha.resultado.confianca:.1f}"
        grava = "SIM" if linha.sera_gravado else "nao"
        print(f"{nome_curto:<70} {tipo:<8} {conf:<6} {grava:<8}")
        for motivo in linha.resultado.motivos:
            print(f"  -> {motivo}")
    print("=" * 100)


def imprimir_sumario(linhas: list[LinhaResultado]) -> None:
    contagem_tipo: Counter[str] = Counter()
    pendentes = 0
    for linha in linhas:
        if linha.sera_gravado:
            contagem_tipo[linha.resultado.tipo] += 1  # type: ignore[arg-type]
        else:
            pendentes += 1
    total = len(linhas)
    print()
    print(f"Total de documentos:       {total}")
    for tipo in ("FT", "FDS", "LAUDO"):
        print(f"  {tipo:<5} classificados:      {contagem_tipo.get(tipo, 0)}")
    print(f"  Pendentes de revisao:    {pendentes}")
    cobertura = (total - pendentes) / total * 100 if total else 0
    print(f"Cobertura:                 {cobertura:.1f}%")


def aplicar_no_banco(conn, linhas: list[LinhaResultado]) -> int:
    atualizados = 0
    with conn.cursor() as cur:
        for linha in linhas:
            if not linha.sera_gravado:
                continue
            cur.execute(
                "UPDATE documentos SET tipo = %s WHERE id = %s",
                (linha.valor_para_gravar, linha.documento.id),
            )
            atualizados += cur.rowcount
    return atualizados


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill de documentos.tipo (dry-run por padrao)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica UPDATE no banco (sem esta flag, apenas imprime).",
    )
    parser.add_argument(
        "--heuristicas",
        choices=list(CONJUNTOS_HEURISTICAS.keys()) + ["none"],
        default=None,
        help="Conjunto de heuristicas de dominio a ativar. 'none' = core puro.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    heuristicas = resolver_heuristicas(args.heuristicas)
    if heuristicas:
        logger.info("Heuristicas ativas: %s", args.heuristicas)
    else:
        logger.info("Rodando com classificador core (sem heuristicas).")

    try:
        # Ingestao escreve no acervo: conecta como botuser. A role da aplicacao
        # (bot_app) so le -- ver db/provisioning/001b_bot_app_grants.sql.
        with get_connection(DATABASE_URL_ADMIN) as conn:
            documentos = carregar_documentos(conn)
            logger.info("Carregados %d documentos.", len(documentos))

            linhas = classificar_todos(documentos, heuristicas)
            imprimir_relatorio(linhas)
            imprimir_sumario(linhas)

            if not args.apply:
                print()
                print("Modo dry-run. Nenhuma alteracao feita.")
                print("Para gravar, rode novamente com --apply.")
                return 0

            atualizados = aplicar_no_banco(conn, linhas)
            logger.info("Atualizacoes preparadas: %d.", atualizados)
        logger.info("Commit realizado.")
        return 0
    except Exception:
        logger.exception("Erro durante classificacao. Rollback executado.")
        return 1


if __name__ == "__main__":
    sys.exit(main())