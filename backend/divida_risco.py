"""Dissecando a dívida pública: prazo, custo e indexadores, os três fatores que decidem se ela é sustentável.

Tudo vem de fontes oficiais já usadas pelo projeto: o Tesouro Nacional (estoque da DPF por título e vencimento, e os
fatores de variação, com os juros apropriados) e o Banco Central (Selic, IPCA, resultado primário, dívida bruta).
O que o projeto calcula, e não a fonte, aparece identificado em "metodo". Nada é projeção.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
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

    # As fontes são independentes: buscamos em paralelo (a soma dos tempos vira o da mais lenta, e o Render corta em ~100 s).
    # As séries do BCB ficam numa linha só (sequenciais), porque o BCB bloqueia muitos pedidos ao mesmo tempo.
    def serie_bcb() -> tuple[list | None, dict[str, dict | None]]:
        ipca12 = tentar("IPCA em 12 meses", lambda: macro.serie("ipca12")["pontos"])
        chaves = ("selic", "ipca12", "dbgg_pib", "primario12", "nominal12", "juros12", "dlsp_pib", "dlsp_rs")
        return ipca12, {c: ultimo(c) for c in chaves}

    def ultimo(chave: str) -> dict | None:
        p = tentar(chave, lambda: macro.serie(chave)["pontos"])
        return {"valor": p[-1]["valor"], "data": p[-1]["data"]} if p else None

    with ThreadPoolExecutor(max_workers=5) as pool:
        f_dpf = pool.submit(tentar, "Estoque da Dívida Pública Federal", macro._estoque_dpf)
        f_fat = pool.submit(tentar, "Fatores de variação da dívida", macro._fatores_variacao)
        f_pib = pool.submit(tentar, "PIB (IBGE)", macro._sidra_pib)
        f_lei = pool.submit(tentar, "Resultado dos leilões", macro._leiloes)
        f_bcb = pool.submit(serie_bcb)
        dpf, fat, pib, leiloes = f_dpf.result(), f_fat.result(), f_pib.result(), f_lei.result()
        ipca_serie, ultimos = f_bcb.result()

    if dpf:
        m = dpf["_extra"]["mercado"]
        serie = m["serie"][-12 * ANOS_SERIE:]
        saida["estrutura"] = {
            "referencia": m["referencia"], "total": m["total"], "atualizado": dpf["atualizado"], "url": dpf["url"], "pagina": dpf["pagina"],
            "atual": serie[-1], "um_ano_antes": serie[-13] if len(serie) > 13 else None,
            "calendario": m["calendario"], "serie": serie,
        }

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

    if leiloes:
        saida["leiloes"] = leiloes

    saida["macro"] = {
        "selic": ultimos["selic"], "ipca12": ultimos["ipca12"], "dbgg_pib": ultimos["dbgg_pib"],
        "primario12": ultimos["primario12"], "nominal12": ultimos["nominal12"], "juros12": ultimos["juros12"],
        "pib4": None, "dlsp_pib": ultimos["dlsp_pib"],
    }
    # PIB em reais (12 meses), derivado do saldo da dívida líquida em R$ ÷ em % do PIB, como em macro.py; só vale se as duas séries têm a mesma data
    dl_pct, dl_rs = ultimos["dlsp_pib"], ultimos["dlsp_rs"]
    if dl_pct and dl_rs and dl_pct["data"] == dl_rs["data"] and dl_pct["valor"]:
        saida["macro"]["pib_rs"] = {"valor": dl_rs["valor"] * 1e6 / (dl_pct["valor"] / 100), "data": dl_pct["data"]}
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
