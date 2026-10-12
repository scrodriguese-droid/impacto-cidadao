"""Simuladores determinísticos (sem IA) — Imposto de Renda e MEI.

Todos os parâmetros legais ficam em constantes no topo, com a fonte indicada,
para serem atualizados quando a lei mudar. Os cálculos são ESTIMATIVAS para
orientação; não substituem o contracheque, a declaração anual nem um contador.

Fontes dos parâmetros (conferidas em out/2026):
  * Tabela progressiva mensal do IR e desconto simplificado: Receita Federal /
    Lei 15.191/2025 (vigência desde maio/2025).
  * Isenção até R$ 5.000 e redutor até R$ 7.350: Lei 15.270/2025 (vigência 01/01/2026).
  * INSS 2026: Portaria Interministerial MPS/MF de janeiro/2026 (salário mínimo R$ 1.621).
  * DAS-MEI 2026: 5% do salário mínimo + R$ 1 (ICMS) / R$ 5 (ISS) — LC 123/2006, art. 18-A.
"""
from __future__ import annotations

from math import inf
from typing import Literal

# --------------------------------------------------------------------------- #
# Imposto de Renda (pessoa física, retenção mensal na fonte)
# --------------------------------------------------------------------------- #
SALARIO_MINIMO_2026 = 1621.00

# (limite superior da base, alíquota, parcela a deduzir)
TABELA_IR_MENSAL = (
    (2428.80, 0.000, 0.00),
    (2826.65, 0.075, 182.16),
    (3751.05, 0.150, 394.16),
    (4664.68, 0.225, 675.49),
    (inf,     0.275, 908.73),
)
DEDUCAO_POR_DEPENDENTE = 189.59
DESCONTO_SIMPLIFICADO = 607.20         # 25% do limite de isenção da tabela (R$ 2.428,80)

# Lei 15.270/2025 — vale a partir de 2026
ISENCAO_TOTAL_ATE = 5000.00
REDUTOR_ATE = 7350.00
REDUTOR_FIXO = 978.62
REDUTOR_COEF = 0.133145

# (limite superior da faixa de salário, alíquota) — progressivo por faixa
FAIXAS_INSS_2026 = (
    (1621.00, 0.075),
    (2902.84, 0.09),
    (4354.27, 0.12),
    (8475.55, 0.14),
)


def _r2(valor: float) -> float:
    return round(valor + 1e-9, 2)


def calcular_inss(salario: float) -> float:
    """Contribuição do empregado (CLT), progressiva por faixa, limitada ao teto."""
    devido, anterior = 0.0, 0.0
    for limite, aliquota in FAIXAS_INSS_2026:
        if salario <= anterior:
            break
        devido += (min(salario, limite) - anterior) * aliquota
        anterior = limite
    return _r2(devido)


def _imposto_pela_tabela(base: float) -> float:
    if base <= 0:
        return 0.0
    for limite, aliquota, deducao in TABELA_IR_MENSAL:
        if base <= limite:
            return max(0.0, base * aliquota - deducao)
    return 0.0  # inalcançável (último limite é inf)


def _imposto_antes_do_redutor(salario: float, inss: float, dependentes: int, outras_deducoes: float) -> tuple[float, str, float]:
    """Escolhe a base mais favorável: deduções legais ou desconto simplificado."""
    base_legal = salario - inss - dependentes * DEDUCAO_POR_DEPENDENTE - outras_deducoes
    base_simplificada = salario - DESCONTO_SIMPLIFICADO
    imposto_legal = _imposto_pela_tabela(base_legal)
    imposto_simples = _imposto_pela_tabela(base_simplificada)
    if imposto_simples < imposto_legal:
        return imposto_simples, "desconto_simplificado", max(base_simplificada, 0.0)
    return imposto_legal, "deducoes_legais", max(base_legal, 0.0)


def redutor_2026(rendimento_mensal: float, imposto: float) -> float:
    """Redutor da Lei 15.270/2025 aplicado sobre o imposto calculado pela tabela."""
    if rendimento_mensal <= ISENCAO_TOTAL_ATE:
        return imposto  # zera o imposto
    if rendimento_mensal <= REDUTOR_ATE:
        return min(imposto, max(0.0, REDUTOR_FIXO - REDUTOR_COEF * rendimento_mensal))
    return 0.0


def simular_ir(salario_bruto: float, dependentes: int = 0, outras_deducoes: float = 0.0) -> dict:
    """Compara o IRRF mensal pela regra de 2025 e pela regra de 2026 (Lei 15.270/2025).

    O INSS é o mesmo nos dois cenários, de modo que a diferença isola o efeito
    da mudança no Imposto de Renda.
    """
    inss = calcular_inss(salario_bruto)
    antes, metodo, base = _imposto_antes_do_redutor(salario_bruto, inss, dependentes, outras_deducoes)

    imposto_2025 = _r2(antes)
    reducao = redutor_2026(salario_bruto, antes)
    imposto_2026 = _r2(max(0.0, antes - reducao))
    economia = _r2(imposto_2025 - imposto_2026)

    if salario_bruto <= ISENCAO_TOTAL_ATE:
        faixa = "isencao_total"
    elif salario_bruto <= REDUTOR_ATE:
        faixa = "reducao_parcial"
    else:
        faixa = "sem_beneficio"

    return {
        "entrada": {"salario_bruto": _r2(salario_bruto), "dependentes": dependentes,
                    "outras_deducoes": _r2(outras_deducoes)},
        "inss": inss,
        "metodo_de_calculo": metodo,
        "base_de_calculo": _r2(base),
        "regra_2025": {"imposto_mensal": imposto_2025,
                       "liquido_mensal": _r2(salario_bruto - inss - imposto_2025)},
        "regra_2026": {"imposto_mensal": imposto_2026, "redutor_aplicado": _r2(reducao),
                       "liquido_mensal": _r2(salario_bruto - inss - imposto_2026)},
        "economia_mensal": economia,
        "economia_anual_estimada": _r2(economia * 12),
        "faixa": faixa,
        "observacoes": [
            "Estimativa para salário CLT com retenção na fonte; usa a base mais favorável "
            "entre deduções legais e desconto simplificado.",
            "Não considera 13º salário, férias, PLR, aluguéis, dividendos nem a tributação "
            "mínima de altas rendas criada pela mesma lei.",
            "A declaração anual (ajuste) pode produzir valores diferentes da retenção mensal.",
        ],
        "fontes": [
            {"nome": "Lei 15.270/2025 (isenção até R$ 5 mil e redutor até R$ 7,35 mil)",
             "url": "https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/L15270.htm"},
            {"nome": "Receita Federal — tabela do IRPF",
             "url": "https://www.gov.br/receitafederal/pt-br/assuntos/meu-imposto-de-renda"},
        ],
    }


# --------------------------------------------------------------------------- #
# MEI
# --------------------------------------------------------------------------- #
LIMITE_MEI_ANUAL = 81_000.00           # LC 123/2006, art. 18-A (confira no Portal do Simples Nacional)
TOLERANCIA_EXCESSO = 0.20
ICMS_FIXO = 1.00
ISS_FIXO = 5.00
INSS_MEI = round(SALARIO_MINIMO_2026 * 0.05, 2)  # 81,05

Atividade = Literal["comercio_industria", "servicos", "comercio_servicos"]
_ADICIONAL_DAS = {
    "comercio_industria": ICMS_FIXO,
    "servicos": ISS_FIXO,
    "comercio_servicos": ICMS_FIXO + ISS_FIXO,
}


def _situacao_mei(faturamento: float, limite: float) -> dict:
    if faturamento <= limite:
        return {
            "codigo": "dentro_do_limite",
            "titulo": "Dentro do limite do MEI",
            "detalhe": "Seu faturamento cabe no limite; segue como MEI pagando o DAS fixo mensal.",
        }
    if faturamento <= limite * (1 + TOLERANCIA_EXCESSO):
        return {
            "codigo": "excesso_ate_20",
            "titulo": "Excedeu até 20% do limite",
            "detalhe": "Em geral, você paga um imposto complementar sobre o excedente e passa a ser "
                       "desenquadrado do MEI a partir de 1º de janeiro do ano seguinte.",
        }
    return {
        "codigo": "excesso_acima_20",
        "titulo": "Excedeu mais de 20% do limite",
        "detalhe": "Em geral, o desenquadramento vale retroativamente desde 1º de janeiro do ano, "
                   "com recolhimento da diferença de impostos. Converse com um contador.",
    }


def simular_mei(faturamento_anual: float, atividade: Atividade = "servicos",
                meses_de_atividade: int = 12, teto_hipotetico: float | None = None) -> dict:
    """Situação do MEI frente ao limite atual e, opcionalmente, a um teto hipotético.

    `meses_de_atividade` < 12 aplica o limite proporcional (R$ 6.750 por mês) de
    quem abriu o MEI no meio do ano.
    """
    proporcao = meses_de_atividade / 12
    limite = _r2(LIMITE_MEI_ANUAL * proporcao)
    das_mensal = _r2(INSS_MEI + _ADICIONAL_DAS[atividade])

    resultado = {
        "entrada": {"faturamento_anual": _r2(faturamento_anual), "atividade": atividade,
                    "meses_de_atividade": meses_de_atividade},
        "limite_vigente": limite,
        "percentual_do_limite": round(faturamento_anual / limite * 100, 1) if limite else None,
        "situacao_atual": _situacao_mei(faturamento_anual, limite),
        "das_mensal": das_mensal,
        "das_anual": _r2(das_mensal * 12),
        "composicao_das": {"inss": INSS_MEI, "icms": ICMS_FIXO if atividade != "servicos" else 0.0,
                           "iss": ISS_FIXO if atividade != "comercio_industria" else 0.0},
        "cenario_hipotetico": None,
        "observacoes": [
            "O DAS é fixo e independe do faturamento enquanto você estiver dentro do limite.",
            "Valores do DAS de 2026 (5% do salário mínimo de R$ 1.621 + ICMS/ISS).",
            "Cenário hipotético é apenas uma simulação: não representa lei aprovada.",
        ],
        "fontes": [
            {"nome": "Portal do Empreendedor — MEI", "url": "https://www.gov.br/empresas-e-negocios/pt-br/empreendedor"},
            {"nome": "Portal do Simples Nacional", "url": "https://www8.receita.fazenda.gov.br/simplesnacional/"},
        ],
    }
    if teto_hipotetico:
        limite_h = _r2(teto_hipotetico * proporcao)
        resultado["cenario_hipotetico"] = {
            "teto_anual": _r2(teto_hipotetico),
            "limite_considerado": limite_h,
            "situacao": _situacao_mei(faturamento_anual, limite_h),
            "mudou_em_relacao_ao_atual": _situacao_mei(faturamento_anual, limite_h)["codigo"]
                                          != resultado["situacao_atual"]["codigo"],
        }
    return resultado
