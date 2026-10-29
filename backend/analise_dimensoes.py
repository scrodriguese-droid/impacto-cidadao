"""Análise das duas dimensões da economia: o ciclo (curto e médio prazo) e a tendência estrutural (longo prazo).

Os números vêm de fontes oficiais (IBGE, Banco Central, Banco Mundial). As faixas e regras que transformam números em
"fase do ciclo" ou "compatível com…" são critério EDITORIAL do projeto, didático: não substituem a datação oficial dos
ciclos (feita pelo CODACE/FGV IBRE) nem um diagnóstico acadêmico. Toda classificação mostra os testes que a sustentam."""
from __future__ import annotations

import logging
from typing import Any

import macro

log = logging.getLogger("impacto.dimensoes")

# --------------------------------------------------------------------------- #
# 1. Ciclo econômico (curto e médio prazo)
# --------------------------------------------------------------------------- #
FASES = [
    {"id": "expansao", "rotulo": "Recuperação / expansão",
     "texto": "O PIB cresce trimestre após trimestre, o desemprego cai, o comércio vende mais e o consumo das famílias sobe."},
    {"id": "pico", "rotulo": "Pico / superaquecimento",
     "texto": "A economia opera perto do pleno emprego. A demanda elevada começa a pressionar a inflação para cima, e o Banco Central sobe os juros."},
    {"id": "desaceleracao", "rotulo": "Desaceleração",
     "texto": "O crédito mais caro esfria o consumo e os investimentos, e o ritmo de crescimento perde força."},
    {"id": "recessao", "rotulo": "Fundo / recessão",
     "texto": "A produção cai de forma contínua (dois trimestres seguidos de PIB negativo são a recessão técnica), o desemprego sobe e empresas fecham."},
]
MARGEM_PLENO_EMPREGO = 1.0     # p.p. acima da menor taxa de desemprego dos últimos 10 anos para ainda contar como "perto do pleno emprego"
ALTA_SELIC = 1.0               # p.p. de alta da Selic em 12 meses que conta como aperto monetário
JURO_REAL_RESTRITIVO = 5.0     # p.p.: juro real (Selic − IPCA 12 meses) a partir do qual o crédito é considerado caro
TOL_DESEMPREGO = 0.5           # p.p. contra 12 meses antes
MIN_MESES_JANELA = 36          # meses de desemprego necessários para falar em "mínima dos últimos 10 anos"
HISTORICO_DESDE = "2015-12"


def _dados_ciclo() -> dict[str, Any]:
    pib = macro._sidra_pib()
    return {"tri": {k: v["valor"] for k, v in pib["tri"].items()},
            "acum4": {k: v["valor"] for k, v in pib["acum4"].items()},
            "desemp": {p["data"][:7]: p["valor"] for p in macro._sidra_desemprego()},
            "selic": macro.serie("selic")["pontos"],
            "ipca": {p["data"][:7]: p["valor"] for p in macro.serie("ipca12")["pontos"]}}


def _t(texto: str, ok: bool | None, valor: str = "") -> dict:
    return {"texto": texto, "ok": ok, "valor": valor}


def _pc(v: float, d: int = 1) -> str:
    return f"{macro._fmt(v, d)}%"


def fase_em(mes: str, d: dict[str, Any]) -> dict | None:
    """Fase do ciclo mais compatível com os dados já divulgados até `mes` (AAAA-MM). None se faltar dado."""
    q = macro._ate(d["tri"], macro._mes_antes(mes, macro.LAG_PIB_MESES))
    dm = macro._ate(d["desemp"], macro._mes_antes(mes, macro.LAG_DESEMPREGO_MESES))
    ip = macro._ate(d["ipca"], mes)
    selic = macro._selic_em(d["selic"], mes)
    if not (q and dm and ip and selic is not None):
        return None
    qk, tri = q
    ant = d["tri"].get(macro._mes_antes(qk, 3))
    ant4 = [d["tri"].get(macro._mes_antes(qk, 3 * i)) for i in range(1, 5)]
    if ant is None or any(v is None for v in ant4):
        return None
    media4 = sum(ant4) / 4
    dk, desemp = dm
    desemp_antes = d["desemp"].get(macro._mes_antes(dk, 12))
    janela = [v for k, v in d["desemp"].items() if macro._mes_antes(dk, 120) <= k <= dk]
    minimo = min(janela) if len(janela) >= MIN_MESES_JANELA else None
    selic_antes = macro._selic_em(d["selic"], macro._mes_antes(mes, 12))
    alta_selic = None if selic_antes is None else round(selic - selic_antes, 2)
    juro_real = round(selic - ip[1], 2)
    meta = macro._meta_inflacao(mes)
    teto = None if meta is None else round(meta[0] + meta[1], 2)
    var_desemp = None if desemp_antes is None else round(desemp - desemp_antes, 2)

    perto_pleno = None if minimo is None else desemp <= minimo + MARGEM_PLENO_EMPREGO
    inflacao_acima = None if teto is None else ip[1] > teto
    aperto = None if alta_selic is None else alta_selic >= ALTA_SELIC
    e = lambda a, b: bool(a) or bool(b)  # noqa: E731
    testes = {
        "recessao": [_t("PIB do último trimestre caiu", tri < 0, f"{macro._fmt(tri, 2)}% no trimestre"),
                     _t("PIB do trimestre anterior também caiu (recessão técnica)", ant < 0, f"{macro._fmt(ant, 2)}%")],
        "pico": [_t("PIB não está caindo", tri >= 0, f"{macro._fmt(tri, 2)}% no trimestre"),
                 _t("Desemprego perto da menor taxa dos últimos 10 anos", perto_pleno,
                    f"{_pc(desemp)} (mínima: {_pc(minimo) if minimo is not None else '—'})"),
                 _t("Inflação acima do teto da meta ou Selic em alta de 12 meses", e(inflacao_acima, aperto) if (inflacao_acima is not None or aperto is not None) else None,
                    f"IPCA {_pc(ip[1], 2)} (teto {_pc(teto) if teto is not None else '—'}); Selic {'+' if (alta_selic or 0) >= 0 else '−'}{macro._fmt(abs(alta_selic or 0), 2)} p.p. em 12 meses")],
        "desaceleracao": [_t("PIB não está caindo", tri >= 0, f"{macro._fmt(tri, 2)}% no trimestre"),
                          _t("Ritmo do trimestre menor que a média dos 4 anteriores", tri < media4, f"{macro._fmt(tri, 2)}% contra média de {macro._fmt(media4, 2)}%"),
                          _t("Crédito caro: juro real alto ou Selic em alta", e(juro_real >= JURO_REAL_RESTRITIVO, aperto),
                             f"juro real de {macro._fmt(juro_real, 1)} p.p.")],
        "expansao": [_t("PIB cresceu nos dois últimos trimestres", tri >= 0 and ant >= 0, f"{macro._fmt(ant, 2)}% e {macro._fmt(tri, 2)}%"),
                     _t("Desemprego não subiu em 12 meses", None if var_desemp is None else var_desemp <= TOL_DESEMPREGO,
                        f"{_pc(desemp)} ({'+' if (var_desemp or 0) >= 0 else '−'}{macro._fmt(abs(var_desemp or 0), 1)} p.p.)")],
    }
    escolhida = next((f for f in ("recessao", "pico", "desaceleracao", "expansao") if all(t["ok"] for t in testes[f])), None)
    return {"mes": mes, "fase": escolhida, "testes": testes, "trimestre": qk, "desemprego_mes": dk,
            "indicadores": {"pib_tri": tri, "pib_tri_anterior": ant, "pib_media4": round(media4, 2), "desemprego": desemp, "desemprego_minimo_10a": minimo,
                            "desemprego_var_12m": var_desemp, "ipca12": ip[1], "teto_meta": teto, "selic": selic, "selic_var_12m": alta_selic,
                            "juro_real": juro_real, "pib_acum4": d["acum4"].get(qk)}}


def ciclo() -> dict:
    d = _dados_ciclo()
    ultimo_mes = max(d["ipca"])
    atual = fase_em(ultimo_mes, d)
    hist = []
    m = HISTORICO_DESDE
    while m <= ultimo_mes:
        f = fase_em(m, d)
        if f:
            hist.append({"mes": m, "fase": f["fase"]})
        m = macro._mes_antes(m, -3)
    return {"fases": FASES, "atual": atual, "historico": hist,
            "criterio": ("Regras do projeto, em ordem: recessão (2 trimestres de PIB negativo), pico (desemprego perto da mínima de 10 anos e pressão de "
                         "preços ou juros), desaceleração (ritmo menor que o dos 4 trimestres anteriores com crédito caro) e expansão (PIB crescendo com o "
                         "desemprego sem subir). Quando nenhuma regra fecha, o ciclo aparece como “sinais mistos”. É uma leitura didática: a datação oficial dos "
                         "ciclos brasileiros é do CODACE (FGV IBRE)."),
            "fonte": {"nome": "IBGE (Contas Nacionais Trimestrais e PNAD Contínua) e Banco Central (Selic e IPCA)", "url": "https://sidra.ibge.gov.br/"}}


# --------------------------------------------------------------------------- #
# 2. Tendência estrutural (longo prazo) — Banco Mundial
# --------------------------------------------------------------------------- #
PAISES = {"BRA": "Brasil", "ARG": "Argentina", "CHL": "Chile", "KOR": "Coreia do Sul", "VEN": "Venezuela", "USA": "Estados Unidos"}
IND_RENDA_PPC = "NY.GDP.PCAP.PP.KD"   # PIB per capita, paridade do poder de compra (US$ constantes)
IND_RENDA_REAL = "NY.GDP.PCAP.KD"     # PIB per capita real (US$ constantes de 2015)
IND_INDUSTRIA = "NV.IND.MANF.ZS"      # indústria de transformação, % do PIB
IND_IDOSOS = "SP.POP.65UP.TO.ZS"      # população com 65 anos ou mais, %
INICIO_INDUSTRIA = 1980               # antes disso a série do Banco Mundial para o Brasil tem quebras (zeros e saltos)
LIM_RENDA_RELATIVA_ALTA = 50.0        # % da renda per capita dos EUA abaixo do qual o país não é tratado como "alcançado"
ESTAGNACAO_PCT_ANO = 1.0              # crescimento real per capita médio, em % ao ano, abaixo do qual a década é "estagnada"
VARIACAO_RELATIVA_ESTAVEL = 5.0       # p.p. (renda relativa aos EUA) dentro dos quais, em 30 anos, o país "ficou no mesmo lugar"
QUEDA_RELATIVA_DECLINIO = 10.0        # p.p. de queda da renda relativa aos EUA em 30 anos para falar em declínio
QUEDA_INDUSTRIA_PRECOCE = 1 / 3       # fração do pico da indústria perdida para falar em desindustrialização relevante


def _banco_mundial(indicador: str, paises: str = ";".join(PAISES), inicio: int = 1960) -> dict[str, dict[int, float]]:
    def produzir() -> dict[str, dict[int, float]]:
        d = macro._http.json(f"https://api.worldbank.org/v2/country/{paises}/indicator/{indicador}",
                             {"format": "json", "per_page": 4000, "date": f"{inicio}:{macro.date.today().year}"})
        saida: dict[str, dict[int, float]] = {}
        for x in d[1] or []:
            if x.get("value") is not None:
                saida.setdefault(x["countryiso3code"], {})[int(x["date"])] = float(x["value"])
        return saida
    return macro._memo(("bm", indicador, paises, inicio), produzir)


def _cagr(s: dict[int, float], anos: int) -> tuple[float, int, int] | None:
    fim = max(s)
    ini = fim - anos
    if ini not in s or s[ini] <= 0:
        return None
    return (((s[fim] / s[ini]) ** (1 / anos) - 1) * 100, ini, fim)


def _relativa(s: dict[int, float], eua: dict[int, float]) -> dict[int, float]:
    return {a: s[a] / eua[a] * 100 for a in s if a in eua and eua[a] > 0}


def _variacao_desde(s: dict[int, float], ano: int) -> str:
    v = (s[max(s)] / s[ano] - 1) * 100
    return f"{'alta' if v >= 0 else 'queda'} de {macro._fmt(abs(v), 0)}%"


def _item(nome: str, valor: str, nota: str = "") -> dict:
    return {"nome": nome, "valor": valor, "nota": nota}


def estrutura() -> dict:
    ppc = _banco_mundial(IND_RENDA_PPC)
    real = _banco_mundial(IND_RENDA_REAL)
    ind = _banco_mundial(IND_INDUSTRIA)
    idosos = _banco_mundial(IND_IDOSOS, "BRA")
    eua = ppc["USA"]
    rel = {c: _relativa(ppc[c], eua) for c in PAISES if c in ppc and c != "USA"}
    br = rel["BRA"]
    fim = max(br)
    ini30 = fim - 30
    var30 = br[fim] - br[ini30] if ini30 in br else None
    c10, c20 = _cagr(real["BRA"], 10), _cagr(real["BRA"], 20)
    manuf = {a: v for a, v in ind["BRA"].items() if a >= INICIO_INDUSTRIA}
    pico = max(manuf, key=manuf.get)
    atual = max(manuf)
    queda = 1 - manuf[atual] / manuf[pico]
    id_fim = max(idosos["BRA"])
    id_antes = idosos["BRA"].get(id_fim - 25)
    renda_baixa = br[fim] < LIM_RENDA_RELATIVA_ALTA

    dim = [
        {"id": "desindustrializacao", "rotulo": "Desindustrialização precoce",
         "texto": ("Ocorre quando um país perde capacidade industrial e tecnológica complexa antes de atingir alta renda per capita. A pauta regride para "
                   "produtos primários de baixo valor agregado (commodities agrícolas e minerais), o que deixa a economia vulnerável a choques externos "
                   "e estagna os salários qualificados."),
         "regra": (f"Compatível se a indústria de transformação perdeu pelo menos um terço da participação que teve no pico (desde {INICIO_INDUSTRIA}) "
                   f"e a renda per capita ainda está abaixo de {LIM_RENDA_RELATIVA_ALTA:.0f}% da dos EUA."),
         "compativel": queda >= QUEDA_INDUSTRIA_PRECOCE and renda_baixa,
         "evidencias": [_item("Indústria de transformação no pico", f"{macro._fmt(manuf[pico])}% do PIB", f"em {pico}"),
                        _item("Indústria de transformação hoje", f"{macro._fmt(manuf[atual])}% do PIB", f"em {atual}; queda de {macro._fmt(queda * 100, 0)}% sobre o pico"),
                        _item("Renda per capita (poder de compra) hoje", f"{macro._fmt(br[fim])}% da dos EUA", f"em {fim}"),
                        *([_item("Para comparar: Coreia do Sul, indústria hoje", f"{macro._fmt(ind['KOR'][max(ind['KOR'])])}% do PIB", f"em {max(ind['KOR'])}")] if "KOR" in ind else [])]},
        {"id": "estagnacao", "rotulo": "Estagnação secular",
         "texto": ("Conceito formalizado por Alvin Hansen e retomado por Larry Summers: um longo período, de 10, 20 ou 30 anos, em que a economia cresce perto de zero. "
                   "É puxada pelo envelhecimento da população, pela produtividade parada e pela falta de oportunidades lucrativas de investimento."),
         "regra": f"Compatível se o PIB per capita real cresceu menos de {ESTAGNACAO_PCT_ANO:.0f}% ao ano, em média, nos últimos 10 anos.",
         "compativel": bool(c10 and c10[0] < ESTAGNACAO_PCT_ANO),
         "evidencias": [*([_item("Crescimento real per capita, últimos 10 anos", f"{macro._fmt(c10[0], 2)}% ao ano", f"{c10[1]} a {c10[2]}")] if c10 else []),
                        *([_item("Crescimento real per capita, últimos 20 anos", f"{macro._fmt(c20[0], 2)}% ao ano", f"{c20[1]} a {c20[2]}")] if c20 else []),
                        _item("População com 65 anos ou mais", f"{macro._fmt(idosos['BRA'][id_fim])}%",
                              f"em {id_fim}" + (f"; era {macro._fmt(id_antes)}% em {id_fim - 25}" if id_antes is not None else "")),
                        _item("Produtividade", "sem série oficial aqui", "o projeto não mede produtividade: leia esta dimensão com cautela")]},
        {"id": "renda_media", "rotulo": "Armadilha da renda média",
         "texto": ("O país sai da pobreza absoluta graças à urbanização e à mão de obra barata, mas não dá o salto para o grupo dos desenvolvidos. Perde competitividade "
                   "de mão de obra para países mais pobres e não consegue competir em tecnologia de ponta com os ricos. O crescimento trava."),
         "regra": (f"Compatível se, em 30 anos, a renda per capita relativa aos EUA variou menos de {VARIACAO_RELATIVA_ESTAVEL:.0f} p.p. "
                   f"e segue abaixo de {LIM_RENDA_RELATIVA_ALTA:.0f}% da americana."),
         "compativel": var30 is not None and abs(var30) < VARIACAO_RELATIVA_ESTAVEL and renda_baixa,
         "evidencias": [*([_item(f"Renda relativa aos EUA em {ini30}", f"{macro._fmt(br[ini30])}%")] if ini30 in br else []),
                        _item(f"Renda relativa aos EUA em {fim}", f"{macro._fmt(br[fim])}%"),
                        *([_item("Variação em 30 anos", f"{'+' if var30 >= 0 else '−'}{macro._fmt(abs(var30))} p.p.")] if var30 is not None else []),
                        *([_item(f"Para comparar: Coreia do Sul ({ini30} → {fim})", f"{macro._fmt(rel['KOR'][ini30])}% → {macro._fmt(rel['KOR'][fim])}%")]
                          if "KOR" in rel and ini30 in rel["KOR"] else [])]},
        {"id": "declinio", "rotulo": "Decadência / declínio estrutural",
         "texto": ("A renda per capita real cai de forma contínua frente aos pares globais, junto com o colapso da infraestrutura, a fuga de cérebros, a degradação "
                   "institucional e a destruição de capital físico e humano. Exemplos históricos de declínio relativo: a Argentina ao longo do século XX e a Venezuela depois de 2013."),
         "regra": f"Compatível se a renda per capita relativa aos EUA caiu {QUEDA_RELATIVA_DECLINIO:.0f} p.p. ou mais em 30 anos.",
         "compativel": var30 is not None and var30 <= -QUEDA_RELATIVA_DECLINIO,
         "evidencias": [*([_item("Brasil, variação em 30 anos", f"{'+' if var30 >= 0 else '−'}{macro._fmt(abs(var30))} p.p. da renda dos EUA")] if var30 is not None else []),
                        *([_item(f"Argentina ({ini30} → {fim})", f"{macro._fmt(rel['ARG'][ini30])}% → {macro._fmt(rel['ARG'][fim])}%")] if "ARG" in rel and ini30 in rel["ARG"] and fim in rel["ARG"] else []),
                        *([_item("Venezuela, PIB per capita real", _variacao_desde(real["VEN"], 2012), f"de 2012 a {max(real['VEN'])}")]
                          if "VEN" in real and 2012 in real["VEN"] else [])]},
    ]
    grafico = {PAISES[c]: [{"ano": a, "valor": round(v, 2)} for a, v in sorted(s.items())] for c, s in rel.items() if c != "VEN"}
    return {"dimensoes": dim, "grafico": {"titulo": "Renda per capita em relação à dos EUA (poder de compra)", "unidade": "% da renda dos EUA", "series": grafico},
            "notas": ["Fonte dos números: Banco Mundial (World Development Indicators), que reúne dados do IBGE e de outros institutos oficiais; costuma ter 1 a 2 anos de atraso.",
                      f"As regras para marcar “compatível” são do projeto: servem para organizar a leitura, não são consenso acadêmico. A série de indústria começa em {INICIO_INDUSTRIA} porque, antes, os dados do Brasil têm quebras.",
                      "Estas condições não são excludentes e uma resposta “não compatível” não prova o contrário: ela só diz que os números escolhidos não indicam o fenômeno."],
            "fonte": {"nome": "Banco Mundial — World Development Indicators (NY.GDP.PCAP.PP.KD, NY.GDP.PCAP.KD, NV.IND.MANF.ZS, SP.POP.65UP.TO.ZS)",
                      "url": "https://databank.worldbank.org/source/world-development-indicators"}}


def dimensoes() -> dict:
    saida: dict[str, Any] = {"consultado_em": macro._agora(), "erros": []}
    for chave, fn in (("ciclo", ciclo), ("estrutura", estrutura)):
        try:
            saida[chave] = fn()
        except Exception as exc:  # noqa: BLE001 — uma dimensão fora do ar não derruba a outra
            log.warning("dimensão %s indisponível: %s", chave, exc)
            saida[chave] = None
            saida["erros"].append(f"{'O ciclo econômico' if chave == 'ciclo' else 'A tendência estrutural'} não pôde ser calculado agora (fonte oficial sem resposta).")
    if saida["ciclo"] is None and saida["estrutura"] is None:
        raise RuntimeError("; ".join(saida["erros"]))
    return saida
