"""Radiografia por período: ciclo, estrutura e as três políticas (fiscal, monetária e tributária) de cada fase recente.

As normas descrevem o que cada texto estabelece, sem juízo sobre ter sido acertado ou não. Os números vêm das séries oficiais
(IBGE, Banco Central, Banco Mundial) e usam as mesmas regras de `analise_dimensoes` para a fase do ciclo."""
from __future__ import annotations

import analise_dimensoes as ad
import macro

PERIODOS = [
    {"id": "2020", "rotulo": "2020", "titulo": "O choque da pandemia", "inicio": "2020-01", "fim": "2020-12", "ano_ini": 2019, "ano_fim": 2020,
     "normas": [
         {"tipo": "fiscal", "norma": "Lei 13.982/2020", "texto": "Criou o auxílio emergencial de R$ 600 por mês para trabalhadores informais e de baixa renda."},
         {"tipo": "fiscal", "norma": "Emenda Constitucional 106/2020", "texto": "Regime extraordinário (“Orçamento de Guerra”): separou as despesas da pandemia do orçamento regular, dispensou temporariamente algumas regras fiscais e autorizou o Banco Central a comprar títulos no mercado."},
         {"tipo": "fiscal", "norma": "Lei Complementar 173/2020", "texto": "Mandou recursos da União a estados e municípios e suspendeu o pagamento de dívidas deles com a União; em troca, congelou reajustes de servidores até o fim de 2021."},
         {"tipo": "tributaria", "norma": "Decreto 10.305/2020", "texto": "Zerou por 90 dias a alíquota do IOF nas operações de crédito."},
         {"tipo": "tributaria", "norma": "Prazos de pagamento", "texto": "Foram adiados os prazos de pagamento de alguns tributos federais e do Simples Nacional."}]},
    {"id": "2021-2022", "rotulo": "2021–2022", "titulo": "Recuperação e fim de mandato", "inicio": "2021-01", "fim": "2022-12", "ano_ini": 2020, "ano_fim": 2022,
     "normas": [
         {"tipo": "fiscal", "norma": "Lei 14.284/2021", "texto": "Criou o Auxílio Brasil, no lugar do Bolsa Família."},
         {"tipo": "fiscal", "norma": "Emenda Constitucional 113/2021", "texto": "Limitou o pagamento de precatórios e mudou a correção do teto de gastos, o que abriu espaço no orçamento de 2022."},
         {"tipo": "fiscal", "norma": "Emenda Constitucional 123/2022", "texto": "Reconheceu estado de emergência e autorizou ampliar benefícios sociais em 2022 sem contar no teto de gastos."},
         {"tipo": "tributaria", "norma": "Lei Complementar 192/2022", "texto": "Fixou o ICMS dos combustíveis em um valor por litro, igual em todo o país."},
         {"tipo": "tributaria", "norma": "Lei Complementar 194/2022", "texto": "Classificou combustíveis, energia, comunicações e transporte coletivo como bens essenciais, o que limita a alíquota de ICMS sobre eles."}]},
    {"id": "2023-hoje", "rotulo": "2023 até hoje", "titulo": "Transição e novo ciclo", "inicio": "2023-01", "fim": None, "ano_ini": 2022, "ano_fim": None,
     "normas": [
         {"tipo": "fiscal", "norma": "Emenda Constitucional 126/2022", "texto": "A “PEC da Transição” (dez/2022): abriu espaço fora do teto de gastos para 2023 e mandou o governo propor uma nova regra fiscal."},
         {"tipo": "fiscal", "norma": "Lei Complementar 200/2023", "texto": "Criou o novo arcabouço fiscal: no lugar do teto de gastos, metas de resultado primário e um limite para o gasto crescer.",
          "url": "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp200.htm"},
         {"tipo": "tributaria", "norma": "Emenda Constitucional 132/2023", "texto": "Reforma tributária do consumo: cria o IBS e a CBS (um IVA dividido em duas partes), com transição gradual."},
         {"tipo": "tributaria", "norma": "Lei 14.754/2023", "texto": "Passou a tributar os fundos fechados (“exclusivos”) e os investimentos no exterior (offshores)."},
         {"tipo": "tributaria", "norma": "Lei Complementar 214/2025", "texto": "Regulamentou o IBS, a CBS e o Imposto Seletivo."}]},
]
POSTURA = {"afrouxamento": "Estimulativa: juros em queda", "aperto": "Restritiva: juros em alta",
           "restritiva": "Restritiva: juros altos e estáveis", "neutra": "Neutra"}
MUDANCA_SELIC = 1.0            # p.p. de variação da Selic no período para falar em afrouxamento ou aperto
VARIACAO_PRIMARIO = 1.0        # p.p. do PIB de variação do resultado primário (12 meses) para dizer que melhorou ou piorou


def _em(d: dict, mes: str) -> float | None:
    p = macro._ate(d, mes)
    return None if p is None else p[1]


def _fins_de_trimestre(ini: str, fim: str) -> list[str]:
    n, saida = ini, []
    while n <= fim:
        if n[5:7] in ("03", "06", "09", "12"):
            saida.append(n)
        n = macro._mes_antes(n, -1)
    return saida


def _periodo(spec: dict, d: dict, extra: dict, ultimo: str) -> dict:
    ini, fim = spec["inicio"], spec["fim"] or ultimo
    base = macro._mes_antes(ini, 1)
    trimestres = _fins_de_trimestre(ini, fim)
    fases = [f for f in (ad.fase_em(q, d) for q in trimestres) if f]
    nomes = {f["id"]: f["rotulo"] for f in ad.FASES} | {"mista": "Sinais mistos"}
    cont: dict[str, int] = {}
    for f in fases:
        k = f["fase"] or "mista"
        cont[k] = cont.get(k, 0) + 1
    dominante = max(cont, key=cont.get) if cont else None
    tris = [v for k, v in d["tri"].items() if ini <= k <= fim]
    # recessão técnica: dois trimestres seguidos de PIB negativo, contando o trimestre anterior ao período
    recessao = any(d["tri"].get(q, 0) < 0 and d["tri"].get(macro._mes_antes(q, 3), 0) < 0 for q in trimestres)
    qk_fim = max((k for k in d["acum4"] if k <= fim), default=None)
    janela = [v for k, v in d["desemp"].items() if ini <= k <= fim]

    s_base, s_fim = macro._selic_em(d["selic"], base), macro._selic_em(d["selic"], fim)
    selics = [v for v in [macro._selic_em(d["selic"], m) for m in [base, *trimestres, fim]] if v is not None]
    ip_fim = _em(d["ipca"], fim)
    ip_max = max((v for k, v in d["ipca"].items() if ini <= k <= fim), default=None)
    var_selic = None if s_base is None or s_fim is None else round(s_fim - s_base, 2)
    juro_real = None if s_fim is None or ip_fim is None else round(s_fim - ip_fim, 2)
    postura = ("aperto" if var_selic is not None and var_selic >= MUDANCA_SELIC
               else "afrouxamento" if var_selic is not None and var_selic <= -MUDANCA_SELIC
               else "restritiva" if juro_real is not None and juro_real >= ad.JURO_REAL_RESTRITIVO else "neutra")

    prim_b, prim_f = _em(extra["primario12"], base), _em(extra["primario12"], fim)   # BCB: positivo = déficit
    div_b, div_f = _em(extra["dbgg"], base), _em(extra["dbgg"], fim)
    var_prim = None if prim_b is None or prim_f is None else round(prim_f - prim_b, 2)
    sit = None if var_prim is None else "piorou" if var_prim >= VARIACAO_PRIMARIO else "melhorou" if var_prim <= -VARIACAO_PRIMARIO else "estavel"

    renda, manuf = extra["renda_real"], extra["manuf"]
    ano_fim = spec["ano_fim"] or (max(renda) if renda else None)
    cresc = None
    if ano_fim and spec["ano_ini"] in renda and ano_fim in renda and ano_fim > spec["ano_ini"]:
        cresc = round(((renda[ano_fim] / renda[spec["ano_ini"]]) ** (1 / (ano_fim - spec["ano_ini"])) - 1) * 100, 2)
    return {
        "id": spec["id"], "rotulo": spec["rotulo"], "titulo": spec["titulo"], "inicio": ini, "fim": fim,
        "ciclo": {"recessao_tecnica": recessao, "dominante": dominante, "dominante_rotulo": nomes.get(dominante), "contagem": {nomes[k]: v for k, v in cont.items()},
                  "trimestres": [{"mes": f["mes"], "fase": f["fase"]} for f in fases],
                  "pib_trimestre_menor": min(tris) if tris else None, "pib_trimestre_maior": max(tris) if tris else None,
                  "pib_acum4_fim": d["acum4"].get(qk_fim) if qk_fim else None, "pib_acum4_ref": qk_fim,
                  "desemprego_inicio": _em(d["desemp"], base), "desemprego_fim": _em(d["desemp"], macro._mes_antes(fim, 1)),
                  "desemprego_minimo": min(janela) if janela else None, "desemprego_maximo": max(janela) if janela else None},
        "estrutura": {"ano_ini": spec["ano_ini"], "ano_fim": ano_fim, "pib_per_capita_real_cresc_ano": cresc,
                      "industria_inicio": manuf.get(spec["ano_ini"]), "industria_fim": manuf.get(ano_fim) if ano_fim else None},
        "fiscal": {"primario_inicio": prim_b, "primario_fim": prim_f, "variacao_pp": var_prim, "situacao": sit,
                   "divida_inicio": div_b, "divida_fim": div_f,
                   "variacao_divida_pp": None if div_b is None or div_f is None else round(div_f - div_b, 2),
                   "normas": [n for n in spec["normas"] if n["tipo"] == "fiscal"],
                   "nota": "O resultado primário também depende da arrecadação, que sobe e desce com a atividade e a inflação: por isso as normas mostram as decisões de política."},
        "monetaria": {"postura": postura, "postura_rotulo": POSTURA[postura], "selic_inicio": s_base, "selic_fim": s_fim,
                      "selic_minima": min(selics) if selics else None, "selic_maxima": max(selics) if selics else None,
                      "ipca12_fim": ip_fim, "ipca12_max": ip_max, "juro_real_fim": juro_real},
        "tributaria": {"normas": [n for n in spec["normas"] if n["tipo"] == "tributaria"]},
    }


def periodos() -> dict:
    d = ad._dados_ciclo()
    ultimo = max(d["ipca"])
    extra = {"primario12": {p["data"][:7]: p["valor"] for p in macro.serie("primario12")["pontos"]},
             "dbgg": {p["data"][:7]: p["valor"] for p in macro.serie("dbgg_pib")["pontos"]},
             "renda_real": ad._banco_mundial(ad.IND_RENDA_REAL, "BRA").get("BRA", {}),
             "manuf": ad._banco_mundial(ad.IND_INDUSTRIA, "BRA").get("BRA", {})}
    return {"periodos": [_periodo(s, d, extra, ultimo) for s in PERIODOS], "ultimo_mes": ultimo,
            "aviso": ("As normas descrevem o que cada texto estabelece; o projeto não avalia se foram acertadas. Os números são das séries oficiais, "
                      "e a leitura do ciclo segue as regras da seção acima, que são didáticas."),
            "fonte": {"nome": "IBGE, Banco Central (Selic, IPCA, resultado primário e dívida) e Banco Mundial; normas: Planalto e Congresso Nacional",
                      "url": "https://www.planalto.gov.br/"}}
