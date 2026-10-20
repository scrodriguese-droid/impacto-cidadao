"""Autoria dos projetos de lei da Câmara: líderes por tema, projetos por político e divisão por tema.

Lê o resultado do coletor (`coletor_autoria.py` → dados/autoria/autoria_camara.json); não consulta fonte
nenhuma em tempo de requisição. Só contagem oficial, sem ranking de "melhores" e sem juízo político:
quantidade de projetos não mede qualidade, e o texto da tela diz isso.
"""
from __future__ import annotations

import json
import threading
from collections import Counter
from typing import Any

from config import RAIZ
from fontes import ErroFonte

ARQUIVO = RAIZ / "dados" / "autoria" / "autoria_camara.json"
LIMITE_PROJETOS = 300

# Campos do site (temas.py) → temas oficiais da Câmara. A Câmara não tem tema "impostos"; tributos ficam
# em "Finanças Públicas e Orçamento". Declarado aqui para a tela nunca fingir equivalência exata.
TEMAS_DO_SITE: dict[str, list[int]] = {
    "impostos": [70], "economia": [40], "emprego": [58], "previdencia": [52], "assistencia": [52],
    "saude": [56], "educacao": [46], "seguranca": [57, 43], "habitacao": [41], "transporte": [61],
    "meio_ambiente": [48], "agro": [64],
}

_trava = threading.Lock()
_cache: dict[str, Any] = {"mtime": None, "dados": None}


def _dados() -> dict:
    try:
        mt = ARQUIVO.stat().st_mtime
    except OSError as exc:
        raise ErroFonte("Dados de autoria ainda não coletados (rode backend/coletor_autoria.py).") from exc
    with _trava:
        if _cache["mtime"] != mt:
            d = json.loads(ARQUIVO.read_text(encoding="utf-8"))
            d["politicos"] = {int(k): v for k, v in d["politicos"].items()}
            d["temas"] = {int(k): v for k, v in d["temas"].items()}
            _cache.update(mtime=mt, dados=d)
        return _cache["dados"]


def _periodo(d: dict, desde, ate) -> tuple[str, str]:
    """Período em datas (AAAA-MM-DD); sem limite = tudo que foi coletado. Comparação de texto ISO é válida."""
    d0, d1 = f"{d['anos'][0]}-01-01", f"{d['anos'][1]}-12-31"
    desde = max(d0, desde.isoformat() if desde else d0)
    ate = min(d1, ate.isoformat() if ate else d1)
    if desde > ate:
        raise ValueError("A data inicial não pode ser posterior à final.")
    return desde, ate


def _lider(d: dict, projetos: list[dict], aprovados: bool) -> dict | None:
    """Quem mais assina projetos (criados ou aprovados) neste conjunto; empate é declarado."""
    cont: Counter[int] = Counter()
    for p in projetos:
        if aprovados and not p["aprovado"]:
            continue
        cont.update(set(p["autores"]))
    if not cont:
        return None
    topo = max(cont.values())
    donos = sorted((i for i, n in cont.items() if n == topo), key=lambda i: d["politicos"][i]["nome"].casefold())
    pol = d["politicos"][donos[0]]
    return {"id": pol["id"], "nome": pol["nome"], "partido": pol["partido"], "uf": pol["uf"],
            "quantidade": topo, "empatados": len(donos) - 1}


def _parada(d: dict, p: dict) -> dict:
    """Em que ponto da tramitação o projeto está: virou lei, arquivado, no Senado ou parado em um órgão."""
    sit = (p.get("situacao") or "").lower()
    sigla = p.get("orgao") or ""
    nome = d.get("orgaos", {}).get(sigla, sigla)
    if p["aprovado"]:
        return {"etapa": "lei", "onde": "Virou lei", "sigla": ""}
    if "arquiv" in sit:
        return {"etapa": "arquivada", "onde": "Arquivada", "sigla": ""}
    if "senado" in sit:
        return {"etapa": "senado", "onde": "Aprovada na Câmara, segue no Senado", "sigla": ""}
    etapa = "plenario" if sigla == "PLEN" else "comissao" if nome.lower().startswith("comiss") else "mesa" if "mesa" in nome.lower() else "outro"
    return {"etapa": etapa, "onde": nome or "Sem órgão informado pela Câmara (em geral, aguardando despacho)", "sigla": sigla}


def _divisao_autores(d: dict, proj: list[dict]) -> dict:
    """Quem assina: deputados (ranking), partidos e tipos de autor (Executivo, Senado, comissões…)."""
    por_dep: dict[int, list[int]] = {}
    por_partido: dict[str, list] = {}
    tipos: Counter[str] = Counter()
    for p in proj:
        deps = set(p["autores"])
        if deps:
            tipos["Deputados(as)"] += 1
        for tp in {(o["tipo"] or "Outros") for o in p.get("outros") or []}:
            tipos[tp] += 1
        if not deps and not p.get("outros"):
            tipos["Autor não informado"] += 1
        for a in deps:
            c = por_dep.setdefault(a, [0, 0])
            c[0] += 1
            c[1] += p["aprovado"]
        for sg in {d["politicos"][a]["partido"] or "Sem partido" for a in deps}:
            c = por_partido.setdefault(sg, [0, 0, set()])
            c[0] += 1
            c[1] += p["aprovado"]
        for a in deps:
            por_partido[d["politicos"][a]["partido"] or "Sem partido"][2].add(a)
    deputados = sorted(por_dep.items(), key=lambda kv: (-kv[1][0], d["politicos"][kv[0]]["nome"].casefold()))[:15]
    partidos = sorted(por_partido.items(), key=lambda kv: (-kv[1][0], kv[0]))[:15]
    return {
        "deputados": [{**{k: d["politicos"][i][k] for k in ("id", "nome", "partido", "uf")}, "criados": c[0], "aprovados": c[1]}
                      for i, c in deputados],
        "partidos": [{"sigla": sg, "criados": c[0], "aprovados": c[1], "deputados": len(c[2])} for sg, c in partidos],
        "tipos": [{"tipo": t, "projetos": n} for t, n in tipos.most_common() if n],
    }


def _paradas(d: dict, proj: list[dict]) -> list[dict]:
    """Onde estão os projetos que NÃO viraram lei (órgão atual), do mais cheio para o mais vazio."""
    cont: Counter[tuple[str, str, str]] = Counter()
    for p in proj:
        if p["aprovado"]:
            continue
        x = _parada(d, p)
        cont[(x["etapa"], x["sigla"], x["onde"])] += 1
    return [{"etapa": e, "sigla": sg, "onde": on, "projetos": n}
            for (e, sg, on), n in cont.most_common()]


def _aviso(d: dict) -> dict:
    return {"gerado_em": d["gerado_em"],
            "fonte": {"nome": "Câmara dos Deputados — arquivos de dados abertos (proposições, autores e temas)",
                      "url": "https://dadosabertos.camara.leg.br/swagger/api.html"},
            "notas": [
                "Projeto = PL, PLP ou PEC. O gráfico e os totais contam todos os autores; os indicadores e a busca por político contam só projetos assinados por deputados(as): Executivo, comissões e Senado ficam de fora.",
                "Aprovado = situação atual “Transformado em Norma Jurídica” (virou lei ou emenda constitucional). Projetos aprovados só na Câmara, ainda no Senado, não contam.",
                "Quando um projeto tem vários autores, cada um deles recebe o projeto. Apoiadores que só assinaram o projeto não contam.",
                "Um projeto pode ter mais de um tema oficial e entra na conta de cada um.",
                "Quantidade de projetos não mede qualidade nem importância: há quem apresente poucos e relevantes e quem apresente muitos de homenagem.",
                "Só a Câmara: o Senado não publica um conjunto de dados equivalente.",
            ]}


def resumo(desde=None, ate=None) -> dict:
    d = _dados()
    desde, ate = _periodo(d, desde, ate)
    proj = [p for p in d["projetos"] if desde <= p["data"] <= ate]
    criados, aprovados = Counter(), Counter()
    for p in proj:
        for t in p["temas"]:
            criados[t] += 1
            if p["aprovado"]:
                aprovados[t] += 1
    temas = sorted(({"cod": t, "nome": d["temas"].get(t, str(t)), "criados": n, "aprovados": aprovados.get(t, 0)}
                    for t, n in criados.items()), key=lambda x: -x["criados"])
    por_campo = {}
    for campo, cods in TEMAS_DO_SITE.items():
        sub = [p for p in proj if set(cods) & set(p["temas"])]
        por_campo[campo] = {
            "temas_camara": [d["temas"].get(c, str(c)) for c in cods],
            "criados": len(sub), "aprovados": sum(1 for p in sub if p["aprovado"]),
            "mais_criados": _lider(d, sub, False), "mais_aprovados": _lider(d, sub, True),
        }
    return {"periodo": {"desde": desde, "ate": ate, "coletado_de": f"{d['anos'][0]}-01-01", "coletado_ate": f"{d['anos'][1]}-12-31"},
            "total": {"criados": len(proj), "aprovados": sum(1 for p in proj if p["aprovado"])},
            "temas": temas, "campos": por_campo,
            "autores": _divisao_autores(d, proj), "paradas": _paradas(d, proj),
            "geral": {"mais_criados": _lider(d, proj, False), "mais_aprovados": _lider(d, proj, True)},
            **_aviso(d)}


def buscar_politicos(q: str, desde=None, ate=None) -> dict:
    import unicodedata

    def norm(s: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower()

    d = _dados()
    desde, ate = _periodo(d, desde, ate)
    nq = norm(q.strip())
    if len(nq) < 2:
        return {"itens": []}
    cont_c, cont_a = Counter(), Counter()
    for p in d["projetos"]:
        if desde <= p["data"] <= ate:
            for a in set(p["autores"]):
                cont_c[a] += 1
                cont_a[a] += p["aprovado"]
    achados = [x for i, x in d["politicos"].items() if nq in norm(x["nome"]) or nq == norm(x["uf"])]
    achados.sort(key=lambda x: (-cont_c.get(x["id"], 0), x["nome"].casefold()))
    return {"itens": [{**x, "criados": cont_c.get(x["id"], 0), "aprovados": cont_a.get(x["id"], 0)} for x in achados[:20]]}


def projetos_do_politico(pid: int, desde=None, ate=None, tema: int | None = None) -> dict:
    d = _dados()
    desde, ate = _periodo(d, desde, ate)
    pol = d["politicos"].get(pid)
    if not pol:
        raise KeyError(pid)
    meus = [p for p in d["projetos"] if pid in p["autores"] and desde <= p["data"] <= ate]
    base = [p for p in meus if tema is None or tema in p["temas"]]
    base.sort(key=lambda p: (p["data"], p["id"]), reverse=True)
    por_tema = Counter(t for p in meus for t in p["temas"])
    return {
        "politico": pol, "periodo": {"desde": desde, "ate": ate},
        "criados": len(meus), "aprovados": sum(1 for p in meus if p["aprovado"]),
        "temas": sorted(({"cod": t, "nome": d["temas"].get(t, str(t)), "criados": n} for t, n in por_tema.items()),
                        key=lambda x: -x["criados"]),
        "total_filtrado": len(base), "limite": LIMITE_PROJETOS, "paradas": _paradas(d, meus),
        "projetos": [{"ident": p["ident"], "data": p["data"], "ementa": p["ementa"], "aprovado": p["aprovado"],
                      "situacao": p["situacao"], "url": p["url"], "ultima_movimentacao": p.get("mov") or None,
                      "andamento": p.get("andamento") or None, "parada": _parada(d, p),
                      "autores": [d["politicos"][a]["nome"] for a in p["autores"]] + [o["nome"] for o in p.get("outros") or []],
                      "temas": [d["temas"].get(t, str(t)) for t in p["temas"]]} for p in base[:LIMITE_PROJETOS]],
        **_aviso(d),
    }


# --------------------------------------------------------------------------- #
# Busca de projetos por tema, com tipo de norma, efeito aparente do texto e efetividade
# --------------------------------------------------------------------------- #
import re
import unicodedata

LIMITE_BUSCA = 30


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower()


# Efeito aparente do texto da EMENTA, só por palavras (sem IA, sem leitura do projeto inteiro). Descreve o que o
# texto diz que faz; não é posição política. Cada grupo é uma expressão regular sobre texto sem acento e minúsculo.
_RESTRINGE = re.compile(r"\b(tipifica\w*|crimes?|criminaliza\w*|penas?|penal|penais|hediond\w*|proibi\w*|proibe\w*|veda\w*|"
                        r"vedacao|agrava\w*|endurece\w*|multas?|sancoes|sancao administrativa|reclusao|detencao|"
                        r"suspende\w*|restring\w*|limita\w*|obrigatori\w*|obriga\w*)\b")
_AMPLIA = re.compile(r"\b(garante\w*|assegura\w*|isen\w*|descriminaliza\w*|despenaliza\w*|autoriza\w*|permite\w*|amplia\w*|"
                     r"reconhece\w*|anistia\w*|perdao|concede\w*|deducao|deducoes|dedutive\w*|incentiv\w*|beneficio\w*|gratuidade|gratuito\w*|"
                     r"revoga\w*|flexibiliza\w*|facilita\w*|desonera\w*|credito presumido|direito\w* de|passe livre)\b")
_PROCEDIMENTAL = re.compile(r"\b(denomina\w*|inscreve\w*|livro dos her\w*|dia nacional|semana nacional|mes de|homenag\w*|"
                            r"institui o dia|institui a semana|cadastro\w*|relatorio\w*|registro\w*|regulamenta\w*|"
                            r"diretrizes|procedimento\w*|sistema nacional|politica nacional|programa nacional|"
                            r"campanha|estatuto|consolida\w*|prazo\w*|competencia\w*|orgao\w*|fiscaliza\w*|transparencia)\b")

ROTULOS_EFEITO = {
    "restringe": "Restringe ou pune mais",
    "amplia": "Amplia permissões ou direitos",
    "procedimental": "Procedimental ou de organização",
    "indefinido": "Não classificado",
}


def efeito_da_ementa(ementa: str) -> str:
    """Efeito aparente do texto: restringe/pune, amplia permissões/direitos, procedimental. Empate ou nada = indefinido."""
    t = _sem_acento(ementa)
    if re.search(r"\b(denomina\w*|inscreve\w*|livro dos her\w*|dia nacional|semana nacional|homenag\w*)\b", t):
        return "procedimental"
    r, a, p = len(_RESTRINGE.findall(t)), len(_AMPLIA.findall(t)), len(_PROCEDIMENTAL.findall(t))
    topo = max(r, a, p)
    if topo == 0:
        return "indefinido"
    vencedores = [n for n, v in (("restringe", r), ("amplia", a), ("procedimental", p)) if v == topo]
    return vencedores[0] if len(vencedores) == 1 else "indefinido"


def _efetividade(d: dict, p: dict) -> str:
    etapa = _parada(d, p)["etapa"]
    return "aprovado" if etapa == "lei" else "arquivado" if etapa == "arquivada" else "em_tramitacao"


def _perfil(serie: list[dict], ano_corrente_parcial: bool) -> dict | None:
    """Compara o início e o fim do período buscado (primeiros e últimos 3 anos): volume e efeito predominante."""
    if len(serie) < 4:
        return None
    k = min(3, len(serie) // 2)
    ini, fim = serie[:k], serie[-k:]

    def soma(trecho: list[dict]) -> dict:
        t = {c: sum(x[c] for x in trecho) for c in ("restringe", "amplia", "procedimental", "indefinido")}
        t["total"] = sum(t.values())
        classificados = t["total"] - t["indefinido"]
        dom = max(("restringe", "amplia", "procedimental"), key=lambda c: t[c]) if classificados else None
        t["dominante"] = dom
        t["pct_dominante"] = round(100 * t[dom] / classificados) if dom else None
        t["classificados"] = classificados
        t["anos"] = [trecho[0]["ano"], trecho[-1]["ano"]]
        return t

    a, b = soma(ini), soma(fim)
    # o ano em curso está incompleto: a variação do volume usa média por ano só quando o fim o inclui
    variacao = round(100 * (b["total"] - a["total"]) / a["total"]) if a["total"] else None
    return {"inicio": a, "fim": b, "variacao_volume_pct": variacao, "ano_parcial": ano_corrente_parcial}


def buscar_projetos(q: str | None, tipo: str | None, efeito: str | None, efetividade: str | None,
                    desde=None, ate=None, deslocamento: int = 0, ano_de: int | None = None, ano_ate: int | None = None) -> dict:
    d = _dados()
    if ano_de or ano_ate:
        from datetime import date as _date
        desde = _date(max(ano_de or d["anos"][0], d["anos"][0]), 1, 1)
        ate = _date(min(ano_ate or d["anos"][1], d["anos"][1]), 12, 31)
    desde, ate = _periodo(d, desde, ate)
    termos = [t for t in _sem_acento(q or "").split() if len(t) >= 2]
    base = []
    for p in d["projetos"]:
        if not (desde <= p["data"] <= ate):
            continue
        sigla = p["ident"].split()[0]
        if tipo and sigla != tipo:
            continue
        if termos:
            if "_t" not in p:  # ementa sem acento, calculada uma vez por projeto
                p["_t"] = _sem_acento(p["ementa"])
            texto = p["_t"]
            if not all(t in texto for t in termos):
                continue
        base.append((p, sigla))
    # termômetro: sobre tudo o que casou com busca, tipo e período (não muda com os filtros de efeito/situação)
    for p, _ in base:  # a classificação por palavras é cara: calcula uma vez por projeto e guarda em memória
        if "_ef" not in p:
            p["_ef"] = efeito_da_ementa(p["ementa"])
    marcados = [(p, s, p["_ef"], _efetividade(d, p)) for p, s in base]
    cont_tipo, cont_efeito, cont_efet = Counter(), Counter(), Counter()
    for _, s, e, f in marcados:
        cont_tipo[s] += 1
        cont_efeito[e] += 1
        cont_efet[f] += 1
    por_ano: dict[int, Counter] = {}
    for p, _s, e, _f in marcados:
        por_ano.setdefault(int(p["data"][:4]), Counter())[e] += 1
    anos = range(int(desde[:4]), int(ate[:4]) + 1)
    serie = [{"ano": a, **{c: por_ano.get(a, Counter()).get(c, 0) for c in ("restringe", "amplia", "procedimental", "indefinido")}} for a in anos]
    from datetime import date as _hoje
    parcial = int(ate[:4]) >= _hoje.today().year
    filtrados = [m for m in marcados if (not efeito or m[2] == efeito) and (not efetividade or m[3] == efetividade)]
    filtrados.sort(key=lambda m: (m[0]["data"], m[0]["id"]), reverse=True)
    pagina = filtrados[deslocamento:deslocamento + LIMITE_BUSCA]
    return {
        "periodo": {"desde": desde, "ate": ate}, "consulta": q or "",
        "total_busca": len(marcados), "total_filtrado": len(filtrados),
        "por_tipo": dict(cont_tipo), "por_efeito": dict(cont_efeito), "por_efetividade": dict(cont_efet),
        "rotulos_efeito": ROTULOS_EFEITO, "anos_disponiveis": d["anos"],
        "serie_anual": serie, "perfil": _perfil(serie, parcial),
        "projetos": [{
            "ident": p["ident"], "tipo_norma": s, "data": p["data"], "ementa": p["ementa"], "url": p["url"],
            "efetividade": f, "orientacao_tema": e, "situacao": p["situacao"], "parada": _parada(d, p),
            "n_autores": len(p["autores"]) + len(p.get("outros") or []),
            "autores": [{"nome": d["politicos"][a]["nome"], "partido": d["politicos"][a]["partido"], "uf": d["politicos"][a]["uf"]}
                        for a in p["autores"][:3]] + [{"nome": o["nome"], "partido": "", "uf": ""} for o in (p.get("outros") or [])[:1]],
            "temas": [d["temas"].get(t, str(t)) for t in p["temas"]],
        } for p, s, e, f in pagina],
        **_aviso(d),
    }
