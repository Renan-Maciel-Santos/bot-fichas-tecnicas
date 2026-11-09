"""
Adapter: sync do Google Drive para o pipeline de ingestao (Fase 1).

Reusa `processar_documento` de src.ingestion.ingest_pdfs — este script
apenas descobre PDFs no Drive, baixa cada um para cache persistente
e delega ao pipeline agnostico de origem.

Escopo: SO Fase 1 (extracao de texto + upsert em `documentos`).
Fases 2 (chunks/embeddings) e 3 (extracao de propriedades) sao
scripts separados por design — Single Responsibility. Cada fase
pode ser rodada, inspecionada e re-rodada de forma independente.

Uso:
    python -m scripts.ingest_from_drive <folder_id>
    python -m scripts.ingest_from_drive <folder_id> --dry-run
    python -m scripts.ingest_from_drive <folder_id> --limit 10
    python -m scripts.ingest_from_drive <folder_id> --heuristicas none
    python -m scripts.ingest_from_drive <folder_id> --force

Cache: os PDFs baixados ficam em data/drive_cache/<file_id>.pdf.
Persistente entre execucoes: rodar de novo pula download (o
de_arquivo_drive detecta o cache hit). Para forcar re-download, apagar
a pasta antes de rodar.
"""

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from src.config import INGEST_HEURISTICAS, PROJECT_ROOT
from src.drive.client import listar_pdfs_recursivo
from src.drive.fonte import de_arquivo_drive
from src.heuristics_registry import CONJUNTOS_HEURISTICAS, resolver_heuristicas
from src.ingestion.ingest_pdfs import processar_documento

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

CACHE_DIR = PROJECT_ROOT / "data" / "drive_cache"


@dataclass
class StatsIngestao:
    """Contadores do resultado da ingestao de um lote de arquivos."""

    inserido: int = 0
    atualizado: int = 0
    skip: int = 0
    erro: int = 0

    def registrar(self, acao: str) -> None:
        """Incrementa o contador da acao. Acao desconhecida vira erro."""
        if hasattr(self, acao):
            setattr(self, acao, getattr(self, acao) + 1)
        else:
            self.erro += 1


def processar_arquivos(
    arquivos: list,
    cache_dir: Path,
    heuristicas,
    force: bool,
    dry_run: bool,
) -> StatsIngestao:
    """Baixa e processa cada arquivo do Drive, retornando estatisticas.

    Separada de main() para ser testavel sem argparse nem Drive real, e
    reutilizavel por outros orquestradores (ex.: sync nightly via n8n).

    Erro de download e contabilizado como `erro` e nao interrompe o lote
    — um PDF problematico nao deve derrubar a ingestao dos demais.

    Args:
        arquivos: lista de ArquivoDrive descobertos na travessia.
        cache_dir: diretorio (ja existente) para o cache de PDFs.
        heuristicas: conjunto de heuristicas de dominio para o pipeline.
        force: se True, reprocessa mesmo com SHA256 inalterado.
        dry_run: se True, nao grava (delegado ao processar_documento).

    Returns:
        StatsIngestao com a contagem por acao.
    """
    stats = StatsIngestao()
    total = len(arquivos)

    for i, arquivo in enumerate(arquivos, 1):
        try:
            fonte = de_arquivo_drive(arquivo, cache_dir)
        except Exception as e:
            # Erro de download: sem o arquivo em disco nao ha como
            # delegar ao pipeline. Conta como erro do proprio adapter.
            log.error(
                "  [%d/%d] [download-erro] %s: %s",
                i, total, arquivo.nome, e,
            )
            stats.erro += 1
            continue

        resultado = processar_documento(
            fonte, heuristicas=heuristicas, force=force
        )
        acao = resultado["acao"]
        stats.registrar(acao)

        if acao == "skip":
            log.info("  [%d/%d] [skip]       %s", i, total, arquivo.nome)
        elif acao == "erro":
            log.error(
                "  [%d/%d] [erro]       %s: %s",
                i, total, arquivo.nome, resultado.get("motivo", "?"),
            )
        else:
            log.info(
                "  [%d/%d] [%s]  %s (%d pags, %d chars, tipo=%s)",
                i, total, acao, arquivo.nome,
                resultado["num_paginas"], resultado["texto_chars"],
                resultado["tipo"],
            )

    return stats


def _imprimir_dry_run(arquivos: list) -> None:
    log.info("=" * 70)
    log.info("DRY-RUN: nada foi baixado nem gravado no banco.")
    log.info("=" * 70)

    por_marca: dict[str, int] = {}
    for a in arquivos:
        por_marca[a.marca_pasta] = por_marca.get(a.marca_pasta, 0) + 1

    log.info("PDFs a processar por marca:")
    for marca, n in sorted(por_marca.items(), key=lambda x: -x[1]):
        log.info("  %4d  %s", n, marca)

    log.info("-" * 70)
    log.info("Primeiros 10 arquivos:")
    for a in arquivos[:10]:
        log.info("  %s / %s", a.marca_pasta, a.nome)

    log.info("-" * 70)
    log.info("Total: %d PDFs", len(arquivos))


def _imprimir_resumo(stats: StatsIngestao) -> None:
    log.info("-" * 70)
    log.info("Inseridos:    %d", stats.inserido)
    log.info("Atualizados:  %d", stats.atualizado)
    log.info("Sem mudanca:  %d", stats.skip)
    log.info("Erros:        %d", stats.erro)
    log.info("=" * 70)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingere PDFs do Google Drive (Fase 1)."
    )
    parser.add_argument(
        "folder_id",
        help="ID da pasta raiz no Drive (contem as pastas de marca).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista o que seria feito, sem baixar nem gravar no banco.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Processa apenas os primeiros N PDFs (uso: teste rapido).",
    )
    parser.add_argument(
        "--heuristicas",
        choices=list(CONJUNTOS_HEURISTICAS.keys()) + ["none"],
        default=None,
        help=(
            "Conjunto de heuristicas de dominio a ativar. "
            f"Default: {INGEST_HEURISTICAS!r} (config.INGEST_HEURISTICAS). "
            "'none' = core puro."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocessa mesmo se o SHA256 nao mudou.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # CLI vence config; config e o default para ingestao automatica.
    nome_heuristicas = (
        args.heuristicas if args.heuristicas is not None else INGEST_HEURISTICAS
    )
    heuristicas = resolver_heuristicas(nome_heuristicas)

    log.info("Descobrindo PDFs no Drive (folder_id=%s)...", args.folder_id)
    arquivos = listar_pdfs_recursivo(args.folder_id)
    log.info("Total descoberto: %d PDFs", len(arquivos))

    if args.limit is not None:
        arquivos = arquivos[: args.limit]
        log.info("Limite aplicado: processando %d PDFs", len(arquivos))

    if args.dry_run:
        _imprimir_dry_run(arquivos)
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Cache: %s", CACHE_DIR)
    log.info("Heuristicas: %s", nome_heuristicas)
    log.info("-" * 70)

    stats = processar_arquivos(
        arquivos,
        cache_dir=CACHE_DIR,
        heuristicas=heuristicas,
        force=args.force,
        dry_run=args.dry_run,
    )

    _imprimir_resumo(stats)


if __name__ == "__main__":
    main()