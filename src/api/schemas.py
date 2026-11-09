"""
Contratos de entrada e saida da API HTTP.

Pydantic valida o payload antes de qualquer processamento: entrada
malformada e rejeitada com 422 sem tocar no banco nem na OpenAI.
"""
from pydantic import BaseModel, Field


class PerguntaRequest(BaseModel):
    """Payload enviado pelo n8n a cada mensagem recebida no WhatsApp."""

    pergunta: str = Field(
        min_length=1,
        max_length=1000,
        description="Texto da mensagem enviada pelo vendedor.",
    )
    remetente: str = Field(
        min_length=10,
        max_length=20,
        pattern=r"^\d+$",
        description="Telefone em E.164 sem '+' (ex.: 5511999998888).",
    )
    message_id: str = Field(
        min_length=1,
        max_length=100,
        description="ID da mensagem na Evolution API, usado para dedupe.",
    )


class RespostaResponse(BaseModel):
    """Resposta do motor, pronta para o n8n enviar via sendText."""

    resposta: str
    intencao: str
    documentos: list[str] = []
    precisa_esclarecimento: bool = False
    duplicada: bool = Field(
        default=False,
        description="True quando o webhook era reenvio e nada foi reprocessado.",
    )