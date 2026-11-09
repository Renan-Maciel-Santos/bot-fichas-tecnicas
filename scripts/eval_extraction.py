"""
Eval de regressao da extracao de propriedades.

Compara a extracao real (LLM) contra um gabarito congelado dos
documentos de referencia. Uso durante calibracao de prompt:
mudou o prompt -> roda o eval -> compara o score.

Uso:
    python -m scripts.eval_extraction
"""

import sys

from src.db import get_connection
from src.extraction import extract_properties

# Gabarito congelado — construido por leitura humana dos documentos.
# Formato: codigo_produto -> {campo: valor_esperado_exato ou None}
# valor None = o campo NAO deve ser extraido deste documento.
GOLDEN = {
    9000001: {  # FDS Agua Sanitaria Limpax
        "ingrediente_ativo": "Hipoclorito de Sódio",
        "numero_cas": "7681-52-9",
        "ph": "10 - 13",
        "numero_onu": "1791",
        "palavra_advertencia": "Perigo",
        "classificacao_abnt": None,  # nao existe classificacao ABNT 15464 nesta FISPQ
    },
    9000004: {  # Ficha tecnica P. HIG ROLAO FOLHABELA
        "gramatura": "16 g/m 2 (variação 2%)",
        "metragem_rolo": "300 m  (variação +/- 2%)",
        "composicao_material": "100% celulose virgem",
        "classificacao_abnt": "Classe 1",  # prefixo aceito (valor completo: Classe 1 + norma)
        "ingrediente_ativo": None,  # papel nao tem ingrediente ativo
    },
}

SQL_TEXTO = """
    SELECT d.texto_extraido
    FROM documentos d
    JOIN produto_documento pd ON pd.documento_id = d.id
    WHERE pd.produto_codigo = %(codigo)s
"""


def avaliar(codigo: int, gabarito: dict) -> tuple[int, int, list[str]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_TEXTO, {"codigo": codigo})
            (texto,) = cur.fetchone()

    extraidas = {p.campo: p for p in extract_properties(texto)}

    acertos, falhas = 0, []
    for campo, esperado in gabarito.items():
        obtida = extraidas.get(campo)
        if esperado is None:
            if obtida is None:
                acertos += 1
            else:
                falhas.append(f"[{codigo}] {campo}: NAO deveria existir, veio {obtida.valor!r}")
        else:
            if obtida is None:
                falhas.append(f"[{codigo}] {campo}: esperado {esperado!r}, AUSENTE")
            elif not obtida.valor.startswith(esperado):
                falhas.append(f"[{codigo}] {campo}: esperado {esperado!r}, veio {obtida.valor!r}")
            else:
                acertos += 1

    return acertos, len(gabarito), falhas


def main() -> int:
    total_ok, total, todas_falhas = 0, 0, []
    for codigo, gabarito in GOLDEN.items():
        ok, n, falhas = avaliar(codigo, gabarito)
        total_ok += ok
        total += n
        todas_falhas.extend(falhas)

    print(f"\nEVAL DE EXTRACAO: {total_ok}/{total} checagens OK")
    for f in todas_falhas:
        print(f"  FALHA: {f}")

    return 0 if total_ok == total else 1


if __name__ == "__main__":
    sys.exit(main())