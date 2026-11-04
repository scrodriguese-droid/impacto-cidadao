"""Dissecando a dívida pública: prazo, custo e indexadores, os três fatores que decidem se ela é sustentável.

Tudo vem de fontes oficiais já usadas pelo projeto: o Tesouro Nacional (estoque da DPF por título e vencimento, e os
fatores de variação, com os juros apropriados) e o Banco Central (Selic, IPCA, resultado primário, dívida bruta).
O que o projeto calcula, e não a fonte, aparece identificado em "metodo". Nada é projeção.
"""
from __future__ import annotations

import logging
from typing import Any

import macro

log = logging.getLogger("impacto.divida_risco")

ANOS_SERIE = 10   # janela dos gráficos de evolução


def _custo_efetivo(fat: dict, ipca: dict[str, float], pib4: dict[str, dict]) -> list[dict]:
    """Custo realizado da dívida: juros apropriados em 12 meses ÷ estoque médio do período, em termos nominais e reais.

    É um custo EFETIVO (inclui correção por inflação, juros e o efeito do câmbio na dívida externa), não a taxa de emissão."""
    serie = [x for x in fat["serie"] if x["juros"] is not None and x["estoque_anterior"]]
    saida = []
    for i in range(11, len(serie)):
        janela = serie[i - 11: i + 1]
        juros = sum(x["juros"] for x in janela)
        medio = sum(x["estoque_anterior"] for x in janela) / 12
        nominal = 100 * juros / medio
        m = serie[i]["mes"]
        inflacao = ipca.get(m)
        real = round(100 * ((1 + nominal / 100) / (1 + inflacao / 100) - 1), 2) if inflacao is not None else None
        q = macro._ate(pib4, macro._mes_antes(m, macro.LAG_PIB_MESES))
        saida.append({"mes": m, "nominal": round(nominal, 2), "real": real, "ipca12": inflacao,
                      "pib4": q[1]["valor"] if q else None})
    return saida[-12 * ANOS_SERIE:]


def risco() -> dict[str, Any]:
    saida: dict[str, Any] = {"consultado_em": macro._agora(), "erros": []}

    def tentar(nome: str, f):  # noqa: ANN001
        try:
            return f()
        except Exception as exc:  # noqa: BLE001 — uma fonte fora do ar não derruba o resto
            log.warning("%s indisponível: %s", nome, exc)
            saida["erros"].append(f"{nome} indisponível no momento.")
            return None

    dpf = tentar("Estoque da Dívida Pública Federal", macro._estoque_dpf)
    if dpf:
        m = dpf["_extra"]["mercado"]
        serie = m["serie"][-12 * ANOS_SERIE:]
        saida["estrutura"] = {
            "referencia": m["referencia"], "total": m["total"], "atualizado": dpf["atualizado"], "url": dpf["url"], "pagina": dpf["pagina"],
            "atual": serie[-1], "um_ano_antes": serie[-13] if len(serie) > 13 else None,
            "calendario": m["calendario"], "serie": serie,
        }

    fat = tentar("Fatores de variação da dívida", macro._fatores_variacao)
    ipca_serie = tentar("IPCA em 12 meses", lambda: macro.serie("ipca12")["pontos"])
    pib = tentar("PIB (IBGE)", macro._sidra_pib)
    if fat and ipca_serie and pib:
        ipca = {p["data"][:7]: p["valor"] for p in ipca_serie}
        custo = _custo_efetivo(fat, ipca, pib["acum4"])
        ult = fat["serie"][-1]
        saida["custo"] = {
            "serie": custo, "atual": custo[-1] if custo else None,
            "ultimo_mes": {"mes": ult["mes"], "juros_rs": ult["juros"] * 1e6, "estoque_rs": ult["estoque"] * 1e6},
            "juros_12m_rs": sum(x["juros"] for x in fat["serie"][-12:]) * 1e6,
            "atualizado": fat["atualizado"], "url": fat["url"], "pagina": fat["pagina"],
        }

    leiloes = tentar("Resultado dos leilões", macro._leiloes)
    if leiloes:
        saida["leiloes"] = leiloes

    def ultimo(chave: str) -> dict | None:
        p = tentar(chave, lambda: macro.serie(chave)["pontos"])
        return {"valor": p[-1]["valor"], "data": p[-1]["data"]} if p else None

    saida["macro"] = {
        "selic": ultimo("selic"), "ipca12": ultimo("ipca12"), "dbgg_pib": ultimo("dbgg_pib"),
        "primario12": ultimo("primario12"), "nominal12": ultimo("nominal12"), "juros12": ultimo("juros12"),
        "pib4": None,
    }
    if pib:
        ks = sorted(pib["acum4"])
        saida["macro"]["pib4"] = {"valor": pib["acum4"][ks[-1]]["valor"], "ref": pib["acum4"][ks[-1]]["ref"]}
    selic, ipca = saida["macro"]["selic"], saida["macro"]["ipca12"]
    if selic and ipca:
        saida["macro"]["juro_real_selic"] = round(100 * ((1 + selic["valor"] / 100) / (1 + ipca["valor"] / 100) - 1), 2)

    saida["metodo"] = {
        "mercado": "A estrutura (prazo, vencimentos e indexadores) considera só a dívida em poder do mercado. A carteira do Banco Central não precisa ser refinanciada em leilão.",
        "prazo_medio": "Calculado pelo projeto: média dos prazos até o vencimento de cada título, ponderada pelo valor do estoque. Difere do prazo médio publicado no Relatório Mensal da Dívida, que usa metodologia própria; compare a evolução, não o nível.",
        "custo": "Custo efetivo realizado: juros apropriados nos últimos 12 meses ÷ estoque médio do período. Inclui correção por inflação e câmbio. É diferente da taxa que o governo paga ao emitir um título novo.",
    }
    return saida
