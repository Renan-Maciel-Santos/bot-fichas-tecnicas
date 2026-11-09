"""
Eval de regressao do motor de resposta (Fase 4).

Roda cada caso do golden set (tests/golden/motor_vN.yaml) contra o
motor completo (classifier -> search -> responder) e mede:
  - Acerto de intent
  - Acerto de produto (quando aplicavel)
  - Substrings obrigatorias / proibidas na resposta
  - Presenca de citacao (verificacao estrutural via produtos/documentos)
  - Latencia p50/p95
  - Distribuicao de intents (proxy de custo LLM)

Uso:
    python -m scripts.eval_motor

Exit code:
    0 - todos os casos passaram
    1 - pelo menos um caso falhou

Design: mesma filosofia do scripts/eval_extraction.py — gabarito
frozen (versao no nome do arquivo), medicao objetiva por criterio,
sem "olhometro". O YAML e editavel sem tocar em codigo, para
permitir que o golden set cresca com o uso real.

O log em `consultas` vai marcado com canal="eval" para nao poluir a
auditoria de uso real.
"""

import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.query.responder import RespostaBot, responder

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "tests" / "golden" / "motor_v1.yaml"

CAMPOS_PERMITIDOS = {
    "id",
    "pergunta",
    "intent_esperado",
    "produto_esperado_codigo",
    "deve_conter",
    "nao_deve_conter",
    "exige_citacao",
}


@dataclass(frozen=True)
class CasoTeste:
    """Representa um caso do golden set."""

    id: str
    pergunta: str
    intent_esperado: str
    produto_esperado_codigo: int | None = None
    deve_conter: list[str] = field(default_factory=list)
    nao_deve_conter: list[str] = field(default_factory=list)
    exige_citacao: bool = False


@dataclass(frozen=True)
class Falha:
    """Um criterio que nao foi satisfeito por um caso."""

    criterio: str
    detalhe: str


@dataclass
class ResultadoCaso:
    """Resultado da execucao de um caso: resposta obtida + falhas encontradas."""

    caso: CasoTeste
    intencao_obtida: str
    latencia_ms: int
    falhas: list[Falha]

    @property
    def passou(self) -> bool:
        return not self.falhas


def _carregar_golden(path: Path) -> list[CasoTeste]:
    """Le o YAML e valida schema. Erro de schema explode cedo, com mensagem clara."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "casos" not in data:
        raise ValueError(f"YAML sem chave 'casos' na raiz: {path}")

    casos: list[CasoTeste] = []
    for i, item in enumerate(data["casos"]):
        if not isinstance(item, dict):
            raise ValueError(f"Caso na posicao {i} nao e um mapping YAML")

        desconhecidos = set(item.keys()) - CAMPOS_PERMITIDOS
        if desconhecidos:
            raise ValueError(
                f"Caso {item.get('id', i)!r} tem campos desconhecidos: "
                f"{sorted(desconhecidos)}. Permitidos: {sorted(CAMPOS_PERMITIDOS)}"
            )

        try:
            casos.append(
                CasoTeste(
                    id=str(item["id"]),
                    pergunta=item["pergunta"],
                    intent_esperado=item["intent_esperado"],
                    produto_esperado_codigo=item.get("produto_esperado_codigo"),
                    deve_conter=list(item.get("deve_conter", [])),
                    nao_deve_conter=list(item.get("nao_deve_conter", [])),
                    exige_citacao=bool(item.get("exige_citacao", False)),
                )
            )
        except KeyError as exc:
            raise ValueError(
                f"Caso na posicao {i} sem campo obrigatorio: {exc}"
            ) from exc

    return casos


def _verificar_criterios(caso: CasoTeste, resposta: RespostaBot) -> list[Falha]:
    """Funcao pura: aplica cada criterio do caso, retorna todas as falhas."""
    falhas: list[Falha] = []

    if resposta.intencao != caso.intent_esperado:
        falhas.append(
            Falha(
                "intent",
                f"esperado {caso.intent_esperado!r}, obtido {resposta.intencao!r}",
            )
        )

    if caso.produto_esperado_codigo is not None:
        if caso.produto_esperado_codigo not in resposta.produtos_referenciados:
            falhas.append(
                Falha(
                    "produto",
                    f"codigo {caso.produto_esperado_codigo} nao esta em "
                    f"produtos_referenciados={resposta.produtos_referenciados}",
                )
            )

    texto_lower = resposta.texto.lower()

    for substring in caso.deve_conter:
        if substring.lower() not in texto_lower:
            falhas.append(
                Falha("deve_conter", f"substring {substring!r} nao encontrada")
            )

    for substring in caso.nao_deve_conter:
        if substring.lower() in texto_lower:
            falhas.append(
                Falha(
                    "nao_deve_conter",
                    f"substring proibida {substring!r} encontrada",
                )
            )

    if caso.exige_citacao:
        tem_citacao = bool(
            resposta.produtos_referenciados or resposta.documentos_enviados
        )
        if not tem_citacao:
            falhas.append(
                Falha(
                    "exige_citacao",
                    "nem produtos_referenciados nem documentos_enviados preenchidos",
                )
            )

    return falhas


def _executar_caso(caso: CasoTeste) -> ResultadoCaso:
    """Executa um caso: cronometra, chama motor, aplica criterios."""
    inicio = time.monotonic()
    resposta = responder(caso.pergunta, canal="eval")
    latencia_ms = int((time.monotonic() - inicio) * 1000)

    falhas = _verificar_criterios(caso, resposta)
    return ResultadoCaso(
        caso=caso,
        intencao_obtida=resposta.intencao,
        latencia_ms=latencia_ms,
        falhas=falhas,
    )


def _percentil(valores: list[int], p: float) -> int:
    """Percentil por interpolacao linear. Retorna 0 para lista vazia."""
    if not valores:
        return 0
    ordenados = sorted(valores)
    k = (len(ordenados) - 1) * p
    f = int(k)
    c = min(f + 1, len(ordenados) - 1)
    if f == c:
        return ordenados[f]
    return int(ordenados[f] + (ordenados[c] - ordenados[f]) * (k - f))


def _contar_falhas_por_criterio(
    resultados: list[ResultadoCaso], criterio: str
) -> int:
    """Casos que falharam ao menos uma vez no criterio dado."""
    return sum(1 for r in resultados if any(f.criterio == criterio for f in r.falhas))


def _imprimir_relatorio(resultados: list[ResultadoCaso]) -> int:
    """Formata resultados no stdout e devolve exit code."""
    total = len(resultados)
    passaram = sum(1 for r in resultados if r.passou)

    print()
    print("=" * 72)
    print("EVAL DO MOTOR DE RESPOSTA")
    print("=" * 72)

    for r in resultados:
        marcador = "OK  " if r.passou else "FAIL"
        print(
            f"\n[{marcador}] {r.caso.id} ({r.latencia_ms}ms) "
            f"— intent: {r.intencao_obtida}"
        )
        print(f"       Q: {r.caso.pergunta}")
        for falha in r.falhas:
            print(f"       - {falha.criterio}: {falha.detalhe}")

    # Aplicabilidade por criterio (denominador correto de cada taxa).
    n_com_produto = sum(
        1 for r in resultados if r.caso.produto_esperado_codigo is not None
    )
    n_com_deve = sum(1 for r in resultados if r.caso.deve_conter)
    n_com_nao_deve = sum(1 for r in resultados if r.caso.nao_deve_conter)
    n_com_citacao = sum(1 for r in resultados if r.caso.exige_citacao)

    def _taxa(criterio: str, aplicavel: int) -> str:
        if aplicavel == 0:
            return "n/a"
        falharam = _contar_falhas_por_criterio(resultados, criterio)
        return f"{aplicavel - falharam}/{aplicavel}"

    latencias = [r.latencia_ms for r in resultados]

    intents_contagem: dict[str, int] = {}
    for r in resultados:
        intents_contagem[r.intencao_obtida] = (
            intents_contagem.get(r.intencao_obtida, 0) + 1
        )

    print()
    print("-" * 72)
    print("METRICAS POR CRITERIO")
    print("-" * 72)
    print(f"  intent           : {_taxa('intent', total)}")
    print(f"  produto          : {_taxa('produto', n_com_produto)}")
    print(f"  deve_conter      : {_taxa('deve_conter', n_com_deve)}")
    print(f"  nao_deve_conter  : {_taxa('nao_deve_conter', n_com_nao_deve)}")
    print(f"  exige_citacao    : {_taxa('exige_citacao', n_com_citacao)}")

    print()
    print("-" * 72)
    print("LATENCIA")
    print("-" * 72)
    print(f"  p50 : {_percentil(latencias, 0.50)}ms")
    print(f"  p95 : {_percentil(latencias, 0.95)}ms")
    if latencias:
        print(f"  media: {int(statistics.mean(latencias))}ms")

    print()
    print("-" * 72)
    print("INTENTS DETECTADAS (pergunta_aberta chama LLM — proxy de custo)")
    print("-" * 72)
    for intencao, n in sorted(intents_contagem.items()):
        print(f"  {intencao:25s} : {n}")

    print()
    print("=" * 72)
    veredito = "PASSOU" if passaram == total else "FALHOU"
    print(f"VEREDITO: {veredito} — {passaram}/{total} casos")
    print("=" * 72)
    print()

    return 0 if passaram == total else 1


def main() -> int:
    print(f"Carregando golden set: {GOLDEN_PATH}")
    casos = _carregar_golden(GOLDEN_PATH)
    print(f"Casos a executar: {len(casos)}")
    print("Executando (chamadas de LLM podem levar alguns segundos)...\n")

    resultados: list[ResultadoCaso] = []
    for i, caso in enumerate(casos, 1):
        print(f"  [{i}/{len(casos)}] {caso.id}: {caso.pergunta[:60]}...")
        resultados.append(_executar_caso(caso))

    return _imprimir_relatorio(resultados)


if __name__ == "__main__":
    sys.exit(main())