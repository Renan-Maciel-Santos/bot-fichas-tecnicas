"""
Dependencias compartilhadas das rotas.

Autenticacao por API key estatica: apenas o n8n chama esta API, entao
uma chave pre-compartilhada e suficiente. JWT seria complexidade sem
beneficio para um unico cliente confiavel.

A chave protege a API de acesso direto; a whitelist de telefone
(aplicada nas rotas) protege de vendedor nao autorizado. Sao camadas
diferentes: a primeira autentica o SISTEMA, a segunda o USUARIO.
"""
import hmac
import logging

from fastapi import Header, HTTPException, status

from src import config

log = logging.getLogger(__name__)


async def verificar_api_key(x_api_key: str = Header(...)) -> None:
    """
    Valida a API key do header contra a configurada no ambiente.

    Raises:
        HTTPException 401: chave ausente ou invalida.
        HTTPException 503: API key nao configurada no servidor.
    """
    if not config.API_KEY:
        log.error("API_KEY nao configurada no ambiente; recusando requisicoes")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servico nao configurado",
        )

    # compare_digest evita timing attack: o tempo de comparacao nao
    # varia com a quantidade de caracteres corretos no inicio da chave.
    if not hmac.compare_digest(x_api_key, config.API_KEY):
        log.warning("Tentativa de acesso com API key invalida")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credencial invalida",
        )