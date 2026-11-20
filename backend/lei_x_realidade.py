"""A lei contra a realidade: o que a LDO exige e o que as contas do país mostram.

Primeira comparação: a meta de resultado primário da LDO do ano contra o resultado primário do Governo Central.

* A meta vem da própria LDO (Lei 15.321/2025, Anexo de Metas Fiscais), escrita aqui com a fonte. Quando entra uma
  LDO nova, basta acrescentar o ano em `METAS_LDO`; sem o ano, a comparação não é mostrada.
* A realidade vem do Banco Central (SGS 5783: NFSP, resultado primário do Governo Federal e Banco Central, % do PIB,
  acumulado em 12 meses). É o mesmo escopo da meta (Governo Central: Tesouro, Previdência e Banco Central), ao
  contrário da série do setor público consolidado, que inclui estados e municípios. Convenção do BCB: positivo = déficit.

Cuidado com o que a comparação diz: a meta é avaliada no ano-calendário, depois das deduções previstas em lei, e o
acumulado em 12 meses é só um indicador de acompanhamento. O texto da tela avisa isso.
"""
from __future__ import annotations

from typing import Any

import macro

SGS_PRIMARIO_GC_12M = 5783

METAS_LDO: dict[int, dict[str, Any]] = {
    2026: {
        "lei": "Lei 15.321/2025 (LDO 2026)",
        "url": "https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/l15321.htm",
        "escopo": "Governo Central (Tesouro, Previdência e Banco Central)",
        "meta_pib": 0.25, "meta_bi": 34.26,
        "inferior_pib": 0.0, "inferior_bi": 0.0,
        "superior_pib": 0.5, "superior_bi": 68.53,
        "banda_pp": 0.25,
    },
}

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _pct(v: float) -> str:
    return f"{abs(v):.2f}".replace(".", ",") + "%"


def _bi(v: float) -> str:
    return f"R$ {abs(v):.1f}".replace(".", ",") + " bi"


def comparar_meta(meta: dict[str, Any], serie: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Põe a meta da LDO ao lado do último resultado de 12 meses. `serie`: pontos {data, valor} do BCB (positivo = déficit)."""
    if not serie:
        return None
    ultimo = serie[-1]
    resultado = round(-float(ultimo["valor"]), 2)          # aqui: positivo = superávit
    mes = int(ultimo["data"][5:7])
    ref = f"{MESES[mes - 1]} de {ultimo['data'][:4]}"
    inferior, alvo = meta["inferior_pib"], meta["meta_pib"]
    if resultado >= alvo:
        estado, rotulo = "atingida", "Na meta ou acima"
        leitura = "O resultado de 12 meses está na meta ou acima dela."
    elif resultado >= inferior:
        estado, rotulo = "na_faixa", "Dentro da faixa de tolerância"
        leitura = f"O resultado de 12 meses está abaixo da meta, mas dentro da faixa de tolerância (até {_pct(inferior)} do PIB)."
    else:
        estado, rotulo = "abaixo", "Abaixo do limite inferior"
        falta = round(inferior - resultado, 2)
        leitura = (f"O resultado de 12 meses está {_pct(falta)} do PIB abaixo do limite inferior da faixa "
                   f"({_pct(inferior)} do PIB).")
    quem = "superávit" if resultado >= 0 else "déficit"
    return {
        "indicador": "Resultado primário do Governo Central",
        "lei": {"nome": meta["lei"], "url": meta["url"], "escopo": meta["escopo"],
                "meta_pib": alvo, "meta_bi": meta["meta_bi"],
                "inferior_pib": inferior, "inferior_bi": meta["inferior_bi"],
                "superior_pib": meta["superior_pib"], "superior_bi": meta["superior_bi"], "banda_pp": meta["banda_pp"]},
        "realidade": {"resultado_pib": resultado, "quem": quem, "referencia": ref, "mes": ultimo["data"][:7],
                      "fonte": "Banco Central do Brasil, SGS 5783 (NFSP, Governo Federal e Banco Central, acumulado em 12 meses)",
                      "url": "https://dadosabertos.bcb.gov.br/dataset/5783-nfsp-sem-desvalorizacao-cambial--pib---fluxo-acumulado-em-12-meses---resultado-primario---tota"},
        "estado": estado, "rotulo": rotulo, "leitura": leitura,
        "nota": "A meta é avaliada no ano-calendário, depois das deduções previstas em lei. O acumulado de 12 meses é um indicador de "
                "acompanhamento e pode diferir do resultado formal da meta.",
    }


def meta_primaria(ano: int) -> dict[str, Any] | None:
    """Comparação do ano, ou None se a LDO do ano não está cadastrada ou a série não respondeu."""
    meta = METAS_LDO.get(ano)
    if not meta:
        return None
    try:
        serie = macro._memo(("meta_primaria", ano, SGS_PRIMARIO_GC_12M), lambda: macro._bcb(SGS_PRIMARIO_GC_12M, ano - 1))
    except macro.ErroFonte:
        return None
    return comparar_meta(meta, serie)
