"""Hierarquia das normas jurídicas (Pirâmide de Kelsen) aplicada aos tipos de proposição.

Serve para o cidadão não confundir uma mudança na Constituição com uma lei comum, nem uma lei com um ato administrativo.
É conteúdo fixo, tirado da Constituição Federal (arts. 47, 49, 59, 60, 62, 66 e 69) e dos regimentos do Congresso: nada aqui
vem de IA. Os números de deputados (513) e senadores (81) são os da composição atual.

Duas ideias que o desenho da pirâmide precisa passar:
  * a posição na pirâmide diz qual norma vale mais quando duas se contradizem (a de cima vence) e quão difícil é aprovar;
  * leis ordinárias, medidas provisórias, decretos legislativos e resoluções ficam no MESMO degrau: têm assuntos e ritos
    diferentes, mas nenhum manda no outro. Abaixo deles ficam os atos administrativos, que só regulamentam a lei.
"""
from __future__ import annotations

# Ordem = do mais ao menos rigoroso. `degrau` é a posição na pirâmide (1 = topo).
HIERARQUIA: list[dict] = [
    {
        "id": "constitucional", "ordem": 1, "degrau": 1, "siglas": ["PEC"],
        "rotulo": "CONSTITUCIONAL", "nome": "Proposta de Emenda à Constituição (PEC)", "vira": "Emenda Constitucional",
        "curto": "Muda a própria Constituição",
        "o_que_e": "Altera a Lei Suprema do país. Nenhuma outra norma pode contrariar a Constituição, por isso mudar o texto dela é o passo mais sério.",
        "rigor": "Dois turnos de votação em cada Casa (Câmara e Senado), com no mínimo 3/5 dos votos: 308 deputados e 49 senadores. "
                 "Não passa por sanção ou veto do Presidente.",
        "exemplo": "Mudanças nas regras de aposentadoria, a reforma tributária de base ou limites a direitos previstos na Constituição.",
    },
    {
        "id": "complementar", "ordem": 2, "degrau": 2, "siglas": ["PLP"],
        "rotulo": "LEI COMPLEMENTAR", "nome": "Projeto de Lei Complementar (PLP)", "vira": "Lei Complementar",
        "curto": "Regulamenta o que a Constituição exige",
        "o_que_e": "Regulamenta pontos que a própria Constituição manda tratar por esse caminho mais rigoroso, como finanças públicas e certos impostos.",
        "rigor": "Maioria absoluta: mais da metade de todos os membros de cada Casa (257 deputados e 41 senadores), e não só dos presentes. "
                 "Depois segue para sanção ou veto.",
        "exemplo": "Limites da dívida pública (o arcabouço fiscal é a Lei Complementar 200/2023), regras do IBS e da CBS e finanças de estados e municípios.",
    },
    {
        "id": "ordinaria", "ordem": 3, "degrau": 3, "siglas": ["PL", "PLN", "PLV", "PLS"],
        "rotulo": "LEI ORDINÁRIA", "nome": "Projeto de Lei (PL) ou do Congresso Nacional (PLN)", "vira": "Lei Ordinária",
        "curto": "Lei comum, do dia a dia",
        "o_que_e": "Cria, altera ou revoga as leis comuns do dia a dia. O PLN é o projeto de lei do Congresso Nacional: trata do orçamento e dos créditos, e é analisado em sessão conjunta da Câmara e do Senado.",
        "rigor": "Maioria simples: mais da metade dos presentes, com a maioria dos membros da Casa na sessão. Depois segue para sanção ou veto.",
        "exemplo": "Mudar a lei do Imposto de Renda (Lei 9.250/1995) para alterar a dedução de despesas médicas.",
    },
    {
        "id": "medida_provisoria", "ordem": 4, "degrau": 3, "siglas": ["MPV"],
        "rotulo": "MEDIDA PROVISÓRIA", "nome": "Medida Provisória (MPV)", "vira": "Lei Ordinária, se o Congresso aprovar",
        "curto": "Vale como lei já, por tempo limitado",
        "o_que_e": "Ato do Presidente da República com força de lei desde a publicação, para casos de urgência e relevância. "
                   "Fica no mesmo degrau da lei ordinária, mas é provisória e não pode tratar de temas reservados a lei complementar, entre outros.",
        "rigor": "Vale por 60 dias, prorrogáveis por mais 60 (até 120). Se o Congresso não a aprovar nesse prazo, perde a validade.",
        "exemplo": "Isenção emergencial ou socorro financeiro a regiões em calamidade.",
    },
    {
        "id": "ato_congresso", "ordem": 5, "degrau": 3, "siglas": ["PDL", "PDC", "PDN", "PRC", "PRN", "PRS", "PDS"],
        "rotulo": "ATO DO CONGRESSO", "nome": "Decreto Legislativo (PDL) e Resolução (PRC)", "vira": "Decreto Legislativo ou Resolução",
        "curto": "Decisão do Congresso, sem sanção",
        "o_que_e": "Atos de competência exclusiva do Congresso ou de uma das Casas. Não vão ao Presidente: o próprio Legislativo promulga. "
                   "Não é o “decreto” do Executivo, que fica abaixo da lei.",
        "rigor": "Votação só no Legislativo, sem sanção nem veto; em regra, maioria simples.",
        "exemplo": "Sustar um decreto do Executivo que passou do que a lei permite, aprovar tratados internacionais ou mudar regras internas da Câmara e do Senado.",
    },
]

# Atos que NÃO são proposições legislativas, mas que o cidadão encontra e confunde com elas: ficam abaixo da lei.
ADMINISTRATIVOS: dict = {
    "rotulo": "ATO ADMINISTRATIVO",
    "nome": "Decretos do Executivo, portarias e instruções normativas",
    "o_que_e": "Atos do próprio Executivo (Presidente, ministérios, agências) que detalham como a lei será cumprida. Não passam pelo Congresso.",
    "limite": "Só regulamentam a lei: se contrariarem uma lei, perdem a força, e o Congresso pode sustar o decreto que passar do limite.",
}

NOTA = ("A pirâmide mostrada é a federal. Nos estados e municípios a lógica é a mesma, com a Constituição Estadual e a Lei Orgânica "
        "no lugar da Constituição Federal.")

_POR_SIGLA = {s: n for n in HIERARQUIA for s in n["siglas"]}


def classificar(sigla_tipo: str | None) -> str | None:
    """Id do degrau/tipo de norma de uma sigla de proposição (PEC, PL, MPV…); None se a sigla não for reconhecida."""
    n = _POR_SIGLA.get((sigla_tipo or "").strip().upper())
    return n["id"] if n else None


def siglas_da_norma(norma_id: str) -> list[str]:
    """Siglas de proposição que pertencem a um tipo de norma (para filtrar a listagem)."""
    for n in HIERARQUIA:
        if n["id"] == norma_id:
            return list(n["siglas"])
    return []


IDS = tuple(n["id"] for n in HIERARQUIA)


def catalogo() -> dict:
    return {"hierarquia": HIERARQUIA, "administrativos": ADMINISTRATIVOS, "nota": NOTA,
            "fonte": {"nome": "Constituição Federal, arts. 47, 49, 59, 60, 62, 66 e 69", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm"}}
