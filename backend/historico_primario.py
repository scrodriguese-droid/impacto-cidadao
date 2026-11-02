"""Histórico do resultado primário do setor público (Banco Central, SGS 5793), mês a mês, com os governos de cada época.

A série oficial vem como "necessidade de financiamento": número positivo = déficit. Aqui o sinal é invertido para o leitor:
acima de zero = superávit (receitas maiores que as despesas, sem contar juros), abaixo de zero = déficit. Os valores são os
oficiais, acumulados em 12 meses e em % do PIB; só o sinal muda.

Os governos são só uma régua de datas (quem estava na Presidência em cada mês). O resultado depende da economia, das regras herdadas
e do Congresso: a tela não atribui o número a uma pessoa nem ranqueia governos."""
from __future__ import annotations

import macro

CODIGO_SGS = 5793
INICIO = 2001   # a série começa em dez/2001

# Quem ocupava a Presidência em cada mês (fato público). `inicio`/`fim` em AAAA-MM; fim None = em curso.
GOVERNOS = [
    {"id": "fhc2", "nome": "Fernando Henrique Cardoso", "rotulo": "FHC", "inicio": "1999-01", "fim": "2002-12"},
    {"id": "lula1", "nome": "Lula (1º mandato)", "rotulo": "Lula I", "inicio": "2003-01", "fim": "2006-12"},
    {"id": "lula2", "nome": "Lula (2º mandato)", "rotulo": "Lula II", "inicio": "2007-01", "fim": "2010-12"},
    {"id": "dilma1", "nome": "Dilma Rousseff (1º mandato)", "rotulo": "Dilma I", "inicio": "2011-01", "fim": "2014-12"},
    {"id": "dilma2", "nome": "Dilma Rousseff (2º mandato)", "rotulo": "Dilma II", "inicio": "2015-01", "fim": "2016-04",
     "nota": "Afastada em 12/05/2016; o impeachment foi concluído em 31/08/2016."},
    {"id": "temer", "nome": "Michel Temer", "rotulo": "Temer", "inicio": "2016-05", "fim": "2018-12",
     "nota": "Assumiu como interino em 12/05/2016 e como titular em 31/08/2016."},
    {"id": "bolsonaro", "nome": "Jair Bolsonaro", "rotulo": "Bolsonaro", "inicio": "2019-01", "fim": "2022-12"},
    {"id": "lula3", "nome": "Lula (3º mandato)", "rotulo": "Lula III", "inicio": "2023-01", "fim": None},
]


def governo_do_mes(mes: str) -> dict | None:
    for g in GOVERNOS:
        if g["inicio"] <= mes and (g["fim"] is None or mes <= g["fim"]):
            return g
    return None


def montar(sgs: list[dict]) -> dict:
    """Recebe a série do SGS ({data, valor}, valor positivo = déficit) e devolve pontos, anos e resumo por governo."""
    pontos = [{"mes": p["data"][:7], "valor": round(-p["valor"], 2)} for p in sgs]
    if len(pontos) < 24:
        raise ValueError("Série do resultado primário curta demais.")
    ultimo = pontos[-1]
    por_mes = {p["mes"]: p["valor"] for p in pontos}

    # um valor por ano: o acumulado em 12 meses de dezembro é o resultado do ano; o ano em curso usa o último mês disponível
    anos = []
    for ano in sorted({p["mes"][:4] for p in pontos}):
        mes = f"{ano}-12" if f"{ano}-12" in por_mes else max(m for m in por_mes if m[:4] == ano)
        g = governo_do_mes(mes)
        anos.append({"ano": int(ano), "mes": mes, "valor": por_mes[mes], "parcial": not mes.endswith("-12"),
                     "governo": g["rotulo"] if g else None})

    resumo_gov = []
    for g in GOVERNOS:
        dentro = [p for p in pontos if g["inicio"] <= p["mes"] and (g["fim"] is None or p["mes"] <= g["fim"])]
        if len(dentro) < 2:
            continue
        melhor, pior = max(dentro, key=lambda p: p["valor"]), min(dentro, key=lambda p: p["valor"])
        resumo_gov.append({**g, "meses": len(dentro), "primeiro": dentro[0], "ultimo": dentro[-1], "melhor": melhor, "pior": pior,
                           "media": round(sum(p["valor"] for p in dentro) / len(dentro), 2),
                           "parcial": g["inicio"] < pontos[0]["mes"] or g["fim"] is None or g["fim"] > ultimo["mes"]})
    melhor, pior = max(pontos, key=lambda p: p["valor"]), min(pontos, key=lambda p: p["valor"])
    rot, fmt = macro._rotulo_mes, macro._fmt

    def sinal(v: float) -> str:
        return f"superávit de {fmt(abs(v), 2)}% do PIB" if v >= 0 else f"déficit de {fmt(abs(v), 2)}% do PIB"
    return {
        "pontos": pontos, "anos": anos, "governos": resumo_gov, "ultimo_mes": ultimo["mes"],
        "resumo": (f"Em {rot(ultimo['mes'])}, o resultado primário acumulado em 12 meses foi de {sinal(ultimo['valor'])}. Desde {rot(pontos[0]['mes'])}, "
                   f"o melhor momento foi {sinal(melhor['valor'])} ({rot(melhor['mes'])}) e o pior, {sinal(pior['valor'])} ({rot(pior['mes'])})."),
        "melhor": melhor, "pior": pior,
    }


def historico() -> dict:
    def produzir() -> dict:
        base = montar(macro._bcb(CODIGO_SGS, INICIO))
        return {
            "consultado_em": macro._agora(), **base,
            "notas": [
                "Resultado primário = receitas menos despesas, sem contar os juros da dívida. Acima de zero é superávit; abaixo, déficit.",
                "Valores acumulados em 12 meses, em % do PIB, do setor público consolidado, como divulga o Banco Central. O Banco Central publica o número com o sinal "
                "de “necessidade de financiamento” (positivo = déficit); aqui o sinal foi invertido para leitura, sem outro ajuste.",
                "Os governos são só uma régua do tempo, com os meses em que cada um esteve na Presidência. O resultado depende também da economia, das regras herdadas "
                "e do Congresso: o gráfico não atribui o número a uma pessoa nem classifica governos.",
                "A média de cada governo é a média dos valores mensais (cada um já acumula 12 meses), só dos meses em que a série existe.",
            ],
            "fonte": {"nome": "Banco Central do Brasil — SGS, série 5793 (resultado primário do setor público consolidado, 12 meses, % do PIB)",
                      "url": "https://dadosabertos.bcb.gov.br/"},
        }
    return macro._memo(("historico_primario",), produzir)
