"""
Adapter que traduz um `ArquivoDrive` em `DocumentoFonte`.

Este modulo e a "ponte" entre a camada de I/O do Drive
(`src/drive/client.py`) e o contrato do dominio
(`src/ingestion/fonte.py`). Aplicacao concreta do Dependency Inversion
Principle: o pipeline de ingestao depende do abstrato `DocumentoFonte`,
e este adapter e a implementacao para a origem Drive.

Responsabilidades:
- Baixar o PDF para um path temporario (o pipeline precisa do arquivo
  em disco).
- Construir o DocumentoFonte com todos os campos preenchidos:
  identificador estavel, url publica e timestamp de modificacao (o que
  o pipeline local `de_arquivo_local` nao consegue preencher).

Nao-responsabilidades:
- Nao conhece o schema do banco.
- Nao decide onde criar o cache — o caller passa o `cache_dir`.
- Nao apaga o cache — quem cria e dono da limpeza.
"""

import logging
from pathlib import Path

from src.drive.client import ArquivoDrive, baixar_arquivo
from src.ingestion.fonte import DocumentoFonte

logger = logging.getLogger(__name__)


def de_arquivo_drive(
    arquivo: ArquivoDrive,
    cache_dir: Path,
) -> DocumentoFonte:
    """Baixa um arquivo do Drive e o embrulha em um DocumentoFonte.

    O download vai para `cache_dir / <file_id>.pdf`. O nome estavel
    baseado no `file_id` (nao no nome) evita colisao entre marcas
    diferentes que tenham arquivos de mesmo nome (ex.: varias
    "FDS.pdf"), e permite reuso do arquivo se ele ja tiver sido baixado
    em uma execucao anterior (idempotencia barata).

    Args:
        arquivo: metadados retornados pela travessia do Drive.
        cache_dir: diretorio ja existente onde o PDF sera colocado.

    Returns:
        DocumentoFonte com identificador = `file_id` (opaco, sem prefixo
        `local:`), url = link publico e modificado_em preenchido.

    Raises:
        FileNotFoundError: se `cache_dir` nao existir.
        HttpError: propaga erro da API do Drive (rede, permissao).
    """
    if not cache_dir.is_dir():
        raise FileNotFoundError(
            f"cache_dir nao existe: {cache_dir}. "
            f"O caller deve criar o diretorio antes de chamar de_arquivo_drive."
        )

    destino = cache_dir / f"{arquivo.file_id}.pdf"

    if destino.is_file():
        logger.debug(
            "Cache hit para %s (%s) — reusando arquivo local.",
            arquivo.file_id, arquivo.nome,
        )
    else:
        logger.info("Baixando %s (%s)...", arquivo.file_id, arquivo.nome)
        baixar_arquivo(arquivo.file_id, destino)

    return DocumentoFonte(
        identificador=arquivo.file_id,
        caminho_local=destino,
        nome_arquivo=arquivo.nome,
        marca_pasta=arquivo.marca_pasta,
        modificado_em=arquivo.modificado_em,
        url=arquivo.url_web,
    )