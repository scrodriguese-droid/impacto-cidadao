"""Arrecadação por tributo e fluxo “de onde vêm e para onde vão” os tributos.

* Federal — Resultado do Tesouro Nacional (Governo Central, R$ milhões, caixa), últimos 12 meses.
* Estadual e municipal — RREO Anexo 03 (SICONFI): arrecadação própria dos últimos 12 meses, LÍQUIDA de
  transferências constitucionais e do Fundeb (é a base de cálculo da receita corrente líquida), não a bruta.

Nada é estimado: o que não tem série aberta nessas fontes fica de fora (a tela mostra “—”).
"""
from __future__ import annotations

import logging
from typing import Any

import ciclo
import economia
from fontes import ErroFonte

log = logging.getLogger("impacto.tributos")

# identificador do tributo (usado em assets/tributos.json) → código da linha na tabela do Tesouro
RTN = {"II": "1.1.01", "IPI": "1.1.02", "IRPF": "1.1.03.1", "IRPJ": "1.1.03.2", "IRRF": "1.1.03.3", "IOF": "1.1.04", "COFINS": "1.1.05",
       "PIS_PASEP": "1.1.06", "CSLL": "1.1.07", "CIDE": "1.1.09", "RGPS_URBANO": "1.3.1", "RGPS_RURAL": "1.3.2", "CPSS": "1.4.3", "SAL_EDUC": "1.4.6"}

SICONFI = {
    "estado": {"ICMS": "ICMSLiquidoExcetoTransferenciasEFUNDEB", "IPVA": "IPVALiquidoExcetoTransferenciasEFUNDEB",
               "ITCMD": "ITCDLiquidoExcetoTransferenciasEFUNDEB", "IRRF_ESTADUAL": "IRRFLiquidoExcetoTransferenciasEFUNDEB"},
    "municipio": {"IPTU": "IPTULiquidoExcetoTransferenciasEFUNDEB", "ISS": "ISSLiquidoExcetoTransferenciasEFUNDEB",
                  "ITBI": "ITBILiquidoExcetoTransferenciasEFUNDEB", "IRRF_MUNICIPAL": "IRRFLiquidoExcetoTransferenciasEFUNDEB"},
}
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _rotulo_periodo(fim: str) -> str:
    ano, mes = int(fim[:4]), int(fim[5:7])
    idx = ano * 12 + mes - 1 - 11
    return f"{MESES[idx % 12]}/{idx // 12} a {MESES[mes - 1]}/{ano}"


def _rtn_12m(rtn: dict, codigo: str, fim: str) -> float | None:
    item = rtn["codigos"].get(codigo)
    if not item:
        return None
    v = ciclo._soma_12(item["serie"], fim)
    return None if v is None else round(v * 1e6, 2)


def federal() -> dict:
    rtn = ciclo._rtn()
    fim = sorted(rtn["receita_total"])[-1]
    valores = {k: _rtn_12m(rtn, c, fim) for k, c in RTN.items()}
    return {"periodo": _rotulo_periodo(fim), "fim": fim, "valores": {k: v for k, v in valores.items() if v is not None},
            "fonte": {"nome": "Tesouro Nacional — Resultado do Tesouro Nacional (Governo Central, caixa)", "url": rtn["pagina"], "atualizado": rtn["atualizado"]},
            "nota": "Valores em reais correntes, apurados pelo regime de caixa. O RGPS é a arrecadação líquida da Previdência (não separa empregado, empregador e autônomo)."}


def _subnacional(ente: int, esfera: str) -> dict | None:
    per = economia._periodo_mais_recente(ente)
    if not per:
        return None
    ano, bim = per
    a3 = economia._rreo(ente, ano, bim, "RREO-Anexo 03")
    valores = {}
    for k, conta in SICONFI[esfera].items():
        v = economia._valor(a3, conta, "TOTAL (ÚLTIMOS 12 MESES)")
        if v is not None:
            valores[k] = round(float(v), 2)
    instituicao = next((r.get("instituicao") for r in a3 if r.get("instituicao")), None)
    return {"instituicao": instituicao, "periodo": f"12 meses até o {bim}º bimestre de {ano}", "valores": valores,
            "fonte": {"nome": "SICONFI — RREO Anexo 03 (arrecadação própria, líquida de repasses e do Fundeb)", "url": "https://siconfi.tesouro.gov.br/"}}


def arrecadacao(municipio: int | None) -> dict:
    saida: dict[str, Any] = {"federal": None, "estadual": None, "municipal": None, "erros": []}
    try:
        saida["federal"] = federal()
    except Exception as exc:  # noqa: BLE001
        log.warning("arrecadação federal indisponível: %s", exc)
        saida["erros"].append("Arrecadação federal (Tesouro Nacional) indisponível no momento.")
    if municipio:
        for chave, ente, esfera in (("estadual", municipio // 100000, "estado"), ("municipal", municipio, "municipio")):
            try:
                saida[chave] = _subnacional(ente, esfera)
            except Exception as exc:  # noqa: BLE001
                log.warning("arrecadação %s indisponível: %s", chave, exc)
                saida["erros"].append(f"Arrecadação {chave} (SICONFI) indisponível no momento.")
    return saida


# --------------------------------------------------------------------------- #
# Fluxo federal: de onde vêm e para onde vão
# --------------------------------------------------------------------------- #
GRUPOS_RECEITA = {
    "renda": ("Renda, lucro e ganho de capital", "#e11d48"),
    "consumo": ("Consumo (bens e serviços)", "#0284c7"),
    "folha": ("Salários e folha", "#0d9488"),
    "financeiras": ("Transações financeiras", "#1e3a8a"),
    "outras": ("Outras receitas (royalties, dividendos, concessões…)", "#a16207"),
}


def fluxo_federal() -> dict:
    rtn = ciclo._rtn()
    fim = sorted(rtn["receita_total"])[-1]

    def v(codigo: str) -> float:
        x = _rtn_12m(rtn, codigo, fim)
        if x is None:
            raise ErroFonte(f"Linha {codigo} ausente no Resultado do Tesouro")
        return x

    esquerda = [
        ("Imposto de Renda", v("1.1.03"), "renda"), ("CSLL", v("1.1.07"), "renda"),
        ("Cofins", v("1.1.05"), "consumo"), ("PIS/Pasep", v("1.1.06"), "consumo"), ("IPI", v("1.1.02"), "consumo"),
        ("Imposto de Importação", v("1.1.01"), "consumo"), ("Cide-Combustíveis", v("1.1.09"), "consumo"), ("IOF", v("1.1.04"), "financeiras"),
        ("Contribuições previdenciárias (RGPS)", v("1.3"), "folha"), ("Outras receitas administradas pela RFB", v("1.1.10"), "outras"),
        ("Receitas não administradas pela RFB", v("1.4"), "outras"),
    ]
    receita_total = v("1")
    despesas = [
        ("Transferências a estados e municípios", v("2"), "Repartição de receita (FPE, FPM, royalties e outros)"),
        ("Benefícios previdenciários (INSS)", v("4.1"), ""), ("Pessoal e encargos sociais", v("4.2"), ""),
        ("Outras despesas obrigatórias", v("4.3"), "Abono e seguro-desemprego, BPC, Fundeb (complementação), precatórios…"),
        ("Demais despesas do Poder Executivo", v("4.4"), "Inclui saúde, educação, investimentos e despesas discricionárias"),
    ]
    despesa_total = sum(d[1] for d in despesas)
    soma_esq = sum(e[1] for e in esquerda)
    saldo = round(despesa_total - soma_esq, 2)   # > 0: gastos acima da arrecadação, cobertos por dívida
    return {
        "periodo": _rotulo_periodo(fim), "unidade": "R$ (reais correntes, 12 meses)",
        "esquerda": [{"nome": n, "valor": val, "grupo": g, "cor": GRUPOS_RECEITA[g][1]} for n, val, g in esquerda],
        "grupos": {k: {"nome": n, "cor": c} for k, (n, c) in GRUPOS_RECEITA.items()},
        "centro": {"nome": "Governo Central (União)", "receita_total": receita_total, "soma_origens": round(soma_esq, 2)},
        "direita": [{"nome": n, "valor": val, "detalhe": d} for n, val, d in despesas],
        "saldo": {"valor": saldo, "rotulo": "Déficit primário (coberto por dívida)" if saldo > 0 else "Superávit primário"},
        "fonte": {"nome": "Tesouro Nacional — Resultado do Tesouro Nacional (Governo Central, caixa)", "url": rtn["pagina"]},
        "nota": "Governo Central: Tesouro Nacional, Previdência Social e Banco Central. Não inclui os juros da dívida (resultado primário) nem as empresas estatais.",
    }
