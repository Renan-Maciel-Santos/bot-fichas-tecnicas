"""
Rotas HTTP da API do bot.

Camada fina: valida entrada, aplica politicas de acesso e delega ao
motor (`src.query.responder`). Nenhuma regra de negocio vive aqui.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import verificar_api_key
from src.api.repository import (
    buscar_vendedor_autorizado,
    liberar_mensagem,
    registrar_mensagem,
)
from src.api.schemas import PerguntaRequest, RespostaResponse
from src.query.responder import responder

log = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/perguntar",
    response_model=RespostaResponse,
    dependencies=[Depends(verificar_api_key)],
    summary="Responde uma pergunta de vendedor sobre os produtos",
)
def perguntar(payload: PerguntaRequest) -> RespostaResponse:
    """
    `def`, nao `async def`: o repositorio (psycopg2) e o motor (OpenAI
    sincrono) sao bloqueantes. Numa corrotina, uma pergunta esperando o
    LLM travaria o event loop inteiro — o /health e as mensagens dos
    outros vendedores. Como funcao comum o FastAPI a roda em thread.

    Fluxo, na ordem em que as barreiras ficam mais caras:

    1. Autorizacao do remetente  — consulta local, barata
    2. Dedupe por message_id     — insert local, barato
    3. Motor de resposta         — LLM + embeddings, caro

    A ordem importa: numero nao autorizado ou webhook reenviado nunca
    chegam a consumir cota da OpenAI.
    """
    vendedor = buscar_vendedor_autorizado(payload.remetente)
    if vendedor is None:
        log.warning("Acesso negado para remetente nao autorizado")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Remetente nao autorizado",
        )

    if not registrar_mensagem(payload.message_id, payload.remetente):
        log.info("Webhook reenviado ignorado (message_id ja processado)")
        return RespostaResponse(
            resposta="",
            intencao="duplicada",
            duplicada=True,
        )

    resultado = responder(
        pergunta=payload.pergunta,
        vendedor_id=payload.remetente,
        canal="whatsapp",
    )

    # O motor nao levanta excecao: falha vira intencao "erro". Nesse
    # caso o dedupe precisa ser desfeito, senao o reenvio da Evolution
    # e descartado como duplicata e o vendedor nunca recebe resposta.
    if resultado.intencao == "erro":
        liberar_mensagem(payload.message_id)

    return RespostaResponse(
        resposta=resultado.texto,
        intencao=resultado.intencao,
        documentos=resultado.documentos_enviados,
        precisa_esclarecimento=resultado.precisa_esclarecimento,
    )