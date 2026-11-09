"""
Testes da leitura da planilha da rodada 2, sem banco.

Foco no casamento de cada resposta com o gabarito oculto: a linha da
aba visível precisa apontar para os ids certos, senão um "Errado"
apaga o vínculo de outro documento.
"""

from openpyxl import Workbook

from scripts.conferencia_rodada2 import (
    ABA_DUVIDAS,
    ABA_MARCAS,
    ABA_VINCULOS,
    ler_respostas,
)


def _planilha(tmp_path):
    planilha = Workbook()
    vinculos = planilha.active
    vinculos.title = ABA_VINCULOS
    vinculos.append(["Nº", "Pasta", "Arquivo", "PDF", "Produto", "Como", "RESPOSTA", "Obs"])
    vinculos.append([1, "P", "a.pdf", "", "Prod A", "x", "Correto", None])
    vinculos.append([2, "P", "b.pdf", "", "Prod B", "x", "Errado", None])
    vinculos.append([3, "P", "c.pdf", "", "Prod C", "x", None, None])

    duvidas = planilha.create_sheet(ABA_DUVIDAS)
    duvidas.append(["Nº", "Pasta", "Arquivo", "PDF", "Por que", "A", "B", "C", "D", "E",
                    "RESPOSTA", "Quais", "Obs"])
    duvidas.append([1, "P", "d.pdf", "", "tamanho", "X 1L", "X 5L", "", "", "",
                    "Mais de um", "A e B", None])

    marcas = planilha.create_sheet(ABA_MARCAS)
    marcas.append(["Pasta", "N", "Exemplos", "Sugestao", "RESPOSTA", "Outra"])
    marcas.append(["Start", 2, "x.pdf", "QUIMICA ALFA", "E a sugerida", None])
    marcas.append(["Tecnoclean", 83, "y.pdf", None, None, None])

    gabarito = planilha.create_sheet("_gabarito")
    gabarito.append(["aba", "linha", "ids", "opcoes"])
    gabarito.append([ABA_VINCULOS, 2, "id-a1,id-a2", ""])
    gabarito.append([ABA_VINCULOS, 3, "id-b", ""])
    gabarito.append([ABA_VINCULOS, 4, "id-c", ""])
    gabarito.append([ABA_DUVIDAS, 2, "doc-d", "101,102"])

    caminho = tmp_path / "rodada2.xlsx"
    planilha.save(caminho)
    return caminho


def test_vinculos_casam_resposta_e_ids_pela_linha(tmp_path):
    respostas = ler_respostas(_planilha(tmp_path))
    assert [(r["ids"], r["resposta"]) for r in respostas["vinculos"]] == [
        (["id-a1", "id-a2"], "Correto"),
        (["id-b"], "Errado"),
        (["id-c"], ""),
    ]


def test_duvida_traz_opcoes_do_gabarito_e_texto_livre(tmp_path):
    (duvida,) = ler_respostas(_planilha(tmp_path))["duvidas"]
    assert duvida["documento_id"] == "doc-d"
    assert duvida["opcoes"] == {"A": 101, "B": 102}
    assert (duvida["resposta"], duvida["quais"]) == ("Mais de um", "A e B")


def test_marcas_lidas_mesmo_sem_resposta(tmp_path):
    marcas = ler_respostas(_planilha(tmp_path))["marcas"]
    assert [(m["pasta"], m["resposta"]) for m in marcas] == [("Start", "E a sugerida"), ("Tecnoclean", "")]
