"""
Testes da leitura da planilha de conferência da Camada 2.

Foco na regra que protege o bot: só "Correto" vira vínculo. Errado,
Nao sei e linha em branco ficam de fora, e a linha da planilha é
casada com o gabarito oculto pelo número, não pela ordem.
"""

import pytest
from openpyxl import Workbook

from scripts.conferencia_llm import ler_aprovados


def _planilha(tmp_path, respostas):
    planilha = Workbook()
    sugestoes = planilha.active
    sugestoes.title = "Sugestoes"
    sugestoes.append(["Doc", "Marca", "Arquivo", "PDF", "Produto", "Trecho", "RESPOSTA", "Obs"])
    gabarito = planilha.create_sheet("_gabarito")
    gabarito.append(["linha", "documento_id", "produto_codigo"])
    for linha, resposta in enumerate(respostas, start=2):
        sugestoes.append([1, "M", "a.pdf", "", f"P{linha}", "t", resposta, None])
        gabarito.append([linha, f"doc-{linha}", 1000 + linha])
    caminho = tmp_path / "conferencia.xlsx"
    planilha.save(caminho)
    return caminho


def test_so_correto_vira_vinculo(tmp_path):
    caminho = _planilha(tmp_path, ["Correto", "Errado", "Nao sei", None, "correto "])
    aprovados, contagem = ler_aprovados(caminho)
    assert aprovados == [("doc-2", 1002), ("doc-6", 1006)]
    assert contagem == {"Correto": 1, "Errado": 1, "Nao sei": 1, "(em branco)": 1, "correto": 1}


def test_planilha_sem_respostas_nao_aprova_nada(tmp_path):
    aprovados, _ = ler_aprovados(_planilha(tmp_path, [None, None]))
    assert aprovados == []


@pytest.mark.integration
def test_documento_revogado_segue_para_quem_o_substituiu(db_transaction):
    """Resposta dada para uma copia revogada depois que a planilha saiu
    vai para a copia que ficou; revogado sem substituto nao tem destino."""
    import uuid

    from scripts.conferencia_llm import documento_ativo

    fica, sai, orfao = (str(uuid.uuid4()) for _ in range(3))
    with db_transaction.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documentos (id, drive_file_id, nome_arquivo, marca_pasta, revogado_em, substituido_por_id)
            VALUES (%s, %s, 'a.pdf', 'X', NULL, NULL),
                   (%s, %s, 'a (1).pdf', 'X', NOW(), %s),
                   (%s, %s, 'b.pdf', 'X', NOW(), NULL)
            """,
            (fica, f"t-{fica}", sai, f"t-{sai}", fica, orfao, f"t-{orfao}"),
        )
        assert documento_ativo(cur, fica) == fica
        assert documento_ativo(cur, sai) == fica
        assert documento_ativo(cur, orfao) is None


def test_linha_pulada_nao_vira_vinculo_mesmo_marcada(tmp_path):
    caminho = _planilha(tmp_path, ["Correto", "Correto", "Errado"])
    aprovados, contagem = ler_aprovados(caminho, pular=frozenset({3}))
    assert aprovados == [("doc-2", 1002)]
    assert contagem == {"Correto": 1, "(pulada)": 1, "Errado": 1}
