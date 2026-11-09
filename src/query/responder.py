"""
Motor de resposta do assistente (Fase 4).

Orquestra: classificacao da pergunta -> busca adequada -> redacao da
resposta -> log em `consultas`.

Regras de ouro (design original, src/query/README.md):
1. Nunca responder sem fonte recuperada — busca vazia = "nao encontrei".
2. Sempre citar a fonte (produto/documento, com tipo quando conhecido).
3. Anexar documentos quando existirem (url_drive; pendente da ingestao
   real do Drive na amostra atual).
4. Disclaimer fixo no rodape.
5. Nao inventar dado.

Decisoes de desenho:
- So a intencao `pergunta_aberta` usa LLM para redigir — e mesmo assim
  APENAS com os chunks recuperados como contexto (grounding por
  construcao). Intencoes estruturadas respondem por template Python:
  deterministico, sem risco de alucinacao, custo zero.
- Ambiguidade de produto -> pergunta de esclarecimento com candidatos
  (nunca adivinhar marca em dado tecnico de seguranca quimica), e a
  escolha do vendedor ("2") reexecuta a intencao original via
  src/query/contexto.py. Sem esse estado a desambiguacao seria um
  beco sem saida: o motor pede um numero que nao saberia interpretar.
- Pedido de documento honra o TIPO pedido (FT/FDS/laudo). Entregar uma
  FDS a quem pediu ficha tecnica e pior do que dizer "nao tenho".
- Formato de citacao vive em src/query/citation.py (fonte unica).
- O log em `consultas` nunca derruba a resposta (best-effort).
"""

import logging
import time
from dataclasses import dataclass, field

from src.config import LLM_MODEL_RESPONSE
from src.db import get_connection
from src.openai_client import get_client
from src.query.citation import formatar_fonte, nome_do_tipo
from src.query.classifier import Classificacao, classificar
from src.query.contexto import (
    CandidatoPendente,
    ContextoPendente,
    buscar_contexto,
    interpretar_selecao,
    limpar_contexto,
    salvar_contexto,
)
from src.query.search_structured import (
    DocumentoProduto,
    ProdutoCandidato,
    documentos_de_produto,
    produtos_por_propriedade,
    propriedades_de_produto,
    resolver_produto,
)
from src.query.search_vector import buscar_chunks

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "_Ferramenta interna. Confira informações críticas na ficha original "
    "antes de repassar ao cliente._"
)

MSG_FORA_ESCOPO = (
    "Consigo ajudar com informações técnicas dos produtos do catálogo "
    "(fichas técnicas, FISPQ, propriedades). Essa pergunta está fora do "
    "que tenho na base."
)

MSG_NAO_ENCONTREI = "Não encontrei essa informação na base de documentos."

MSG_PRODUTO_NAO_ENCONTRADO = (
    "Não encontrei esse produto no catálogo. Pode confirmar o nome ou o código?"
)

# Criterio de desambiguacao: top-1 resolve direto se nao ha 2o candidato
# ou se o score do top-1 e >= FATOR_DESTAQUE x o score do top-2.
FATOR_DESTAQUE = 1.3

PROMPT_REDATOR = f"""Voce e um assistente interno para vendedores de uma revenda de produtos de limpeza e descartaveis.

Responda a pergunta do vendedor usando EXCLUSIVAMENTE os trechos de documentos fornecidos abaixo. Regras:
1. Se os trechos nao contem a resposta, diga exatamente: "{MSG_NAO_ENCONTREI}"
2. NUNCA use conhecimento externo aos trechos, mesmo que voce saiba a resposta.
3. Cite a fonte usando EXATAMENTE o rotulo que aparece entre colchetes no inicio de cada trecho. Exemplo: "Conforme a FDS de Aromatizante Aromar, ...". Nao invente nome nem tipo de documento.
4. Seja direto e pratico - o vendedor esta atendendo um cliente.
5. Se a pergunta envolve seguranca (misturas, acidentes, riscos), transcreva a orientacao do documento fielmente, sem resumir instrucoes de seguranca."""

# Quantos candidatos entram na pergunta de esclarecimento. Lista maior
# nao cabe bem numa mensagem de WhatsApp e piora a escolha.
MAX_CANDIDATOS_ESCLARECIMENTO = 3


@dataclass
class RespostaBot:
    """Resposta completa do motor, pronta para qualquer canal."""

    texto: str
    intencao: str
    produtos_referenciados: list[int] = field(default_factory=list)
    documentos_enviados: list[str] = field(default_factory=list)
    precisa_esclarecimento: bool = False
    fallback_roteamento: bool = False
    erro: str | None = None
    # Candidatos oferecidos quando precisa_esclarecimento. O motor os
    # devolve para que `responder` grave a pendencia sem ter que
    # reconstruir a lista que ja foi montada aqui.
    candidatos_pendentes: list[CandidatoPendente] = field(default_factory=list)


def _formatar_candidatos(candidatos: list[ProdutoCandidato]) -> str:
    linhas = [
        f"  {i}. {c.descricao} ({c.marca}) — codigo {c.codigo}"
        for i, c in enumerate(candidatos[:MAX_CANDIDATOS_ESCLARECIMENTO], 1)
    ]
    return (
        "Encontrei mais de um produto parecido. Qual deles?\n"
        + "\n".join(linhas)
        + "\n\nResponda com o numero ou o codigo do produto."
    )


def _resolver_ou_esclarecer(
    termo: str,
) -> tuple[ProdutoCandidato | None, RespostaBot | None]:
    """
    Resolve o termo em um unico produto ou produz a pergunta de
    esclarecimento.

    Returns:
        (produto, None) quando resolvido;
        (None, resposta_de_esclarecimento) quando ambiguo ou ausente.
    """
    candidatos = resolver_produto(termo)

    if not candidatos:
        return None, RespostaBot(
            texto=f"{MSG_PRODUTO_NAO_ENCONTRADO}\n\n{DISCLAIMER}",
            intencao="",
            precisa_esclarecimento=True,
        )

    if len(candidatos) == 1 or candidatos[0].score >= FATOR_DESTAQUE * candidatos[1].score:
        return candidatos[0], None

    oferecidos = candidatos[:MAX_CANDIDATOS_ESCLARECIMENTO]
    return None, RespostaBot(
        texto=f"{_formatar_candidatos(candidatos)}\n\n{DISCLAIMER}",
        intencao="",
        precisa_esclarecimento=True,
        produtos_referenciados=[c.codigo for c in oferecidos],
        # A ordem aqui e a MESMA da mensagem: e o que da sentido ao "2".
        candidatos_pendentes=[
            CandidatoPendente(
                ordem=i,
                codigo=c.codigo,
                descricao=c.descricao,
                marca=c.marca,
            )
            for i, c in enumerate(oferecidos, 1)
        ],
    )


def _responder_busca_caracteristica(cls: Classificacao) -> RespostaBot:
    campo = cls.campo or "ingrediente_ativo"
    try:
        resultados = produtos_por_propriedade(campo, cls.valor_busca)
    except ValueError as exc:
        logger.warning("Campo invalido na busca: %s", exc)
        resultados = []

    if not resultados:
        return RespostaBot(
            texto=f"{MSG_NAO_ENCONTREI}\n\n{DISCLAIMER}",
            intencao=cls.intencao,
        )

    linhas = []
    for r in resultados:
        base = (
            f"- {r.produto_descricao} ({r.marca}) — codigo {r.produto_codigo}: "
            f"{r.campo} = {r.valor}"
        )
        if r.documento_fonte:
            fonte = formatar_fonte(r.documento_fonte, r.tipo_documento)
            base += f" _(fonte: {fonte})_"
        linhas.append(base)

    texto = (
        f"Produtos com '{cls.valor_busca}' em {campo}:\n"
        + "\n".join(linhas)
        + f"\n\n{DISCLAIMER}"
    )
    return RespostaBot(
        texto=texto,
        intencao=cls.intencao,
        produtos_referenciados=sorted({r.produto_codigo for r in resultados}),
    )


def _responder_propriedades(cls: Classificacao) -> RespostaBot:
    produto, esclarecimento = _resolver_ou_esclarecer(cls.produto_termo or "")
    if esclarecimento:
        esclarecimento.intencao = cls.intencao
        return esclarecimento

    return _propriedades_do_produto(produto, cls)


def _propriedades_do_produto(
    produto: ProdutoCandidato,
    cls: Classificacao,
) -> RespostaBot:
    """
    Monta a resposta de propriedades para um produto JA resolvido.

    Separado de `_responder_propriedades` porque este e o ponto de
    reentrada quando o vendedor escolhe um item da desambiguacao: nesse
    caminho o produto ja veio da escolha e nao ha termo para resolver.
    """
    props = propriedades_de_produto(produto.codigo)
    if cls.campo:
        props = [p for p in props if p.campo == cls.campo]

    if not props:
        alvo = f" ({cls.campo})" if cls.campo else ""
        return RespostaBot(
            texto=(
                f"Nao ha propriedades{alvo} extraidas para "
                f"{produto.descricao} na base.\n\n{DISCLAIMER}"
            ),
            intencao=cls.intencao,
            produtos_referenciados=[produto.codigo],
        )

    # Fontes unicas por (nome, tipo), preservando a ordem de primeira
    # aparicao (mais confiavel primeiro, gracas ao ORDER BY confianca).
    fontes_vistas: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for p in props:
        if not p.documento_fonte:
            continue
        chave = (p.documento_fonte, p.tipo_documento)
        if chave not in seen:
            seen.add(chave)
            fontes_vistas.append(chave)

    linhas = [
        f"- {p.campo}: {p.valor}" + (f" {p.unidade}" if p.unidade else "")
        for p in props
    ]
    if fontes_vistas:
        rotulos = [formatar_fonte(nome, tipo) for nome, tipo in fontes_vistas]
        citacao = f"\n\nConforme {', '.join(rotulos)}."
    else:
        citacao = ""

    texto = (
        f"{produto.descricao} ({produto.marca}) — codigo {produto.codigo}:\n"
        + "\n".join(linhas)
        + citacao
        + f"\n\n{DISCLAIMER}"
    )
    return RespostaBot(
        texto=texto,
        intencao=cls.intencao,
        produtos_referenciados=[produto.codigo],
    )


def _deduplicar_documentos(docs: list[DocumentoProduto]) -> list[DocumentoProduto]:
    """
    Colapsa documentos que sao o mesmo arquivo repetido na base.

    A ingestao do Drive gerou duplicatas (mesmo arquivo em mais de uma
    pasta de marca), e parte delas ficou sem `url_drive`. Sem isto o
    vendedor recebe a mesma ficha duas vezes, uma delas sem link — o
    que parece defeito do bot, nao da base.

    Criterio de sobrevivencia: entre duplicatas, vence a que TEM link,
    porque e a unica acionavel no WhatsApp. Empate resolve pela ordem
    de chegada (ja ordenada por nome no SQL), mantendo a saida estavel.

    A deduplicacao aqui e cosmetica e nao substitui a limpeza da base —
    ver o backlog de qualidade de dados.
    """
    melhor: dict[tuple[str, str | None], DocumentoProduto] = {}
    ordem: list[tuple[str, str | None]] = []

    for d in docs:
        chave = (formatar_fonte(d.nome_arquivo, d.tipo).casefold(), d.tipo)
        anterior = melhor.get(chave)
        if anterior is None:
            melhor[chave] = d
            ordem.append(chave)
        elif not anterior.url_drive and d.url_drive:
            melhor[chave] = d

    return [melhor[k] for k in ordem]


def _linhas_documentos(docs: list[DocumentoProduto]) -> str:
    linhas = []
    for d in docs:
        rotulo = formatar_fonte(d.nome_arquivo, d.tipo)
        linha = f"- {rotulo}"
        if d.url_drive:
            linha += f" — {d.url_drive}"
        else:
            # Sem link nao ha o que anexar. Dizer isso e melhor do que
            # uma linha truncada que parece um erro de formatacao.
            linha += " — _(sem link no Drive; peça ao escritório)_"
        linhas.append(linha)
    return "\n".join(linhas)


def _responder_fetch_documento(cls: Classificacao) -> RespostaBot:
    produto, esclarecimento = _resolver_ou_esclarecer(cls.produto_termo or "")
    if esclarecimento:
        esclarecimento.intencao = cls.intencao
        return esclarecimento

    return _documentos_do_produto(produto, cls)


def _documentos_do_produto(
    produto: ProdutoCandidato,
    cls: Classificacao,
) -> RespostaBot:
    """
    Monta a resposta de documentos para um produto JA resolvido.

    Honra o tipo pedido (`cls.tipo_documento`): pedir "a ficha tecnica"
    e receber a FDS e pior do que receber um "nao tenho" — sao
    documentos com finalidades diferentes, e o vendedor pode repassar o
    errado ao cliente. Quando o tipo pedido nao existe, dizemos isso e
    listamos o que ha, em vez de entregar outra coisa em silencio.
    """
    docs = _deduplicar_documentos(documentos_de_produto(produto.codigo))

    if not docs:
        return RespostaBot(
            texto=(
                f"Nao ha documentos vinculados a {produto.descricao} "
                f"na base.\n\n{DISCLAIMER}"
            ),
            intencao=cls.intencao,
            produtos_referenciados=[produto.codigo],
        )

    if cls.tipo_documento:
        do_tipo = [d for d in docs if d.tipo == cls.tipo_documento]
        if not do_tipo:
            texto = (
                f"Nao tenho {nome_do_tipo(cls.tipo_documento)} de "
                f"{produto.descricao} na base. O que existe para esse produto:\n"
                + _linhas_documentos(docs)
                + f"\n\n{DISCLAIMER}"
            )
            return RespostaBot(
                texto=texto,
                intencao=cls.intencao,
                produtos_referenciados=[produto.codigo],
                # Sem documentos_enviados: nada do tipo pedido foi entregue,
                # e o campo alimenta o anexo do n8n.
            )
        docs = do_tipo

    texto = (
        f"Documentos de {produto.descricao} ({produto.marca}):\n"
        + _linhas_documentos(docs)
        + f"\n\n{DISCLAIMER}"
    )
    return RespostaBot(
        texto=texto,
        intencao=cls.intencao,
        produtos_referenciados=[produto.codigo],
        documentos_enviados=[d.documento_id for d in docs],
    )


def _responder_pergunta_aberta(cls: Classificacao, pergunta: str) -> RespostaBot:
    filtro_codigo = None
    produtos_ref: list[int] = []

    if cls.produto_termo:
        candidatos = resolver_produto(cls.produto_termo)
        # Para pergunta aberta, o filtro so entra se a resolucao for
        # inequivoca; caso contrario busca-se na base inteira (mais
        # tolerante que travar a conversa com esclarecimento).
        if len(candidatos) == 1 or (
            len(candidatos) > 1
            and candidatos[0].score >= FATOR_DESTAQUE * candidatos[1].score
        ):
            filtro_codigo = candidatos[0].codigo
            produtos_ref = [filtro_codigo]

    chunks = buscar_chunks(pergunta, filtro_produto_codigo=filtro_codigo)
    if not chunks and filtro_codigo is not None:
        # Fallback: nada nos chunks do produto -> tenta a base inteira.
        chunks = buscar_chunks(pergunta)

    if not chunks:
        return RespostaBot(
            texto=f"{MSG_NAO_ENCONTREI}\n\n{DISCLAIMER}",
            intencao=cls.intencao,
            produtos_referenciados=produtos_ref,
        )

    # O rotulo entre colchetes vira o contrato de citacao com o LLM
    # (ver regra 3 do PROMPT_REDATOR).
    contexto = "\n\n---\n\n".join(
        f"[{formatar_fonte(c.documento_fonte, c.tipo_documento)}]\n{c.texto}"
        for c in chunks
    )
    client = get_client()
    response = client.chat.completions.create(
        model=LLM_MODEL_RESPONSE,
        temperature=0,
        messages=[
            {"role": "system", "content": PROMPT_REDATOR},
            {
                "role": "user",
                "content": f"Trechos dos documentos:\n\n{contexto}\n\nPergunta do vendedor: {pergunta}",
            },
        ],
    )
    redacao = (response.choices[0].message.content or "").strip()

    # Se o LLM concluiu que os trechos nao respondem a pergunta (mesmo
    # tendo havido chunks acima do limiar), a resposta e "nao encontrei"
    # e NAO faz sentido reportar documentos como fonte. Sem isso,
    # documentos_enviados ficaria preenchido contradizendo o texto.
    if redacao.rstrip(".") == MSG_NAO_ENCONTREI.rstrip("."):
        return RespostaBot(
            texto=f"{MSG_NAO_ENCONTREI}\n\n{DISCLAIMER}",
            intencao=cls.intencao,
            produtos_referenciados=produtos_ref,
        )

    docs_usados = sorted({c.documento_id for c in chunks})
    return RespostaBot(
        texto=f"{redacao}\n\n{DISCLAIMER}",
        intencao=cls.intencao,
        produtos_referenciados=produtos_ref,
        documentos_enviados=docs_usados,
    )


SQL_LOG_CONSULTA = """
    INSERT INTO consultas
        (vendedor_id, canal, pergunta, resposta, intencao_detectada,
         produtos_referenciados, documentos_enviados, tempo_resposta_ms, erro)
    VALUES
        (%(vendedor_id)s, %(canal)s, %(pergunta)s, %(resposta)s, %(intencao)s,
         %(produtos)s, %(documentos)s::uuid[], %(tempo_ms)s, %(erro)s)
"""


def _log_consulta(
    pergunta: str,
    resposta: RespostaBot,
    tempo_ms: int,
    vendedor_id: str | None,
    canal: str,
) -> None:
    """Grava a consulta para auditoria. Best-effort: nunca derruba a resposta."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    SQL_LOG_CONSULTA,
                    {
                        "vendedor_id": vendedor_id,
                        "canal": canal,
                        "pergunta": pergunta,
                        "resposta": resposta.texto,
                        "intencao": resposta.intencao
                        + (" [fallback]" if resposta.fallback_roteamento else ""),
                        "produtos": resposta.produtos_referenciados or None,
                        "documentos": resposta.documentos_enviados or None,
                        "tempo_ms": tempo_ms,
                        "erro": resposta.erro,
                    },
                )
    except Exception:
        logger.exception("Falha ao gravar log em consultas (resposta nao afetada)")


def _despachar(cls: Classificacao, pergunta: str) -> RespostaBot:
    """Roteia a classificacao para a rota de resposta correspondente."""
    if cls.intencao == "fora_escopo":
        return RespostaBot(
            texto=f"{MSG_FORA_ESCOPO}\n\n{DISCLAIMER}",
            intencao=cls.intencao,
        )
    if cls.intencao == "busca_caracteristica":
        return _responder_busca_caracteristica(cls)
    if cls.intencao == "propriedades_produto":
        return _responder_propriedades(cls)
    if cls.intencao == "fetch_documento":
        return _responder_fetch_documento(cls)
    return _responder_pergunta_aberta(cls, pergunta)  # inclui fallback


def _responder_escolha(
    pergunta: str,
    pendente: ContextoPendente | None,
) -> RespostaBot | None:
    """
    Trata a mensagem como escolha de uma desambiguacao pendente.

    Returns:
        A resposta a intencao ORIGINAL ja com o produto escolhido, ou
        None quando a mensagem nao e uma escolha — caso em que o fluxo
        normal de classificacao assume.
    """
    if pendente is None:
        return None

    codigo = interpretar_selecao(pergunta, pendente.candidatos)
    if codigo is None:
        return None

    candidatos = resolver_produto(str(codigo))
    if not candidatos:
        # Produto saiu do catalogo entre a pergunta e a escolha.
        # Raro, mas nao pode virar excecao no meio da conversa.
        logger.warning("Produto escolhido %s nao esta mais disponivel", codigo)
        return None

    produto = candidatos[0]
    # Reconstroi a classificacao original com o produto ja resolvido.
    cls = Classificacao(
        intencao=pendente.intencao,
        produto_termo=str(codigo),
        campo=pendente.campo,
        valor_busca=None,
        tipo_documento=pendente.tipo_documento,
    )

    if pendente.intencao == "propriedades_produto":
        return _propriedades_do_produto(produto, cls)
    if pendente.intencao == "fetch_documento":
        return _documentos_do_produto(produto, cls)

    # Nenhuma outra intencao gera desambiguacao hoje. Se passar a
    # gerar, o caminho seguro e tratar como pergunta nova.
    logger.warning(
        "Pendencia com intencao inesperada (%s); tratando como pergunta nova",
        pendente.intencao,
    )
    return None


def responder(
    pergunta: str,
    vendedor_id: str | None = None,
    canal: str = "terminal",
) -> RespostaBot:
    """
    Ponto de entrada do motor: pergunta -> resposta completa.

    Duas passagens possiveis:
    1. A mensagem e a escolha de uma desambiguacao pendente ("2") — o
       motor reexecuta a intencao original com o produto escolhido.
    2. Caso contrario, classifica e responde normalmente.

    A pendencia so existe quando ha `vendedor_id` (canal identificado).
    No terminal, sem identificacao, o comportamento e o de sempre.

    Nunca levanta excecao: erro interno vira resposta de indisponibilidade
    com o erro registrado no log.
    """
    inicio = time.monotonic()
    pergunta = (pergunta or "").strip()

    try:
        pendente = buscar_contexto(vendedor_id) if vendedor_id else None
        resposta = _responder_escolha(pergunta, pendente)

        if resposta is not None:
            # Escolha atendida: a pendencia cumpriu seu papel.
            if vendedor_id:
                limpar_contexto(vendedor_id)
        else:
            cls = classificar(pergunta)
            resposta = _despachar(cls, pergunta)
            resposta.fallback_roteamento = cls.fallback

            if vendedor_id:
                if resposta.candidatos_pendentes:
                    salvar_contexto(
                        vendedor_id=vendedor_id,
                        intencao=cls.intencao,
                        campo=cls.campo,
                        tipo_documento=cls.tipo_documento,
                        pergunta_original=pergunta,
                        candidatos=resposta.candidatos_pendentes,
                    )
                elif pendente is not None:
                    # Havia pendencia e o vendedor mudou de assunto:
                    # a lista antiga nao pode capturar a proxima mensagem.
                    limpar_contexto(vendedor_id)

    except Exception as exc:  # noqa: BLE001 — o motor nunca explode para o canal
        logger.exception("Erro no motor de resposta")
        resposta = RespostaBot(
            texto=(
                "Tive um problema tecnico ao processar sua pergunta. "
                f"Tente novamente em instantes.\n\n{DISCLAIMER}"
            ),
            intencao="erro",
            erro=str(exc),
        )

    tempo_ms = int((time.monotonic() - inicio) * 1000)
    _log_consulta(pergunta, resposta, tempo_ms, vendedor_id, canal)
    return resposta