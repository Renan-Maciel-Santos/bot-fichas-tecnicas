"""
Contrato de fonte de documento para o pipeline de ingestao.

Este modulo existe para desacoplar o pipeline de ingestao da origem
fisica do arquivo. Antes deste contrato, `processar_pdf` sabia que
documentos vinham de disco local e construia o identificador inline
(`f"local:{caminho.name}"`), o que impedia reuso pelo sync do Drive.

Aplicacao do Dependency Inversion Principle: o pipeline passa a
depender desta abstracao, e cada origem (disco local, Google Drive)
vira um adapter que produz `DocumentoFonte`.
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Prefixo de namespace para documentos de origem local (amostras de
# desenvolvimento). Garante que `drive_file_id` de fixtures locais nunca
# colida com IDs reais do Google Drive, que sao opacos e sem prefixo.
PREFIXO_LOCAL = "local:"


@dataclass(frozen=True)
class DocumentoFonte:
    """Descritor imutavel de um documento a ser ingerido.

    Agrupa tudo que o pipeline precisa saber sobre a procedencia de um
    PDF, sem conhecer o mecanismo que o trouxe ate o disco.

    Attributes:
        identificador: Chave estavel e unica. Vai para `documentos.drive_file_id`.
            Para origem local: `local:<nome_arquivo>`.
            Para origem Drive: o `fileId` opaco retornado pela API.
        caminho_local: Onde o arquivo esta acessivel AGORA. Para origem
            local e o caminho definitivo; para Drive e um temporario que
            o chamador se responsabiliza por limpar.
        nome_arquivo: Nome de exibicao do arquivo.
        marca_pasta: Pasta de nivel 1 no Drive (a marca). Para origem
            local, um rotulo de amostra (ex.: `local_amostra`).
        modificado_em: Timestamp de modificacao na origem. Alimenta o
            Nivel 1 da deteccao de mudanca do sync (comparacao barata,
            sem download). None quando a origem nao expoe o dado.
        url: Link direto para o documento na origem. Usado nas citacoes
            enviadas ao vendedor pelo WhatsApp. None para origem local.
    """

    identificador: str
    caminho_local: Path
    nome_arquivo: str
    marca_pasta: str
    modificado_em: datetime | None = None
    url: str | None = None

    @classmethod
    def de_arquivo_local(
        cls,
        caminho: Path,
        marca_pasta: str,
    ) -> "DocumentoFonte":
        """Constroi uma fonte a partir de um PDF em disco local.

        Centraliza a convencao de namespacing `local:` em um unico lugar.
        Antes deste factory, o prefixo era construido inline no pipeline,
        espalhando conhecimento de formato de identificador pelo codigo.

        Args:
            caminho: Caminho do PDF em disco.
            marca_pasta: Rotulo de agrupamento (ex.: `local_amostra`).

        Returns:
            Fonte com `modificado_em` e `url` nulos --- disco local nao
            expoe timestamp de origem nem link publico.
        """
        return cls(
            identificador=f"{PREFIXO_LOCAL}{caminho.name}",
            caminho_local=caminho,
            nome_arquivo=caminho.name,
            marca_pasta=marca_pasta,
        )

    @property
    def e_local(self) -> bool:
        """True se a fonte e uma amostra de desenvolvimento em disco.

        Permite ao sync distinguir fixtures locais de documentos reais do
        Drive sem precisar de uma coluna dedicada no schema --- o proprio
        identificador carrega essa informacao.
        """
        return self.identificador.startswith(PREFIXO_LOCAL)