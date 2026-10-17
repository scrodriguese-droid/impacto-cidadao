"""Composição partidária do Congresso Nacional — cadeiras por partido e limiares de votação.

Fontes (abertas, sem chave):
* Câmara dos Deputados — https://dadosabertos.camara.leg.br/api/v2/deputados (deputados em exercício)
* Senado Federal       — https://legis.senado.leg.br/dadosabertos/senador/lista/atual (senadores em exercício)

Só contagem de parlamentares por partido, na sigla informada pela própria fonte. Sem projeções,
sem juízo político; a ordem de apresentação é alfabética. Câmaras municipais: não há fonte aberta
consolidada e confiável da composição atual (o TSE publica o resultado da eleição, não quem está
em exercício), então a lacuna é declarada.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from config import RAIZ
from fontes import ClienteHTTP, ErroFonte

log = logging.getLogger("impacto.bancadas")

_http = ClienteHTTP(pausa=0.05, timeout=60.0)

TTL_S = 6 * 3600
_cache: dict[str, tuple[float, Any]] = {}
_trava = threading.RLock()  # reentrante: _frentes usa o cache de deputados por dentro do seu

URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/deputados"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/senador/lista/atual"
URL_MESA_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/legislaturas/{leg}/mesa"
URL_MESA_SENADO = "https://legis.senado.leg.br/dadosabertos/composicao/mesaSF"
PAGINA_CAMARA = "https://www.camara.leg.br/deputados/quem-sao"
PAGINA_SENADO = "https://www25.senado.leg.br/web/senadores/em-exercicio"

# Constituição Federal: Câmara 513 (art. 45 e LC 78/1993), Senado 81 (art. 46).
TOTAL_CONSTITUCIONAL = {"camara": 513, "senado": 81}


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _contar(siglas: list[str | None], membros: list[dict] | None = None) -> list[dict]:
    """Cadeiras por partido (ordem alfabética). Com `membros` (um por sigla, na mesma ordem), cada partido
    leva também quem ocupa as cadeiras: nome, UF e página oficial, em ordem de estado e nome."""
    cont: dict[str, int] = {}
    quem: dict[str, list[dict]] = {}
    for i, s in enumerate(siglas):
        s = (s or "").strip() or "Sem partido"
        cont[s] = cont.get(s, 0) + 1
        if membros is not None:
            quem.setdefault(s, []).append(membros[i])
    saida = []
    for s, n in sorted(cont.items(), key=lambda x: x[0].casefold()):
        item = {"sigla": s, "cadeiras": n}
        if membros is not None:
            item["membros"] = sorted(quem[s], key=lambda m: ((m.get("uf") or "~"), (m.get("nome") or "").casefold()))
        saida.append(item)
    return saida


def _limiares(total: int, partidos: list[dict]) -> list[dict]:
    """Limiares sobre o total CONSTITUCIONAL de cadeiras (não sobre as ocupadas)."""
    maiores = sorted((p["cadeiras"] for p in partidos), reverse=True)

    def minimo_partidos(meta: int) -> int | None:
        soma = 0
        for i, n in enumerate(maiores, start=1):
            soma += n
            if soma >= meta:
                return i
        return None

    absoluta = total // 2 + 1
    itens = [
        ("simples", "Maioria simples", absoluta // 2 + 1,
         f"Mais votos “sim” que “não” entre os presentes, com quórum de {absoluta}. O mínimo de {absoluta // 2 + 1} "
         f"votos vale se só o quórum estiver presente; com todos presentes, são {total // 2 + 1}."),
        ("absoluta", "Maioria absoluta", absoluta,
         "Mais da metade de TODOS os membros da Casa, estejam presentes ou não (ex.: lei complementar)."),
        ("tres_quintos", "3/5 — emenda à Constituição (PEC)", math.ceil(total * 3 / 5),
         "Três quintos dos membros da Casa, em dois turnos de votação."),
    ]
    return [{"id": c, "nome": n, "votos": v, "nota": nota, "minimo_partidos": minimo_partidos(v)}
            for c, n, v, nota in itens]


def _presidente_camara() -> dict | None:
    """Presidente da Mesa Diretora: titular do cargo 'Presidente' sem data de fim, na legislatura atual."""
    legs = _http.json("https://dadosabertos.camara.leg.br/api/v2/legislaturas",
                      {"itens": 1, "ordem": "DESC", "ordenarPor": "id"}).get("dados") or []
    if not legs:
        return None
    mesa = _http.json(URL_MESA_CAMARA.format(leg=legs[0]["id"])).get("dados") or []
    for m in mesa:
        if (m.get("titulo") or "").strip().casefold() == "presidente" and not m.get("dataFim"):
            return {"nome": m.get("nome"), "partido": m.get("siglaPartido"), "uf": m.get("siglaUf"),
                    "desde": m.get("dataInicio"), "foto": m.get("urlFoto"),
                    "url": f"https://www.camara.leg.br/deputados/{m.get('id')}"}
    return None


def _presidente_senado() -> dict | None:
    """Presidente do Senado = presidente da Mesa (Comissão Diretora)."""
    r = _http.get(URL_MESA_SENADO, accept="application/json").json()
    try:
        colegiados = r["MesaSenado"]["Colegiados"]["Colegiado"]
    except (KeyError, TypeError):
        return None
    if isinstance(colegiados, dict):
        colegiados = [colegiados]
    for col in colegiados:
        cargos = (col.get("Cargos") or {}).get("Cargo") or []
        if isinstance(cargos, dict):
            cargos = [cargos]
        for c in cargos:
            nome_cargo = c.get("Cargo")
            nome_cargo = nome_cargo[0] if isinstance(nome_cargo, list) and nome_cargo else nome_cargo
            if (nome_cargo or "").strip().casefold() == "presidente":
                nome = re.sub(r"^Senador(a)?\s+", "", c.get("NomeParlamentar") or "")
                m = re.match(r"\(([^-]+)-([A-Z]{2})\)", c.get("Bancada") or "")
                return {"nome": nome, "partido": m.group(1) if m else None, "uf": m.group(2) if m else None,
                        "desde": None,
                        # 'Http' é o código do senador; a foto oficial segue este padrão
                        "foto": f"https://legis.senado.leg.br/senadores/fotos-oficiais/{c.get('Http')}",
                        "url": f"https://www25.senado.leg.br/web/senadores/senador/-/perfil/{c.get('Http')}"}
    return None


def _presidente_seguro(fn) -> dict | None:  # noqa: ANN001
    """A identificação do presidente é complementar: se falhar, a página segue só com as cadeiras."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("Presidente indisponível: %s", exc)
        return None


def _casa(chave: str, nome: str, partidos: list[dict], url_api: str, pagina: str,
          presidente: dict | None = None) -> dict:
    total = TOTAL_CONSTITUCIONAL[chave]
    ocupadas = sum(p["cadeiras"] for p in partidos)
    return {
        "id": chave, "nome": nome, "cadeiras_total": total, "cadeiras_ocupadas": ocupadas,
        "cadeiras_sem_titular": max(total - ocupadas, 0),
        "presidente": presidente,
        "partidos": partidos, "n_partidos": len(partidos),
        "limiares": _limiares(total, partidos),
        "fonte": {"nome": f"{nome} — dados abertos", "url_api": url_api, "url_pagina": pagina},
    }


def _deputados() -> list[dict]:
    """Deputados em exercício (id, nome, partido, UF), todas as páginas."""
    deps: list[dict] = []
    pagina = 1
    while True:
        r = _http.json(URL_CAMARA, {"itens": 100, "pagina": pagina, "ordem": "ASC", "ordenarPor": "nome"})
        dados = r.get("dados") or []
        deps += [{"id": d.get("id"), "nome": d.get("nome"), "partido": (d.get("siglaPartido") or "").strip() or "Sem partido",
                  "uf": d.get("siglaUf")} for d in dados]
        if not dados or not any(l.get("rel") == "next" for l in r.get("links", [])):
            break
        pagina += 1
        if pagina > 20:
            raise ErroFonte("Paginação da Câmara não terminou.")
    if not deps:
        raise ErroFonte("A Câmara não retornou deputados.")
    return deps


def _camara() -> dict:
    deps = _memo("deputados", _deputados)
    return _casa("camara", "Câmara dos Deputados", _contar([d["partido"] for d in deps], [
        {"nome": d["nome"], "uf": d["uf"], "url": f"https://www.camara.leg.br/deputados/{d['id']}"} for d in deps]),
                 URL_CAMARA, PAGINA_CAMARA,
                 _presidente_seguro(_presidente_camara))


def _senado() -> dict:
    r = _http.get(URL_SENADO, accept="application/json").json()
    try:
        lista = r["ListaParlamentarEmExercicio"]["Parlamentares"]["Parlamentar"]
    except (KeyError, TypeError) as exc:
        raise ErroFonte("Formato inesperado na resposta do Senado.") from exc
    if isinstance(lista, dict):
        lista = [lista]
    ident = [p.get("IdentificacaoParlamentar") or p for p in lista]
    siglas = [i.get("SiglaPartidoParlamentar") for i in ident]
    if not siglas:
        raise ErroFonte("O Senado não retornou senadores.")
    membros = [{"nome": i.get("NomeParlamentar"), "uf": i.get("UfParlamentar"),
                "url": f"https://www25.senado.leg.br/web/senadores/senador/-/perfil/{i.get('CodigoParlamentar')}"} for i in ident]
    return _casa("senado", "Senado Federal", _contar(siglas, membros), URL_SENADO, PAGINA_SENADO,
                 _presidente_seguro(_presidente_senado))


# Frentes Parlamentares: grupos suprapartidários registrados na Câmara. Seleção de frentes de grande
# alcance, escolhida por tema (não por orientação política); o nome é o título oficial registrado.
# O ID é o da API da Câmara na 57ª legislatura. Há centenas de outras frentes registradas.
FRENTES_SELECIONADAS = [
    (54323, "Agropecuária"), (54477, "Evangélica"), (54326, "Católica"), (54317, "Segurança Pública"),
    (54406, "Ambiental"), (54449, "Saúde"), (54377, "Educação"), (54284, "Empreendedorismo"),
    (54296, "Comércio e Serviços"),
]
URL_FRENTES = "https://dadosabertos.camara.leg.br/api/v2/frentes"


def _frentes() -> dict:
    deps = _memo("deputados", _deputados)
    por_id = {d["id"]: d for d in deps}
    total = TOTAL_CONSTITUCIONAL["camara"]
    absoluta, pec = total // 2 + 1, math.ceil(total * 3 / 5)
    lista, indice = [], {}
    for fid, tema in FRENTES_SELECIONADAS:
        det = (_http.json(f"{URL_FRENTES}/{fid}").get("dados")) or {}
        membros = _http.json(f"{URL_FRENTES}/{fid}/membros").get("dados") or []
        ativos = [por_id[m["id"]] for m in membros if m.get("id") in por_id]
        coord = det.get("coordenador") or {}
        for d in ativos:
            indice.setdefault(d["id"], {"id": d["id"], "nome": d["nome"], "partido": d["partido"], "uf": d["uf"], "frentes": []})
            indice[d["id"]]["frentes"].append(fid)
        n = len(ativos)
        lista.append({
            "id": fid, "tema": tema, "nome": det.get("titulo") or f"Frente {tema}",
            "coordenador": {"nome": coord.get("nome"), "partido": coord.get("siglaPartido"), "uf": coord.get("siglaUf")} if coord else None,
            "deputados": n, "percentual": round(100 * n / total, 1),
            "partidos": _contar([d["partido"] for d in ativos]),
            "ufs": len({d["uf"] for d in ativos}),
            "atinge_absoluta": n >= absoluta, "atinge_pec": n >= pec,
            "url_pagina": f"https://www.camara.leg.br/internet/deputado/frenteDetalhe.asp?id={fid}",
        })
    return {
        "total_camara": total, "maioria_absoluta": absoluta, "tres_quintos": pec,
        "frentes": sorted(lista, key=lambda f: f["tema"].casefold()),
        "parlamentares": sorted(indice.values(), key=lambda d: (d["nome"] or "").casefold()),
        "senado": {"disponivel": False,
                   "motivo": ("A API de dados abertos do Senado não publica a lista de senadores por frente parlamentar "
                              "(só por comissão). Por isso as frentes mostram apenas deputados; os senadores que assinam "
                              "frentes mistas não entram na conta.")},
        "notas": [
            "Frente parlamentar é uma associação voluntária e suprapartidária: pertencer a ela não obriga ninguém a votar de certo modo.",
            "Contamos só deputados em exercício que constam como membros da frente na Câmara; ex-membros e suplentes fora do exercício ficam de fora.",
            "As frentes mostradas são uma seleção por tema; a Câmara tem centenas de outras registradas na legislatura.",
            "A comparação com 257 (maioria absoluta) e 308 (PEC) é aritmética, não previsão: membros de uma mesma frente votam de formas diferentes.",
        ],
    }


def _memo(chave: str, produzir):  # noqa: ANN001
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < TTL_S:
        return _cache[chave][1]
    with _trava:
        agora = time.time()
        if chave in _cache and agora - _cache[chave][0] < TTL_S:
            return _cache[chave][1]
        valor = produzir()
        _cache[chave] = (agora, valor)
        return valor


def bancadas() -> dict:
    """Cada Casa é independente: se uma falhar, a outra continua e o erro é declarado."""
    casas, erros = [], []
    for chave, fn in (("camara", _camara), ("senado", _senado)):
        try:
            casas.append(_memo(chave, fn))
        except Exception as exc:  # noqa: BLE001
            log.warning("Fonte %s indisponível: %s", chave, exc)
            erros.append({"id": chave, "mensagem": "Fonte oficial indisponível no momento."})
    if not casas:
        raise ErroFonte("Câmara e Senado indisponíveis.")
    return {
        "consultado_em": _agora(),
        "casas": casas,
        "erros": erros,
        "camaras_municipais": {
            "disponivel": False,
            "motivo": ("Não há fonte aberta consolidada e confiável da composição ATUAL das câmaras municipais. "
                       "O TSE divulga o resultado da eleição de vereadores (eleitos por partido), mas não quem está "
                       "em exercício, nem trocas de partido, licenças e suplências. Para não apresentar um número "
                       "possivelmente desatualizado, não mostramos essa composição."),
            "url_fonte": "https://dadosabertos.tse.jus.br/",
        },
        "notas": [
            "Contagem de parlamentares em exercício por partido, conforme informado pela própria Casa; cadeiras sem titular não entram.",
            "Partidos federados ou em bloco continuam contados individualmente, como a fonte informa.",
            "Os limiares usam o total constitucional de cadeiras (513 e 81), não o número de parlamentares em exercício.",
            "É aritmética, não previsão: votar de acordo com o partido não é obrigatório.",
        ],
    }


def frentes_tematicas() -> dict:
    """Bancadas temáticas (frentes parlamentares). Falha da fonte vira ErroFonte para a rota tratar."""
    return {"consultado_em": _agora(), **_memo("frentes", _frentes)}


# --------------------------------------------------------------------------- #
# Quem vota junto: alinhamento com o Governo, coesão e afinidade entre partidos
# --------------------------------------------------------------------------- #
ARQ_ALINHAMENTO = RAIZ / "dados" / "votacoes" / "alinhamento_camara.json"
LIMIAR_ALINHADO = 70       # % de votos iguais à orientação do Governo para o atalho "mais alinhados"
MIN_VOTOS_PARTIDO = 30     # abaixo disso o percentual não é confiável e o partido fica de fora
MIN_VOTACOES_PAR = 20      # idem para a afinidade entre dois partidos
_cache_alinh: dict[str, Any] = {"mtime": None, "dados": None}


def _dados_alinhamento() -> dict:
    try:
        mt = ARQ_ALINHAMENTO.stat().st_mtime
    except OSError as exc:
        raise ErroFonte("Alinhamento de votação ainda não coletado (rode backend/coletor_votacoes.py).") from exc
    with _trava:
        if _cache_alinh["mtime"] != mt:
            _cache_alinh.update(mtime=mt, dados=json.loads(ARQ_ALINHAMENTO.read_text(encoding="utf-8")))
        return _cache_alinh["dados"]


def alinhamento(desde: int | None = None, ate: int | None = None) -> dict:
    d = _dados_alinhamento()
    anos = sorted(int(a) for a in d["anos"])
    desde, ate = max(anos[0], desde or anos[0]), min(anos[-1], ate or anos[-1])
    if desde > ate:
        raise ValueError("O ano inicial não pode ser posterior ao final.")
    sel = [d["anos"][str(a)] for a in range(desde, ate + 1) if str(a) in d["anos"]]
    tot: dict[str, dict] = {}
    pares: dict[str, list[int]] = {}
    for a in sel:
        for sg, x in a["partidos"].items():
            t = tot.setdefault(sg, {"gov_ok": 0, "gov_n": 0, "rice_soma": 0.0, "rice_n": 0})
            for k in t:
                t[k] += x[k]
        for k, (ok, n) in a["pares"].items():
            q = pares.setdefault(k, [0, 0])
            q[0] += ok
            q[1] += n
    afin: dict[str, list[dict]] = {}
    for k, (ok, n) in pares.items():
        if n < MIN_VOTACOES_PAR:
            continue
        a, b = k.split("|")
        pct = 100 * ok / n
        afin.setdefault(a, []).append({"sigla": b, "pct": round(pct, 1), "votacoes": n})
        afin.setdefault(b, []).append({"sigla": a, "pct": round(pct, 1), "votacoes": n})
    itens = []
    for sg, t in sorted(tot.items(), key=lambda kv: kv[0].casefold()):
        if t["gov_n"] < MIN_VOTOS_PARTIDO or sg.upper() in ("S.PART.", "SEM PARTIDO"):
            continue
        outros = sorted(afin.get(sg, []), key=lambda x: (-x["pct"], x["sigla"]))
        itens.append({
            "sigla": sg, "votos": t["gov_n"],
            "alinhamento_governo": round(100 * t["gov_ok"] / t["gov_n"], 1),
            "coesao": round(100 * t["rice_soma"] / t["rice_n"], 1) if t["rice_n"] else None,
            "mais_afins": outros[:5], "menos_afins": list(reversed(outros[-3:])) if len(outros) > 5 else [],
        })
    return {
        "periodo": {"desde": desde, "ate": ate, "anos_disponiveis": anos},
        "votacoes": sum(a["votacoes"] for a in sel), "limiar_alinhado": LIMIAR_ALINHADO,
        "gerado_em": d["gerado_em"], "partidos": itens,
        "fonte": {"nome": "Câmara dos Deputados — votações nominais e orientações de bancada (dados abertos)",
                  "url": "https://dadosabertos.camara.leg.br/swagger/api.html"},
        "notas": [
            "Não classificamos partidos como esquerda, centro ou direita: a Câmara não faz essa classificação e qualquer rótulo seria opinião. Mostramos só o comportamento em votações.",
            "Alinhamento com o Governo: dos votos “Sim” e “Não” do partido, quantos coincidem com a orientação do Governo, nas votações nominais em que o Governo orientou “Sim” ou “Não”.",
            "Coesão: 100% = todos os deputados do partido votaram igual; valores baixos indicam bancada dividida. É a média das votações em que o partido teve ao menos 3 votos.",
            f"Afinidade: percentual das votações em que a posição da maioria de cada partido coincidiu. Só aparece com pelo menos {MIN_VOTACOES_PAR} votações em comum.",
            "Votações nominais não são todas as decisões do Congresso (muita coisa é votada simbolicamente) e o alinhamento muda com o tema: é retrato, não previsão.",
            f"Partidos com menos de {MIN_VOTOS_PARTIDO} votos no período não aparecem, por falta de base para o percentual.",
        ],
    }
