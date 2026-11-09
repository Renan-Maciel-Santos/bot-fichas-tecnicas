"""REPL de conversa com o motor de resposta.

Ferramenta de desenvolvimento — permite testar o motor no terminal antes
do WhatsApp, exercitando classifier, buscas, redator e citacao ponta a
ponta. Nao usa Evolution API nem n8n; conversa direto com src.query.responder.

Uso:
    python -m scripts.chat
    python -m scripts.chat --verbose      # mostra logs INFO das buscas
    python -m scripts.chat --canal wa     # simula chamada vinda do WhatsApp
"""

import argparse
import logging

from src.query.responder import responder

_COMANDOS_SAIDA = {"sair", "exit", "quit"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REPL local do motor de resposta.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Habilita logs INFO (buscas, classifier, retrieval).",
    )
    parser.add_argument(
        "--canal",
        default="terminal",
        help="Canal a registrar em consultas.canal (default: 'terminal').",
    )
    parser.add_argument(
        "--vendedor",
        default="vendedor-teste",
        help="Identificador do vendedor a registrar em consultas.vendedor_id.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    print("Bot de fichas tecnicas — digite 'sair' para encerrar.\n")

    while True:
        try:
            pergunta = input("Voce: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if pergunta.lower() in _COMANDOS_SAIDA:
            break
        if not pergunta:
            continue

        resposta = responder(
            pergunta,
            vendedor_id=args.vendedor,
            canal=args.canal,
        )

        print(f"\nBot: {resposta.texto}\n")

        # Metadados uteis para debug e calibracao
        metadados = [f"intencao: {resposta.intencao}"]
        if resposta.fallback_roteamento:
            metadados.append("FALLBACK")
        if resposta.produtos_referenciados:
            metadados.append(f"produtos: {resposta.produtos_referenciados}")
        if resposta.documentos_enviados:
            metadados.append(f"docs: {len(resposta.documentos_enviados)}")
        print(f"     [{' | '.join(metadados)}]\n")


if __name__ == "__main__":
    main()