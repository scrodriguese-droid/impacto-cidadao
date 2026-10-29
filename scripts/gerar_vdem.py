"""Gera frontend/assets/vdem.json a partir do conjunto V-Dem (Varieties of Democracy).

O V-Dem não tem API REST; publica o conjunto de dados em CSV (https://v-dem.net/data/the-v-dem-dataset/).
Usamos o "Country-Year: V-Dem Full+Others", que traz as variáveis dos seis eixos da página Democracia.

Uso (Python 3.9+, só biblioteca padrão):
    python scripts/gerar_vdem.py                 # baixa a versão VERSAO e grava o JSON
    python scripts/gerar_vdem.py --csv arq.csv   # usa um CSV já baixado

Ao sair uma versão nova (normalmente em março), troque VERSAO e rode de novo.
"""
import argparse
import csv
import io
import json
import statistics
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSAO = "16"
URL = f"https://v-dem.net/media/datasets/V-Dem-CY-FullOthers-v{VERSAO}_csv.zip"
SAIDA = Path(__file__).resolve().parent.parent / "frontend" / "assets" / "vdem.json"
ANO_INI = 1945
PAIS = "BRA"
PARES = {"ARG": "Argentina", "CHL": "Chile", "COL": "Colômbia", "MEX": "México", "URY": "Uruguai", "USA": "Estados Unidos"}

# tipo "idx": índice 0–1 (variável v2x…); tipo "osp": indicador na escala original 0–4 (variável …_osp).
# alto = "bom" quando valores maiores são, segundo o V-Dem, mais democracia/menos problema; "ruim" quando valores maiores são pior.
# ancoras: [texto do valor 0, texto do valor máximo] na escala do próprio V-Dem.
EIXOS = [
    {"id": "geral", "nome": "Visão geral", "icone": "fa-scale-balanced", "indicadores": [
        {"var": "v2x_polyarchy", "tipo": "idx", "alto": "bom", "nome": "Democracia eleitoral",
         "mede": "Se as eleições são limpas e competitivas, se há liberdade de organização e de expressão e se o voto é universal. É o índice-síntese do V-Dem."},
        {"var": "v2x_libdem", "tipo": "idx", "alto": "bom", "nome": "Democracia liberal",
         "mede": "A democracia eleitoral somada a limites ao poder: direitos individuais protegidos, Justiça independente e Legislativo que controla o Executivo."},
    ]},
    {"id": "poderes", "nome": "Separação de poderes", "icone": "fa-building-columns", "indicadores": [
        {"var": "v2xlg_legcon", "tipo": "idx", "alto": "bom", "nome": "Controle do Legislativo sobre o Executivo",
         "mede": "Capacidade do Congresso e de órgãos de fiscalização de questionar, investigar e controlar o Executivo."},
        {"var": "v2x_jucon", "tipo": "idx", "alto": "bom", "nome": "Limites do Judiciário ao Executivo",
         "mede": "Em que medida o Executivo respeita decisões judiciais e a Justiça age com independência."},
        {"var": "v2juhcind", "tipo": "osp", "alto": "bom", "nome": "Independência da corte suprema",
         "mede": "Com que frequência a corte suprema decide contra a vontade do governo em casos de peso, sem sofrer retaliação.",
         "ancoras": ["Sempre decide conforme o governo", "Nunca decide só para agradar o governo"]},
        {"var": "v2exrescon", "tipo": "osp", "alto": "bom", "nome": "Respeito do Executivo à Constituição",
         "mede": "Se presidente e ministros agem dentro dos limites da Constituição.",
         "ancoras": ["Violam sempre que querem", "Respeitam sempre"]},
    ]},
    {"id": "eleicoes", "nome": "Eleições e partidos", "icone": "fa-check-to-slot", "indicadores": [
        {"var": "v2xel_frefair", "tipo": "idx", "alto": "bom", "nome": "Eleições limpas",
         "mede": "Ausência de fraude, compra de votos, intimidação e violência nas eleições."},
        {"var": "v2elembaut", "tipo": "osp", "alto": "bom", "nome": "Autonomia da Justiça Eleitoral",
         "mede": "Se o órgão que organiza as eleições (no Brasil, TSE e TREs) tem autonomia frente ao governo.",
         "ancoras": ["Controlada pelo governo", "Autônoma e imparcial"]},
        {"var": "v2xps_party", "tipo": "idx", "alto": "bom", "nome": "Institucionalização dos partidos",
         "mede": "Se os partidos têm organização permanente, raízes na sociedade, programa e coesão, em vez de depender de pessoas."},
        {"var": "v2cacamps", "tipo": "osp", "alto": "ruim", "nome": "Polarização política",
         "mede": "Até que ponto as diferenças políticas azedam as relações pessoais (família, trabalho, associações).",
         "ancoras": ["Opositores convivem com cordialidade", "Convivência hostil entre campos opostos"]},
        {"var": "v2smpolsoc", "tipo": "osp", "alto": "bom", "nome": "Polarização da sociedade (divergência sobre temas políticos)",
         "mede": "Quanto as divergências de opinião sobre os grandes temas políticos viram choque aberto. Atenção: aqui, quanto maior o valor, MENOR a polarização.",
         "ancoras": ["Polarização séria", "Sem polarização"]},
    ]},
    {"id": "liberdades", "nome": "Liberdades e informação", "icone": "fa-bullhorn", "indicadores": [
        {"var": "v2x_freexp_altinf", "tipo": "idx", "alto": "bom", "nome": "Expressão e fontes alternativas de informação",
         "mede": "Censura, assédio a jornalistas, liberdade de expressão e diversidade de meios de comunicação."},
        {"var": "v2xcs_ccsi", "tipo": "idx", "alto": "bom", "nome": "Espaço da sociedade civil",
         "mede": "Facilidade para ONGs, sindicatos e associações se organizarem e atuarem sem repressão."},
        {"var": "v2x_diagacc", "tipo": "idx", "alto": "bom", "nome": "Controle por mídia e sociedade civil (responsabilização diagonal)",
         "mede": "Quanto cidadãos, imprensa e organizações conseguem cobrar o governo fora do voto. É diferente de acesso à Justiça."},
        {"var": "v2smgovdom", "tipo": "osp", "alto": "bom", "nome": "Desinformação do governo nas redes",
         "mede": "Com que frequência o governo e seus agentes espalham informação falsa para a própria população nas redes sociais. Quanto maior o valor, menos desinformação.",
         "ancoras": ["Extremamente frequente", "Nunca ou quase nunca"]},
        {"var": "v2smpardom", "tipo": "osp", "alto": "bom", "nome": "Desinformação de partidos e candidatos nas redes",
         "mede": "Com que frequência os grandes partidos e candidatos espalham informação falsa nas redes. Quanto maior o valor, menos desinformação.",
         "ancoras": ["Extremamente frequente", "Nunca ou quase nunca"]},
    ]},
    {"id": "corrupcao", "nome": "Burocracia e corrupção", "icone": "fa-scale-unbalanced", "indicadores": [
        {"var": "v2x_corr", "tipo": "idx", "alto": "ruim", "nome": "Corrupção política",
         "mede": "Índice composto de corrupção no Executivo, no Legislativo, no Judiciário e no serviço público. Quanto MAIOR, mais corrupção percebida pelos especialistas."},
        {"var": "v2x_execorr", "tipo": "idx", "alto": "ruim", "nome": "Corrupção no Executivo",
         "mede": "Propina e desvio de recursos públicos no Executivo. Quanto MAIOR, mais corrupção."},
        {"var": "v2clrspct", "tipo": "osp", "alto": "bom", "nome": "Administração pública rigorosa e imparcial",
         "mede": "Se os servidores aplicam a lei de forma técnica e impessoal, sem favorecimento.",
         "ancoras": ["Lei desrespeitada, aplicação arbitrária", "Lei aplicada com rigor e imparcialidade"]},
    ]},
    {"id": "igualdade", "nome": "Inclusão e igualdade", "icone": "fa-people-group", "indicadores": [
        {"var": "v2x_egaldem", "tipo": "idx", "alto": "bom", "nome": "Democracia igualitária",
         "mede": "Quanto direitos e liberdades valem igualmente para todos e os recursos e o poder estão distribuídos de forma equilibrada."},
        {"var": "v2xeg_eqprotec", "tipo": "idx", "alto": "bom", "nome": "Proteção igual de direitos",
         "mede": "Se direitos e liberdades são garantidos de modo parecido entre grupos sociais, inclusive no acesso à Justiça."},
        {"var": "v2xeg_eqdr", "tipo": "idx", "alto": "bom", "nome": "Distribuição igual de recursos",
         "mede": "Quão equilibrada é a distribuição de recursos (saúde, educação, renda) que dá a todos condições de participar da política."},
        {"var": "v2pepwrsoc", "tipo": "osp", "alto": "bom", "nome": "Poder político por grupo social",
         "mede": "Se o poder político está distribuído entre classes, etnias, religiões e regiões ou concentrado em poucos grupos.",
         "ancoras": ["Monopólio de um grupo", "Distribuído entre todos os grupos"]},
        {"var": "v2pepwrgen", "tipo": "osp", "alto": "bom", "nome": "Poder político por gênero",
         "mede": "Se homens e mulheres têm poder político parecido.",
         "ancoras": ["Homens quase monopolizam", "Poder aproximadamente igual"]},
    ]},
]

REGIMES = {"0": "Autocracia fechada", "1": "Autocracia eleitoral", "2": "Democracia eleitoral", "3": "Democracia liberal"}


def abrir_csv(caminho):
    if caminho:
        return open(caminho, encoding="utf-8", newline="")
    req = urllib.request.Request(URL, headers={"User-Agent": "ImpactoCidadao/1.0 (projeto civico; dados abertos V-Dem)"})
    print("baixando", URL, file=sys.stderr)
    with urllib.request.urlopen(req, timeout=300) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    nome = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    return io.TextIOWrapper(z.open(nome), encoding="utf-8", newline="")


def num(s):
    try:
        return round(float(s), 3) if s not in ("", None) else None
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="CSV do V-Dem já baixado (pula o download)")
    args = ap.parse_args()

    campos = {"v2x_regime"}
    for e in EIXOS:
        for ind in e["indicadores"]:
            col = ind["var"] + ("_osp" if ind["tipo"] == "osp" else "")
            ind["col"] = col
            campos |= {col, col + "_codelow", col + "_codehigh"}

    brasil, mundo, pares = {}, {}, {}
    nomes = {}
    with abrir_csv(args.csv) as f:
        for lin in csv.DictReader(f):
            ano, cod = int(lin["year"]), lin["country_text_id"]
            nomes[cod] = lin["country_name"]
            if ano >= ANO_INI:
                for v in ("v2x_polyarchy", "v2x_libdem"):
                    x = num(lin[v])
                    if x is not None:
                        mundo.setdefault((v, ano), []).append(x)
                if cod in PARES:
                    pares.setdefault(cod, {})[ano] = {v: num(lin[v]) for v in ("v2x_polyarchy", "v2x_libdem")}
            if cod == PAIS and ano >= ANO_INI:
                brasil[ano] = {c: num(lin[c]) for c in campos if c in lin} | {"regime": lin["v2x_regime"]}
            # posição no ano mais recente: guarda os valores de todos os países
            for e in EIXOS:
                for ind in e["indicadores"]:
                    x = num(lin[ind["col"]])
                    if x is not None:
                        ind.setdefault("_todos", {}).setdefault(ano, {})[cod] = x

    ano_max = max(brasil)
    saida = {
        "versao": f"v{VERSAO}", "pais": PAIS, "ano_ini": ANO_INI, "ano_fim": ano_max,
        "regimes": REGIMES,
        "regime_serie": [[a, brasil[a]["regime"]] for a in sorted(brasil)],
        "eixos": [],
        "comparacao": {
            "paises": {PAIS: "Brasil", **PARES},
            "indices": {
                v: {
                    "Brasil": [[a, brasil[a][v]] for a in sorted(brasil) if brasil[a].get(v) is not None],
                    **{PARES[c]: [[a, pares[c][a][v]] for a in sorted(pares[c]) if pares[c][a][v] is not None] for c in PARES},
                    "Média mundial": [[a, round(statistics.mean(mundo[(v, a)]), 3)] for a in sorted(brasil) if (v, a) in mundo],
                } for v in ("v2x_polyarchy", "v2x_libdem")
            },
        },
    }
    for e in EIXOS:
        eo = {k: e[k] for k in ("id", "nome", "icone")} | {"indicadores": []}
        for ind in e["indicadores"]:
            col = ind["col"]
            serie = [[a, brasil[a].get(col), brasil[a].get(col + "_codelow"), brasil[a].get(col + "_codehigh")]
                     for a in sorted(brasil) if brasil[a].get(col) is not None]
            if not serie:
                print("sem dados:", ind["var"], file=sys.stderr)
                continue
            ult = serie[-1][0]
            todos = ind["_todos"].get(ult, {})
            ordem = sorted(todos.values(), reverse=(ind["alto"] == "bom"))
            pos = ordem.index(todos[PAIS]) + 1 if PAIS in todos else None
            o = {k: ind[k] for k in ("var", "tipo", "alto", "nome", "mede")}
            o["escala"] = [0, 1] if ind["tipo"] == "idx" else [0, 4]
            if ind.get("ancoras"):
                o["ancoras"] = ind["ancoras"]
            o["serie"] = serie
            o["ranking"] = {"ano": ult, "pos": pos, "de": len(todos)}
            eo["indicadores"].append(o)
        saida["eixos"].append(eo)

    SAIDA.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"ok: {SAIDA} ({SAIDA.stat().st_size // 1024} KB), anos {ANO_INI}-{ano_max}")


if __name__ == "__main__":
    main()
