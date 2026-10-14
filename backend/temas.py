"""Termos de cada tema — fonte única para a busca nos planos e para o agrupamento por tema.

Por que existe: sem IA, os atos ainda não têm áreas definidas por um modelo. Esta lista dá uma
classificação APROXIMADA, transparente e auditável (palavras-chave da ementa oficial), igual para
todos os atos. Quando a IA traduz um ato, as áreas dela substituem esta aproximação.

Os termos passam pelo dicionário `pt_busca` do PostgreSQL (português com stemming, sem acento):
"saude" acha "saúde"; "aposentado" acha "aposentadoria". Frases entre aspas casam como expressão.
"""
from __future__ import annotations

# Ordem = ordem de exibição (a mesma da tabela `categorias`). "outros" não tem termos: é o resto.
TERMOS_POR_TEMA: dict[str, list[str]] = {
    "impostos": ["imposto", "tributo", "tributária", "tributário", "isenção", "alíquota",
                 "imposto de renda", "icms", "ipi", "irpf", "reforma tributária"],
    "economia": ["inflação", "juros", "crédito", "fiscal", "contas públicas", "dívida", "câmbio",
                 "orçamento", "orçamentária", "financeiro"],
    "emprego": ["emprego", "trabalho", "salário", "desemprego", "trabalhador", "trabalhista",
                "empreendedor", "microempreendedor"],
    "previdencia": ["previdência", "previdenciário", "aposentadoria", "aposentado", "inss", "pensão", "idoso"],
    "assistencia": ["bolsa família", "assistência social", "pobreza", "fome", "transferência de renda",
                    "bpc", "benefício de prestação continuada"],
    "saude": ["saúde", "sus", "hospital", "médico", "medicamento", "vacina", "plano de saúde", "sanitária"],
    "educacao": ["educação", "escola", "escolar", "creche", "professor", "universidade", "ensino", "estudante"],
    "seguranca": ["segurança pública", "polícia", "crime", "criminoso", "violência", "facção", "presídio",
                  "penal", "contravenção"],
    "habitacao": ["moradia", "habitação", "habitacional", "saneamento", "minha casa", "esgoto"],
    "transporte": ["transporte", "ferrovia", "rodovia", "porto", "aeroporto", "mobilidade",
                   "infraestrutura", "trânsito", "energia"],
    "meio_ambiente": ["ambiente", "ambiental", "clima", "desmatamento", "amazônia", "sustentável",
                      "carbono", "florestal", "resíduos"],
    "agro": ["agropecuária", "agricultura", "agricultor", "agrícola", "rural", "safra", "pecuária",
             "reforma agrária"],
}


def consulta_tsquery(tema: str) -> str:
    """Termos do tema na sintaxe de websearch_to_tsquery: `a or "frase composta" or b`."""
    return " or ".join(f'"{t}"' if " " in t else t for t in TERMOS_POR_TEMA[tema])
