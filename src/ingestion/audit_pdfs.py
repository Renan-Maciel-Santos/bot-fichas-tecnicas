"""
Auditoria rápida de PDFs (v2 — usando pymupdf, melhor com acentos).

Lê todos os PDFs em um diretório e reporta:
- Tamanho e número de páginas
- Se é texto-nativo ou provavelmente escaneado (precisa OCR)
- Tipo provável (FT/FDS/Laudo) com base em palavras-chave
- Amostra do texto extraído

Uso:
    python -m src.ingestion.audit_pdfs pdf/
    python -m src.ingestion.audit_pdfs pdf/ --json relatorio.json
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import pymupdf  # PyMuPDF — extração de texto de alta qualidade

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# Limiar para considerar PDF como texto-nativo
MIN_CHARS_PARA_TEXTO_NATIVO = 100

# Palavras-chave para classificação de tipo de documento
KEYWORDS_FT = [
    "ficha técnica", "ficha tecnica",
    "especificação técnica", "especificacao tecnica",
    "boletim técnico", "boletim tecnico",
    "informações técnicas", "informacoes tecnicas",
    "tipo de produto",
]
KEYWORDS_FDS = [
    "ficha de informações de segurança", "ficha de informacoes de seguranca",
    "fispq",
    "ficha de dados de segurança", "ficha de dados de seguranca",
    "ficha com dados de segurança", "ficha com dados de seguranca",
    "safety data sheet",
    "identificação dos perigos", "identificacao dos perigos",
    "nbr 14725",
]
KEYWORDS_LAUDO = [
    "laudo",
    "relatório de ensaio", "relatorio de ensaio",
    "certificado de análise", "certificado de analise",
    "boletim de análise", "boletim de analise",
    "resultado do ensaio",
]


def extrair_texto(caminho: Path, max_paginas: int = 3) -> str:
    """
    Extrai texto de um PDF usando pymupdf.
    Lê só as primeiras `max_paginas` (suficiente para auditoria).
    """
    doc = pymupdf.open(str(caminho))
    try:
        textos = []
        for i, page in enumerate(doc):
            if i >= max_paginas:
                break
            textos.append(page.get_text())
        return "\n".join(textos)
    finally:
        doc.close()


def num_paginas(caminho: Path) -> int:
    """Retorna número total de páginas do PDF."""
    doc = pymupdf.open(str(caminho))
    try:
        return doc.page_count
    finally:
        doc.close()


def classificar_tipo(texto: str) -> tuple[str, float]:
    """
    Classifica o tipo de documento com base em palavras-chave.
    Retorna (tipo, confianca).
    """
    if not texto:
        return "INDEFINIDO", 0.0

    texto_lower = texto.lower()
    scores = {
        "FT": sum(1 for k in KEYWORDS_FT if k in texto_lower),
        "FDS": sum(1 for k in KEYWORDS_FDS if k in texto_lower),
        "LAUDO": sum(1 for k in KEYWORDS_LAUDO if k in texto_lower),
    }

    max_score = max(scores.values())
    if max_score == 0:
        return "INDEFINIDO", 0.0

    tipo = max(scores, key=scores.get)
    confianca = min(1.0, max_score / 3)
    return tipo, confianca


def analisar_pdf(caminho: Path) -> dict:
    """Analisa um PDF e retorna metadados + sample de texto."""
    info = {
        "arquivo": caminho.name,
        "tamanho_kb": round(caminho.stat().st_size / 1024, 1),
        "num_paginas": None,
        "texto_extraido_chars": 0,
        "is_texto_nativo": False,
        "tipo_provavel": "INDEFINIDO",
        "confianca_tipo": 0.0,
        "amostra_texto": "",
        "erro": None,
    }

    try:
        info["num_paginas"] = num_paginas(caminho)
        texto = extrair_texto(caminho, max_paginas=3)

        info["texto_extraido_chars"] = len(texto)
        info["is_texto_nativo"] = len(texto) >= MIN_CHARS_PARA_TEXTO_NATIVO
        info["amostra_texto"] = texto[:300].replace("\n", " ").strip()

        if info["is_texto_nativo"]:
            tipo, conf = classificar_tipo(texto)
            info["tipo_provavel"] = tipo
            info["confianca_tipo"] = round(conf, 2)

    except Exception as e:
        info["erro"] = str(e)

    return info


def imprimir_relatorio(resultados: list[dict]):
    """Imprime relatório formatado no terminal."""
    print()
    print("=" * 90)
    print(f"AUDITORIA DE PDFs — {len(resultados)} arquivos")
    print("=" * 90)
    print()

    for r in resultados:
        if r["erro"]:
            print(f"  [ERRO] {r['arquivo']}: {r['erro']}")
            print()
            continue

        status = "TEXTO " if r["is_texto_nativo"] else "ESCAN?"
        tipo = r["tipo_provavel"]
        print(f"  [{status}] [{tipo:<10}] {r['arquivo']}")
        print(f"          {r['num_paginas']} pags  |  {r['tamanho_kb']} KB  |  {r['texto_extraido_chars']} chars extraidos")
        if r["amostra_texto"]:
            amostra = r["amostra_texto"][:150].strip()
            print(f"          amostra: \"{amostra}...\"")
        print()

    print("-" * 90)
    print("RESUMO")
    print("-" * 90)

    total = len(resultados)
    com_erro = sum(1 for r in resultados if r["erro"])
    texto_nativo = sum(1 for r in resultados if r["is_texto_nativo"])
    escaneados = total - texto_nativo - com_erro

    print(f"  Total de PDFs:              {total}")
    print(f"  Texto-nativo (ok):          {texto_nativo} ({100*texto_nativo/total:.0f}%)")
    print(f"  Provavelmente escaneados:   {escaneados} ({100*escaneados/total:.0f}%)  ->  precisa OCR")
    print(f"  Com erro de leitura:        {com_erro}")
    print()

    tipos = {}
    for r in resultados:
        t = r["tipo_provavel"]
        tipos[t] = tipos.get(t, 0) + 1
    print("  Distribuicao por tipo (estimativa por palavra-chave):")
    for tipo, qtd in sorted(tipos.items(), key=lambda x: -x[1]):
        print(f"    {tipo:<12}  {qtd}")
    print()

    print("-" * 90)
    print("RECOMENDACAO")
    print("-" * 90)
    if escaneados == 0:
        print("  OK Todos os PDFs sao texto-nativo. NAO precisa OCR.")
        print("     Pipeline mais simples e barato.")
    elif escaneados <= total * 0.2:
        print(f"  ~  Poucos escaneados ({escaneados}/{total}). Considerar OCR opcional.")
    else:
        print(f"  !  Muitos escaneados ({escaneados}/{total}). OCR sera necessario.")
        print("     Sugestoes: tesseract (gratuito, local) ou Google Vision API (~R$ 0,10/pagina).")
    print()


def main():
    parser = argparse.ArgumentParser(description="Auditoria rapida de PDFs")
    parser.add_argument("diretorio", help="Pasta contendo os PDFs (ex: pdf/)")
    parser.add_argument("--json", help="Opcional: caminho para salvar relatorio em JSON", default=None)
    args = parser.parse_args()

    pasta = Path(args.diretorio)
    if not pasta.exists():
        log.error(f"Pasta nao encontrada: {pasta}")
        sys.exit(1)

    pdfs = sorted(pasta.glob("*.pdf"))
    if not pdfs:
        log.error(f"Nenhum PDF encontrado em {pasta}")
        sys.exit(1)

    log.info(f"Analisando {len(pdfs)} PDFs em {pasta}...")
    resultados = [analisar_pdf(pdf) for pdf in pdfs]

    imprimir_relatorio(resultados)

    if args.json:
        Path(args.json).write_text(
            json.dumps(resultados, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(f"Relatorio JSON salvo em {args.json}")


if __name__ == "__main__":
    main()
