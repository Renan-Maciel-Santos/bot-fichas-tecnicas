"""Gera o catalogo e os PDFs ficticios da demo.

Tudo aqui e inventado: marcas, empresas, CNPJs, codigos e valores. Os
documentos imitam a estrutura de uma FDS (ABNT NBR 14725) e de uma ficha
tecnica o suficiente para o pipeline funcionar de verdade: classificacao
do tipo, extracao de propriedades, chunks e as tres camadas de vinculacao.

Cada arquivo foi pensado para exercitar um caminho:
    9100001 - FDS Aromatizante ...      Camada 0: codigo do produto no nome
    FDS Agua Sanitaria Limpax           Camada 1: nome parecido com o produto
    FISPQ - Gel Antisseptico Higimax    Camada 1: agrupa 500 ML e 1 L
    FDS-LIMPAX-DESINFETANTE             Camada 2: so o TEXTO diz as fragrancias
    Alvara de Funcionamento Higimax     filtro de documento regulatorio
    BRILHONET/...                       marca fora do catalogo: nunca vincula

Uso (na raiz do repositorio):
    python demo/gerar_dados.py
Saida: demo/catalogo.xlsx e demo/pdfs/<MARCA>/*.pdf
"""

from pathlib import Path

import fitz  # pymupdf
from openpyxl import Workbook

RAIZ = Path(__file__).resolve().parent

CABECALHO = ["Código", "ICMS ST", "Descrição", "NCM", "CEST", "Marca", "Grupo Produto", "Peso", "Texto para busca"]

PRODUTOS = [
    (9000001, "AGUA SANITARIA LIMPAX 1L", "LIMPAX", "QUIMICOS", 1.0),
    (9000003, "DETERGENTE LIQUIDO NEUTRO 500 ML LIMPAX", "LIMPAX", "QUIMICOS", 0.5),
    (9000004, "DESINFETANTE LAVANDA 2L LIMPAX", "LIMPAX", "QUIMICOS", 2.0),
    (9000005, "DESINFETANTE EUCALIPTO 2L LIMPAX", "LIMPAX", "QUIMICOS", 2.0),
    (9000006, "DESINFETANTE PINHO 2L LIMPAX", "LIMPAX", "QUIMICOS", 2.0),
    (9100001, "AROMATIZANTE DE AMBIENTES 120ML AROMAR", "AROMAR", "AROMATIZANTES", 0.12),
    (9100002, "DIFUSOR DE AROMAS 250ML AROMAR", "AROMAR", "AROMATIZANTES", 0.25),
    (9200001, "ALCOOL GEL ANTISSEPTICO 500 ML HIGIMAX", "HIGIMAX", "HIGIENE", 0.5),
    (9200002, "ALCOOL GEL ANTISSEPTICO 1 L HIGIMAX", "HIGIMAX", "HIGIENE", 1.0),
    (9200003, "REFIL SABONETE ERVA DOCE ESPUMA 700 ML HIGIMAX", "HIGIMAX", "HIGIENE", 0.7),
    (9200004, "REFIL SABONETE NEUTRO LIQUIDO 800 ML HIGIMAX", "HIGIMAX", "HIGIENE", 0.8),
    (9300001, "P.T. INTERF. 100% 20X20 1.000 FLS SLIM FOLHABELA", "FOLHABELA", "PAPEIS", 1.2),
    (9300002, "P. HIG ROLAO 100% CEL 8RLX300MT FOLHABELA", "FOLHABELA", "PAPEIS", 3.4),
]


def _fds(produto: str, fabricante: str, cnpj: str, uso: str, composicao: list[str],
         perigos: str, olhos: str, pele: str, aspecto: str, ph: str, densidade: str,
         extra: str = "") -> str:
    return f"""FICHA DE DADOS DE SEGURANCA (FDS)
Em conformidade com ABNT NBR 14725 - revisao 01
DOCUMENTO FICTICIO PARA DEMONSTRACAO

1. IDENTIFICACAO
Nome do produto: {produto}
Uso recomendado: {uso}
Fornecedor: {fabricante} (empresa ficticia)
CNPJ: {cnpj}
Telefone de emergencia: 0800 000 0000
{extra}
2. IDENTIFICACAO DE PERIGOS
{perigos}

3. COMPOSICAO E INFORMACOES SOBRE OS INGREDIENTES
Ingredientes que contribuem para o perigo:
""" + "\n".join(composicao) + f"""

4. MEDIDAS DE PRIMEIROS SOCORROS
Contato com os olhos: {olhos}
Contato com a pele: {pele}
Ingestao: nao induza o vomito. Procure atendimento medico levando esta ficha.

9. PROPRIEDADES FISICAS E QUIMICAS
Aspecto: {aspecto}
pH: {ph}
Densidade: {densidade}

10. ESTABILIDADE E REATIVIDADE
Produto estavel em condicoes normais de uso e armazenamento.
"""


DOCUMENTOS = {
    "LIMPAX": {
        "FDS Agua Sanitaria Limpax.pdf": _fds(
            "AGUA SANITARIA LIMPAX", "Limpax Industria Quimica Ltda", "00.000.000/0001-01",
            "Alvejante e desinfetante de uso geral",
            ["Ingrediente ativo: Hipoclorito de sodio | CAS: 7681-52-9 | Concentracao: 2,0 a 2,5% de cloro ativo",
             "Hidroxido de sodio - CAS 1310-73-2 - menor que 1%",
             "Agua - CAS 7732-18-5 - qsp 100%"],
            "Provoca irritacao ocular grave. Nao misturar com acidos ou amonia: libera gas cloro.",
            "lave imediatamente com agua corrente por pelo menos 15 minutos, mantendo as "
            "palpebras abertas. Retire lentes de contato. Procure um oftalmologista.",
            "lave com agua em abundancia e retire as roupas contaminadas.",
            "Liquido limpido, levemente amarelado, odor de cloro", "10 - 13", "1,03 a 1,05 g/cm3",
        ),
        "FDS Detergente Liquido Neutro Limpax.pdf": _fds(
            "DETERGENTE LIQUIDO NEUTRO LIMPAX", "Limpax Industria Quimica Ltda", "00.000.000/0001-01",
            "Lavagem manual de louças",
            ["Ingrediente ativo: Linear alquilbenzeno sulfonato de sodio | CAS: 68411-30-3 | Concentracao: 6 a 8%",
             "Coco amido propil betaina - CAS 61789-40-0 - 1 a 2%",
             "Agua - CAS 7732-18-5 - qsp 100%"],
            "Produto nao classificado como perigoso.",
            "lave com agua corrente por 15 minutos.",
            "enxague com agua.",
            "Liquido viscoso transparente", "6,5 - 7,5", "1,01 g/cm3",
        ),
        # So o texto diz que vale para as tres fragrancias: o nome do arquivo
        # empata as tres e a Camada 1 manda para a Camada 2.
        "FDS-LIMPAX-DESINFETANTE.pdf": _fds(
            "DESINFETANTE LIMPAX PARA USO GERAL - FRAGRANCIAS LAVANDA, EUCALIPTO E PINHO",
            "Limpax Industria Quimica Ltda", "00.000.000/0001-01",
            "Desinfeccao de pisos e superficies",
            ["Ingrediente ativo: Cloreto de alquil dimetil benzil amonio | CAS: 68424-85-1 | Concentracao: 0,3 a 0,5%",
             "Essencia (lavanda, eucalipto ou pinho) - menor que 1%",
             "Agua - CAS 7732-18-5 - qsp 100%"],
            "Pode causar irritacao ocular.",
            "lave com agua corrente por 15 minutos.",
            "lave com agua e sabao.",
            "Liquido colorido conforme a fragrancia", "6,0 - 8,0", "1,00 g/cm3",
            extra="Esta ficha se aplica as tres versoes do produto: Lavanda, Eucalipto e Pinho.\n",
        ),
    },
    "AROMAR": {
        "9100001 - FDS Aromatizante de Ambientes 120ml Aromar.pdf": _fds(
            "AROMATIZANTE DE AMBIENTES AROMAR 120ML", "Aromar Cosmeticos Ltda", "00.000.000/0001-02",
            "Aromatizacao de ambientes",
            ["Ingrediente ativo: Alcool etilico | CAS: 64-17-5 | Concentracao: 70 a 80%", "Fragrancia - menor que 5%"],
            "Liquido e vapores inflamaveis. Provoca irritacao ocular.",
            "lave com agua corrente por 15 minutos.",
            "lave com agua.",
            "Liquido incolor, odor caracteristico", "6,0 - 7,0", "0,86 g/cm3",
        ),
        "FDS Difusor de Aromas Aromar.pdf": _fds(
            "DIFUSOR DE AROMAS AROMAR", "Aromar Cosmeticos Ltda", "00.000.000/0001-02",
            "Aromatizacao continua de ambientes",
            ["Dipropilenoglicol - CAS 25265-71-8 - 60 a 70%", "Fragrancia - 10 a 15%"],
            "Pode provocar reacao alergica na pele.",
            "lave com agua corrente por 15 minutos.",
            "lave com agua e sabao.",
            "Liquido levemente amarelado", "nao aplicavel", "1,02 g/cm3",
        ),
    },
    "HIGIMAX": {
        "FISPQ - Gel Antisseptico Higimax.pdf": _fds(
            "ALCOOL GEL ANTISSEPTICO HIGIMAX 70 INPM", "Higimax Higiene Ltda", "00.000.000/0001-03",
            "Antissepsia das maos",
            ["Ingrediente ativo: Alcool etilico | CAS: 64-17-5 | Concentracao: 70% p/p",
             "Carbomer - CAS 9003-01-4 - menor que 1%",
             "Glicerina - CAS 56-81-5 - menor que 2%"],
            "Liquido e vapores altamente inflamaveis. Provoca irritacao ocular grave.",
            "lave com agua corrente por 15 minutos. Procure um medico se a irritacao persistir.",
            "nao se aplica (uso topico).",
            "Gel transparente", "6,0 - 8,0", "0,88 g/cm3",
        ),
        "FT Refil Sabonete Espuma Erva Doce - Higimax.pdf": """FICHA TECNICA
DOCUMENTO FICTICIO PARA DEMONSTRACAO

Produto: REFIL SABONETE ESPUMA ERVA DOCE HIGIMAX 700 ML
Fabricante: Higimax Higiene Ltda (empresa ficticia)

Descricao: sabonete liquido para dispenser de espuma, fragrancia erva doce.
Aplicacao: higienizacao das maos em banheiros coletivos.

Caracteristicas tecnicas
Aspecto: liquido perolado
Cor: branco
pH: 6,0 - 7,0
Viscosidade: 50 a 150 cP
Rendimento: aproximadamente 1.400 doses por refil

Armazenamento: local seco e arejado, ao abrigo da luz.
Validade: 24 meses a partir da data de fabricacao.
""",
        "Alvara de Funcionamento Higimax.pdf": """ALVARA DE FUNCIONAMENTO
DOCUMENTO FICTICIO PARA DEMONSTRACAO

Empresa: Higimax Higiene Ltda
Atividade: fabricacao de produtos de higiene pessoal
Validade: 31/12/2099
""",
    },
    "FOLHABELA": {
        "9300001 - Toalha Interfolhada Folhabela Slim 20x20.pdf": """FICHA TECNICA - PAPEL TOALHA INTERFOLHADO
DOCUMENTO FICTICIO PARA DEMONSTRACAO

Produto: TOALHA INTERFOLHADA FOLHABELA SLIM 20 x 20 cm
Fabricante: Folhabela Papeis Ltda (empresa ficticia)
Composicao: 100% celulose virgem

Especificacoes tecnicas
Gramatura: 24 g/m2
Alvura: minimo 85%
Absorcao: maximo 10 segundos
Folhas por pacote: 1.000
pH do extrato aquoso: 6,0 - 8,0
""",
    },
    # Marca que o catalogo nao tem: a ficha nunca vincula, por nenhuma camada.
    "BRILHONET": {
        "FDS Limpa Vidros Brilhonet.pdf": _fds(
            "LIMPA VIDROS BRILHONET", "Brilhonet Quimica Ltda", "00.000.000/0001-04",
            "Limpeza de vidros e espelhos",
            ["Alcool isopropilico - CAS 67-63-0 - 5 a 10%", "Agua - CAS 7732-18-5 - qsp 100%"],
            "Liquido inflamavel.",
            "lave com agua corrente por 15 minutos.",
            "lave com agua.",
            "Liquido azul", "7,0 - 9,0", "0,99 g/cm3",
        ),
    },
}


def gerar_catalogo() -> None:
    planilha = Workbook()
    aba = planilha.active
    aba.append(CABECALHO)
    for codigo, descricao, marca, grupo, peso in PRODUTOS:
        aba.append([codigo, "NAO", descricao, "00000000", None, marca, grupo, peso,
                    f"{codigo} {descricao} {marca} {grupo}".lower()])
    planilha.save(RAIZ / "catalogo.xlsx")


def gerar_pdfs() -> None:
    for marca, documentos in DOCUMENTOS.items():
        pasta = RAIZ / "pdfs" / marca
        pasta.mkdir(parents=True, exist_ok=True)
        for nome, texto in documentos.items():
            pdf = fitz.open()
            pagina = pdf.new_page()
            pagina.insert_textbox(fitz.Rect(50, 50, 545, 800), texto, fontsize=10, fontname="helv")
            pdf.set_metadata({"title": nome.removesuffix(".pdf"), "creator": "demo/gerar_dados.py"})
            pdf.save(pasta / nome, deflate=True)
            pdf.close()


if __name__ == "__main__":
    gerar_catalogo()
    gerar_pdfs()
    total = sum(len(docs) for docs in DOCUMENTOS.values())
    print(f"catalogo: {len(PRODUTOS)} produtos | pdfs: {total} em {len(DOCUMENTOS)} pastas")
