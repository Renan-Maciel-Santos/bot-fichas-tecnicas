# src/query

Roteamento da pergunta do vendedor → busca correta → resposta com citação.

## Fluxo (Fase 3)

```
Pergunta do vendedor
       ↓
[classifier.py] Detecta intenção
       ↓
       ├─→ "busca_caracteristica"  → SQL na tabela propriedades_produto
       ├─→ "fetch_documento"       → SELECT na tabela documentos
       ├─→ "pergunta_aberta"       → busca vetorial nos chunks
       └─→ "fora_escopo"           → resposta padrão "não tenho essa info"
       ↓
[responder.py] Monta resposta com citação + anexa PDF
       ↓
       └─→ retorna texto + lista de arquivos
```

## Arquivos planejados

### `classifier.py`
Recebe a pergunta, classifica em uma das 4 intenções. Prompt curto, modelo barato.

### `search_structured.py`
Para perguntas de característica ("produtos com peróxido"). Constrói SQL e busca em `propriedades_produto`.

### `search_vector.py`
Para perguntas abertas. Embedda a pergunta, busca top-K chunks similares.

### `responder.py`
Monta resposta natural com citação ao produto/ficha. Sempre inclui disclaimer e link para o PDF original.

## Regras de ouro (que vão para os prompts)

1. **Nunca responder sem fonte recuperada.** Se a busca não retornar nada, dizer "não encontrei essa informação na base".
2. **Sempre citar.** "Conforme ficha técnica do produto X (código Y)..."
3. **Sempre anexar PDF** quando o produto referenciado tiver um.
4. **Disclaimer fixo no rodapé:** "Ferramenta interna. Confira informações críticas na ficha anexa antes de repassar ao cliente."
5. **Não inventar dado.** Se um campo não está na base, dizer "essa informação não consta na ficha disponível".
