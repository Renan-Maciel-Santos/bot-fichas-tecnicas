"""
Ingestão de PDFs para a tabela `documentos`.

Para cada documento recebido como `DocumentoFonte`:
- Calcula SHA256 do conteúdo
- Extrai texto completo (todas as páginas) via pymupdf
- Classifica o tipo do documento (FT/FDS/LAUDO) via src.classify_doc_type
- Calcula metadados (tamanho, num páginas)
- Insere/atualiza na tabela documentos (UPSERT)
- Se o hash não mudou, pula a re-extração (idempotente)

Este módulo é agnóstico de origem: o pipeline recebe `DocumentoFonte`
(ver src/ingestion/fonte.py) e não sabe se o PDF veio de disco local ou
do Google Drive. O `main()` deste arquivo é o adapter de disco local;
o sync do Drive é outro adapter que reusa `processar_documento`.

Uso (adapter local):
    python -m src.ingestion.ingest_pdfs pdf/
    python -m src.ingestion.ingest_pdfs pdf/ --marca-pasta PAPELMAIS
    python -m src.ingestion.ingest_pdfs pdf/ --heuristicas none  # core puro
    python -m src.ingestion.ingest_pdfs pdf/ --force

O UPSERT preserva a classificação anterior via COALESCE: se o
classificador retornar NULL numa reingestão, o `tipo` já gravado (por
backfill ou revisão humana) não é sobrescrito.
"""
import argparse
import hashlib
import logging
import sys
from datetime import datetime
from pathlib import Path

import pymupdf

from src.classify_doc_type import HeuristicDetector, classify_doc_type
from src.config import CONFIANCA_MINIMA_TIPO, INGEST_HEURISTICAS
from src.config import DATABASE_URL_ADMIN
from src.db import get_connection
from src.heuristics_registry import CONJUNTOS_HEURISTICAS, resolver_heuristicas
from src.ingestion.fonte import DocumentoFonte

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def compute_sha256(caminho: Path) -> str:
    """SHA256 hexadecimal do conteúdo do arquivo."""
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extrair_texto_completo(caminho: Path) -> tuple[str, int]:
    """Extrai texto de todas as páginas. Retorna (texto_completo, num_paginas)."""
    doc = pymupdf.open(str(caminho))
    try:
        textos = [page.get_text() for page in doc]
        return "\n".join(textos), doc.page_count
    finally:
        doc.close()


def _calcular_tipo_para_gravar(
    nome_arquivo: str,
    texto_extraido: str | None,
    heuristicas: list[HeuristicDetector] | None,
) -> str | None:
    """Classifica o documento e devolve o tipo APENAS se a confianca
    atinge o threshold definido em config.CONFIANCA_MINIMA_TIPO.

    Funcao pura (nao toca em banco, nao le arquivo): testavel em memoria.

    Returns:
        "FT" | "FDS" | "LAUDO" se confianca suficiente; None caso contrario.
        None significa "grava NULL" (documento vai para v_pendentes_revisao).
    """
    resultado = classify_doc_type(
        nome_arquivo=nome_arquivo,
        texto_extraido=texto_extraido,
        heuristicas=heuristicas,
    )
    if resultado.tipo is None or resultado.confianca < CONFIANCA_MINIMA_TIPO:
        return None
    return resultado.tipo


def hash_existente(identificador: str) -> str | None:
    """Retorna o hash atual do documento no banco, ou None se não existir."""
    # Ingestao escreve no acervo: conecta como botuser. A role da aplicacao
    # (bot_app) so le -- ver db/provisioning/001b_bot_app_grants.sql.
    with get_connection(DATABASE_URL_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT hash_conteudo FROM documentos WHERE drive_file_id = %s",
                (identificador,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def atualizar_marca_temporal(
    identificador: str,
    modificado_em: datetime | None,
) -> None:
    """Atualiza APENAS o timestamp de modificacao da origem.

    Chamado quando o Nivel 2 da deteccao de mudanca (SHA256) confirma que
    o conteudo e identico, apesar de o Nivel 1 (timestamp) ter divergido.
    Cenario tipico: alguem abriu e re-salvou o arquivo no Drive sem
    alterar nada.

    Sem esta atualizacao, o Nivel 1 divergiria indefinidamente para aquele
    arquivo e o sync o baixaria em toda execucao --- desperdicio silencioso
    que so apareceria em metrica de banda, meses depois.
    """
    if modificado_em is None:
        return

    with get_connection(DATABASE_URL_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE documentos
                   SET data_modificacao_drive = %s,
                       updated_at             = NOW()
                 WHERE drive_file_id = %s
                """,
                (modificado_em, identificador),
            )


def registrar_erro(fonte: DocumentoFonte, mensagem: str) -> None:
    """Persiste uma falha de processamento sem destruir dados bons.

    Em execucao interativa, erros no stdout bastam --- alguem esta olhando.
    Em sync noturno desassistido, ninguem esta: a falha precisa ficar no
    banco para aparecer em v_pendentes_revisao e no painel.

    Politica de preservacao: se o documento ja existia e estava integro,
    apenas anota o erro. `texto_extraido`, `tipo` e `hash_conteudo`
    anteriores sao mantidos, e o bot segue respondendo com os dados
    antigos. Indisponibilidade e pior que dado levemente desatualizado.
    """
    sql = """
        INSERT INTO documentos (
            drive_file_id, nome_arquivo, marca_pasta,
            url_drive, data_modificacao_drive,
            erro_processamento, data_indexacao
        )
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (drive_file_id) DO UPDATE SET
            erro_processamento = EXCLUDED.erro_processamento,
            updated_at         = NOW()
    """
    with get_connection(DATABASE_URL_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    fonte.identificador,
                    fonte.nome_arquivo,
                    fonte.marca_pasta,
                    fonte.url,
                    fonte.modificado_em,
                    mensagem[:1000],
                ),
            )


def upsert_documento(
    fonte: DocumentoFonte,
    hash_conteudo: str,
    tamanho_bytes: int,
    num_paginas: int,
    texto_extraido: str,
    tipo: str | None,
) -> tuple[bool, str]:
    """
    Insere ou atualiza um documento processado com sucesso.
    Retorna (foi_inserido, doc_id_uuid).

    O UPDATE de `tipo` usa COALESCE(EXCLUDED.tipo, documentos.tipo):
    - Se a nova classificacao acerta um tipo, sobrescreve.
    - Se a nova classificacao devolve NULL, preserva o valor anterior
      (backfill ou revisao humana feita depois nao e destruida).

    `erro_processamento` e zerado: se um documento voltou a processar
    com sucesso, o erro anterior deixou de existir. Erro que sobrevive a
    propria correcao vira ruido e treina o operador a ignorar o painel.
    """
    sql = """
        INSERT INTO documentos (
            drive_file_id, nome_arquivo, marca_pasta,
            url_drive, data_modificacao_drive,
            hash_conteudo, tamanho_bytes, num_paginas,
            texto_extraido, tipo,
            erro_processamento, data_indexacao
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NOW())
        ON CONFLICT (drive_file_id) DO UPDATE SET
            nome_arquivo           = EXCLUDED.nome_arquivo,
            marca_pasta            = EXCLUDED.marca_pasta,
            url_drive              = COALESCE(EXCLUDED.url_drive, documentos.url_drive),
            data_modificacao_drive = COALESCE(
                EXCLUDED.data_modificacao_drive, documentos.data_modificacao_drive
            ),
            hash_conteudo          = EXCLUDED.hash_conteudo,
            tamanho_bytes          = EXCLUDED.tamanho_bytes,
            num_paginas            = EXCLUDED.num_paginas,
            texto_extraido         = EXCLUDED.texto_extraido,
            tipo                   = COALESCE(EXCLUDED.tipo, documentos.tipo),
            erro_processamento     = NULL,
            data_indexacao         = NOW(),
            updated_at             = NOW()
        RETURNING (xmax = 0) AS inserido, id
    """
    with get_connection(DATABASE_URL_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    fonte.identificador,
                    fonte.nome_arquivo,
                    fonte.marca_pasta,
                    fonte.url,
                    fonte.modificado_em,
                    hash_conteudo,
                    tamanho_bytes,
                    num_paginas,
                    texto_extraido,
                    tipo,
                ),
            )
            inserido, doc_id = cur.fetchone()
            return inserido, str(doc_id)


def processar_documento(
    fonte: DocumentoFonte,
    heuristicas: list[HeuristicDetector] | None,
    force: bool = False,
) -> dict:
    """Processa um unico documento. Retorna dict com status.

    Agnostico de origem: recebe um `DocumentoFonte` ja materializado em
    disco (seja um PDF local ou um temporario baixado do Drive) e nao
    conhece o mecanismo que o trouxe ate la.

    Args:
        fonte: Descritor do documento (identificador, caminho, metadados).
        heuristicas: Detectores de dominio opcionais para classificacao.
        force: Reprocessa mesmo que o SHA256 nao tenha mudado.

    Returns:
        dict com chave `acao` em {inserido, atualizado, skip, erro}.
    """
    hash_atual = compute_sha256(fonte.caminho_local)

    # Nivel 2 da deteccao de mudanca: conteudo identico dispensa
    # reprocessamento, mas o timestamp da origem precisa avancar --- ver
    # docstring de `atualizar_marca_temporal`.
    if not force:
        hash_prev = hash_existente(fonte.identificador)
        if hash_prev == hash_atual:
            atualizar_marca_temporal(fonte.identificador, fonte.modificado_em)
            return {"arquivo": fonte.nome_arquivo, "acao": "skip"}

    try:
        texto, num_pags = extrair_texto_completo(fonte.caminho_local)
    except Exception as e:
        registrar_erro(fonte, f"Falha na extracao de texto: {e}")
        return {"arquivo": fonte.nome_arquivo, "acao": "erro", "motivo": str(e)}

    # Classificacao — a decisao semantica acontece aqui, antes do upsert
    tipo = _calcular_tipo_para_gravar(fonte.nome_arquivo, texto, heuristicas)

    tamanho = fonte.caminho_local.stat().st_size

    try:
        inserido, doc_id = upsert_documento(
            fonte=fonte,
            hash_conteudo=hash_atual,
            tamanho_bytes=tamanho,
            num_paginas=num_pags,
            texto_extraido=texto,
            tipo=tipo,
        )
    except Exception as e:
        registrar_erro(fonte, f"Falha na gravacao: {e}")
        return {"arquivo": fonte.nome_arquivo, "acao": "erro", "motivo": str(e)}

    return {
        "arquivo": fonte.nome_arquivo,
        "acao": "inserido" if inserido else "atualizado",
        "doc_id": doc_id,
        "num_paginas": num_pags,
        "tamanho_bytes": tamanho,
        "texto_chars": len(texto),
        "tipo": tipo or "-",
    }


def main():
    """Adapter de disco local: varre uma pasta e delega ao pipeline."""
    parser = argparse.ArgumentParser(description="Ingere PDFs locais no banco")
    parser.add_argument("diretorio", help="Pasta contendo os PDFs")
    parser.add_argument(
        "--marca-pasta",
        default="local_amostra",
        help="Nome da pasta/marca para registrar (default: local_amostra)",
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
        help="Força re-extração mesmo se o hash não mudou",
    )
    args = parser.parse_args()

    # CLI vence config; config e o default para ingestao automatica
    nome_heuristicas = args.heuristicas if args.heuristicas is not None else INGEST_HEURISTICAS
    heuristicas = resolver_heuristicas(nome_heuristicas)

    pasta = Path(args.diretorio)
    if not pasta.exists():
        log.error(f"Pasta não encontrada: {pasta}")
        sys.exit(1)

    pdfs = sorted(pasta.glob("*.pdf"))
    if not pdfs:
        log.error(f"Nenhum PDF encontrado em {pasta}")
        sys.exit(1)

    log.info(
        f"Processando {len(pdfs)} PDFs "
        f"(marca_pasta='{args.marca_pasta}', heuristicas='{nome_heuristicas}')"
    )
    log.info("-" * 70)

    stats = {"inserido": 0, "atualizado": 0, "skip": 0, "erro": 0}

    for pdf in pdfs:
        fonte = DocumentoFonte.de_arquivo_local(pdf, args.marca_pasta)
        resultado = processar_documento(
            fonte, heuristicas=heuristicas, force=args.force
        )
        acao = resultado["acao"]
        stats[acao] += 1

        if acao == "skip":
            log.info(f"  [skip]       {pdf.name}")
        elif acao == "erro":
            log.error(f"  [erro]       {pdf.name}: {resultado['motivo']}")
        else:
            log.info(
                f"  [{acao}]  {pdf.name} "
                f"({resultado['num_paginas']} págs, "
                f"{resultado['texto_chars']} chars, tipo={resultado['tipo']})"
            )

    log.info("-" * 70)
    log.info(f"Inseridos:    {stats['inserido']}")
    log.info(f"Atualizados:  {stats['atualizado']}")
    log.info(f"Sem mudança:  {stats['skip']}")
    log.info(f"Erros:        {stats['erro']}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()