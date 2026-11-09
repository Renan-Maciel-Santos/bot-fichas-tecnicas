"""Gera planilha de rotulagem para o especialista de dominio (cliente).

Por que existe:
    A verdade sobre qual documento pertence a qual produto esta com quem
    conhece o catalogo, nao com quem escreveu o algoritmo. Este script
    converte a amostra tecnica em uma planilha que um nao-tecnico consegue
    preencher sem contexto de engenharia.

Decisoes de metodo:
    - Rotulagem CEGA: as opcoes sao embaralhadas e o vinculo atual do
      sistema nao e identificado. Mostrar a escolha do algoritmo enviesaria
      a resposta (ancoragem) e invalidaria a medicao de precisao.
    - Para linhas ja vinculadas, o produto vinculado entra a forca entre as
      opcoes -- senao seria impossivel confirmar que o vinculo esta certo.
    - Seed fixa: o embaralhamento e reproduzivel, entao o gabarito da aba
      oculta sempre bate com a planilha entregue.

Pre-requisito:
    python -m scripts.gerar_golden_links

Uso:
    python -m scripts.gerar_planilha_cliente
"""

import csv
import logging
import random
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from src.db import get_connection

logger = logging.getLogger(__name__)

ENTRADA = Path("data/analise/golden_links_para_rotular.csv")
SAIDA = Path("data/analise/CONFERENCIA_DOCUMENTOS.xlsx")
SEED = 42
NUM_OPCOES = 5

LETRAS_OPCAO = ["A", "B", "C", "D", "E"]
RESPOSTAS_ESPECIAIS = ["Mais de um", "Nenhum", "Nao sei"]

COR_CABECALHO = "1F4E79"
COR_RESPOSTA = "FFF2CC"

SQL_CANDIDATOS = """
    SELECT p.codigo,
           p.descricao,
           similarity(unaccent(lower(p.descricao)), unaccent(lower(%(miolo)s))) AS score
    FROM produtos p
    WHERE unaccent(lower(p.marca)) = unaccent(lower(%(marca)s))
    ORDER BY score DESC
    LIMIT %(limite)s
"""

SQL_PRODUTO = "SELECT codigo, descricao FROM produtos WHERE codigo = %(codigo)s"

INSTRUCOES = [
    ("Conferencia de documentos x produtos", True),
    ("", False),
    ("O que e isto:", True),
    ("O sistema esta aprendendo a identificar a qual produto cada ficha tecnica / FDS", False),
    ("pertence. Para conferir se ele esta acertando, precisamos da resposta correta de", False),
    ("quem conhece o catalogo.", False),
    ("", False),
    ("Como preencher:", True),
    ("1. Va para a aba 'Documentos'.", False),
    ("2. Em cada linha, leia o nome do arquivo e as opcoes de produto (A ate E).", False),
    ("3. Na coluna RESPOSTA, escolha no menu suspenso a opcao correta.", False),
    ("", False),
    ("Opcoes de resposta:", True),
    ("A, B, C, D, E   -> o produto correto e esse", False),
    ("Mais de um      -> o documento vale para varios produtos (ex.: mesma formula,", False),
    ("                   so muda a fragrancia). Escreva quais na coluna ao lado.", False),
    ("Nenhum          -> nenhuma das opcoes corresponde. Se souber qual e o produto", False),
    ("                   certo, escreva o nome dele na coluna de observacao.", False),
    ("Nao sei         -> nao da para ter certeza.", False),
    ("", False),
    ("Importante:", True),
    ("'Nao sei' e uma resposta valida e util. Chute atrapalha mais do que ajuda,", False),
    ("porque o sistema vai aprender a resposta errada. Na duvida, marque 'Nao sei'.", False),
    ("", False),
    ("Sao 50 linhas, cerca de 20 a 30 minutos. Nao precisa fazer tudo de uma vez --", False),
    ("salve e continue depois. Obrigado!", False),
]


def _buscar_opcoes(cur, marca: str, miolo: str, codigo_forcado: int | None) -> list[tuple[int, str]]:
    """Retorna ate NUM_OPCOES produtos candidatos, garantindo o codigo forcado.

    O codigo_forcado (vinculo ja existente) pode nao estar entre os melhores
    scores; sem ele na lista, seria impossivel o especialista confirmar que o
    vinculo atual esta correto.
    """
    cur.execute(SQL_CANDIDATOS, {"miolo": miolo, "marca": marca, "limite": NUM_OPCOES})
    opcoes = [(codigo, descricao) for codigo, descricao, _score in cur.fetchall()]

    if codigo_forcado is not None and all(codigo != codigo_forcado for codigo, _ in opcoes):
        cur.execute(SQL_PRODUTO, {"codigo": codigo_forcado})
        registro = cur.fetchone()
        if registro is not None:
            if len(opcoes) >= NUM_OPCOES:
                opcoes.pop()
            opcoes.append((registro[0], registro[1]))

    return opcoes


def _montar_linhas(cur, registros: list[dict[str, str]]) -> list[dict[str, object]]:
    """Monta as linhas da planilha com opcoes embaralhadas de forma reproduzivel."""
    aleatorio = random.Random(SEED)
    linhas: list[dict[str, object]] = []

    for numero, registro in enumerate(registros, start=1):
        bruto = registro.get("vinculo_atual_codigo") or ""
        codigo_forcado = int(bruto) if bruto.strip() else None

        opcoes = _buscar_opcoes(
            cur,
            marca=registro["marca_pasta"],
            miolo=registro["miolo"],
            codigo_forcado=codigo_forcado,
        )
        aleatorio.shuffle(opcoes)

        linhas.append({
            "numero": numero,
            "estrato": registro["estrato"],
            "marca": registro["marca_pasta"],
            "arquivo": registro["nome_arquivo"],
            "opcoes": opcoes,
            "codigo_vinculo_atual": codigo_forcado,
        })

    return linhas


def _escrever_instrucoes(planilha) -> None:
    aba = planilha.active
    aba.title = "Instrucoes"
    aba.column_dimensions["A"].width = 95

    for indice, (texto, negrito) in enumerate(INSTRUCOES, start=1):
        celula = aba.cell(row=indice, column=1, value=texto)
        celula.font = Font(bold=negrito, size=13 if indice == 1 else 11)


def _escrever_documentos(planilha, linhas: list[dict[str, object]]) -> None:
    aba = planilha.create_sheet("Documentos")

    cabecalho = (
        ["Nº", "Marca", "Arquivo"]
        + [f"Opção {letra}" for letra in LETRAS_OPCAO]
        + ["RESPOSTA", "Se marcou 'Mais de um', quais?", "Observação"]
    )
    aba.append(cabecalho)

    for indice, titulo in enumerate(cabecalho, start=1):
        celula = aba.cell(row=1, column=indice)
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor=COR_CABECALHO)
        celula.alignment = Alignment(vertical="center", wrap_text=True)

    for linha in linhas:
        opcoes = linha["opcoes"]
        descricoes = [
            opcoes[i][1] if i < len(opcoes) else ""
            for i in range(NUM_OPCOES)
        ]
        aba.append([linha["numero"], linha["marca"], linha["arquivo"], *descricoes, "", "", ""])

    coluna_resposta = 3 + NUM_OPCOES + 1
    formula = '"{}"'.format(",".join(LETRAS_OPCAO + RESPOSTAS_ESPECIAIS))
    validacao = DataValidation(type="list", formula1=formula, allow_blank=True, showDropDown=False)
    validacao.error = "Escolha uma das opcoes do menu."
    validacao.prompt = "Selecione a opcao correta."
    aba.add_data_validation(validacao)

    letra_resposta = get_column_letter(coluna_resposta)
    validacao.add(f"{letra_resposta}2:{letra_resposta}{len(linhas) + 1}")

    for numero_linha in range(2, len(linhas) + 2):
        celula = aba.cell(row=numero_linha, column=coluna_resposta)
        celula.fill = PatternFill("solid", fgColor=COR_RESPOSTA)
        celula.alignment = Alignment(horizontal="center", vertical="center")

    larguras = [5, 14, 46] + [40] * NUM_OPCOES + [16, 30, 34]
    for indice, largura in enumerate(larguras, start=1):
        aba.column_dimensions[get_column_letter(indice)].width = largura

    for numero_linha in range(2, len(linhas) + 2):
        aba.row_dimensions[numero_linha].height = 34
        for coluna in range(2, 3 + NUM_OPCOES + 1):
            aba.cell(row=numero_linha, column=coluna).alignment = Alignment(
                vertical="center", wrap_text=True
            )

    aba.freeze_panes = "D2"
    aba.auto_filter.ref = f"A1:{get_column_letter(len(cabecalho))}{len(linhas) + 1}"


def _escrever_gabarito(planilha, linhas: list[dict[str, object]]) -> None:
    """Aba oculta: mapeia letra -> codigo do produto, para reverter as respostas."""
    aba = planilha.create_sheet("_gabarito")
    aba.append(
        ["numero", "estrato", "arquivo", "codigo_vinculo_atual"]
        + [f"opcao_{letra}_codigo" for letra in LETRAS_OPCAO]
    )

    for linha in linhas:
        opcoes = linha["opcoes"]
        codigos = [
            opcoes[i][0] if i < len(opcoes) else None
            for i in range(NUM_OPCOES)
        ]
        aba.append([
            linha["numero"],
            linha["estrato"],
            linha["arquivo"],
            linha["codigo_vinculo_atual"],
            *codigos,
        ])

    aba.sheet_state = "hidden"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if not ENTRADA.exists():
        raise SystemExit(f"Arquivo nao encontrado: {ENTRADA}. Rode scripts.gerar_golden_links antes.")

    with ENTRADA.open(encoding="utf-8-sig") as arquivo:
        registros = list(csv.DictReader(arquivo))

    with get_connection() as conn:
        with conn.cursor() as cur:
            linhas = _montar_linhas(cur, registros)
        conn.rollback()  # script e read-only; explicito para deixar a intencao clara

    planilha = Workbook()
    _escrever_instrucoes(planilha)
    _escrever_documentos(planilha, linhas)
    _escrever_gabarito(planilha, linhas)
    planilha.save(SAIDA)

    logger.info("Planilha gerada: %s (%d documentos)", SAIDA, len(linhas))
    logger.info("Aba '_gabarito' esta oculta -- nao remova, ela mapeia letra -> codigo.")


if __name__ == "__main__":
    main()