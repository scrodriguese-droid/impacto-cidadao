"""Partidos políticos: (1) evolução histórica registrada no TSE e (2) mapa de afinidade de votação na Câmara.

MÓDULO 1 — Evolução histórica. Fonte oficial única: página “Partidos políticos registrados no TSE”
(https://www.tse.jus.br/partidos/partidos-registrados-no-tse). Ela traz a lista dos partidos registrados (com a data de
deferimento) e a relação oficial de fusões, incorporações e mudanças de nome/sigla deferidas desde a Lei 9.096/1995.
O Portal de Dados Abertos do TSE (dadosabertos.tse.jus.br) NÃO publica conjunto de dados sobre registro/fusão de partidos
(só resultados, prestação de contas e delegados), por isso lemos a página oficial. Nada é inferido: cada ligação do
grafo é uma linha da tabela do TSE, com processo e data. Antes de 1995, e a data de registro de partidos já extintos,
não constam dessa fonte e a lacuna é declarada.

MÓDULO 2 — Mapa de afinidade de votação. O TSE não define ideologia. Calculamos, só a partir das votações nominais
abertas da Câmara (dados/votacoes/*.json, gerados por coletor_votacoes.py), uma projeção em 2 dimensões (escalonamento
multidimensional clássico) da semelhança de votos entre os partidos em cada ano. Não é uma escala esquerda–direita.
"""
from __future__ import annotations

import html as _html
import json
import logging
import math
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timezone
from typing import Any

from config import RAIZ
from fontes import ClienteHTTP, ErroFonte

log = logging.getLogger("impacto.partidos")

_http = ClienteHTTP(pausa=0.05, timeout=60.0)
TTL_S = 6 * 3600
_cache: dict[str, tuple[float, Any]] = {}
_trava = threading.RLock()

URL_TSE = "https://www.tse.jus.br/partidos/partidos-registrados-no-tse"
URL_TSE_DADOS = "https://dadosabertos.tse.jus.br/"


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


# --------------------------------------------------------------------------- #
# Leitura da página do TSE
# --------------------------------------------------------------------------- #
def _limpa(txt: str) -> str:
    t = re.sub(r"<[^>]+>", " ", txt)
    t = _html.unescape(t).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def _tabelas(pagina: str) -> list[list[list[str]]]:
    saida = []
    for tb in re.findall(r"<table.*?</table>", pagina, flags=re.S | re.I):
        linhas = []
        for tr in re.findall(r"<tr.*?</tr>", tb, flags=re.S | re.I):
            cel = [_limpa(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
            if any(cel):
                linhas.append(cel)
        saida.append(linhas)
    return saida


def _data(txt: str) -> str | None:
    """'30.6.1981', '1º.7.1988' ou '16/11/1995' → ISO."""
    m = re.search(r"(\d{1,2})\D{0,3}[./](\d{1,2})[./](\d{4})", txt)
    if not m:
        return None
    d, mes, a = (int(x) for x in m.groups())
    try:
        return date(a, mes, d).isoformat()
    except ValueError:
        return None


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def _chave(nome: str) -> str:
    """Identifica um partido pelo nome: a sigla entre parênteses, ou o nome inteiro quando não há."""
    n = re.sub(r"[\s*.]+$", "", nome)
    m = re.search(r"\(([^)]+)\)\s*$", n)
    base = m.group(1) if m else n
    return re.sub(r"[^A-Z0-9]", "", _sem_acento(base).upper())


def _partes(txt: str) -> list[str]:
    """'Partido A (A) e Partido B (B)' → ['Partido A (A)', 'Partido B (B)']."""
    return [p.strip(" *.") for p in re.split(r"(?<=\))\s+e\s+", txt) if p.strip(" *.")]


def _nome_sigla(nome: str) -> tuple[str, str]:
    n = re.sub(r"[\s*.]+$", "", nome)
    m = re.search(r"^(.*?)\s*\(([^)]+)\)$", n)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return n, n


def _ler_tse() -> dict:
    r = _http.get(URL_TSE)
    pagina = r.content.decode("utf-8", errors="replace")
    registrados, fusoes, incorporacoes, mudancas = [], [], [], []
    for linhas in _tabelas(pagina):
        topo = " ".join(linhas[0]).upper()
        corpo = linhas[1:]
        if "DEFERIMENTO" in topo and "SIGLA" in topo:
            for c in corpo:
                if len(c) >= 4 and _data(c[3]):
                    registrados.append({"sigla": c[1], "nome": c[2], "deferimento": _data(c[3])})
        elif "FUSIONADOS" in topo:
            fusoes += [{"de": _partes(c[1]), "para": c[2], "processo": c[3], "data": _data(c[4])} for c in corpo if len(c) >= 5 and _data(c[4])]
        elif "INCORPORADO" in topo:
            incorporacoes += [{"de": _partes(c[1]), "para": c[2], "processo": c[3], "data": _data(c[4])} for c in corpo if len(c) >= 5 and _data(c[4])]
        elif "NOVO NOME" in topo:
            mudancas += [{"de": [c[1]], "para": c[2], "processo": c[3], "data": _data(c[4])} for c in corpo if len(c) >= 5 and _data(c[4])]
    if len(registrados) < 20 or not fusoes or not incorporacoes or not mudancas:
        raise ErroFonte("O formato da página do TSE mudou: tabelas esperadas não encontradas.")
    return {"registrados": registrados, "fusoes": fusoes, "incorporacoes": incorporacoes, "mudancas": mudancas}


# --------------------------------------------------------------------------- #
# Grafo: cada partido (registro) é uma entidade que pode mudar de nome
# --------------------------------------------------------------------------- #
def _construir(f: dict) -> dict:
    eventos = ([dict(e, tipo="fusao") for e in f["fusoes"]] + [dict(e, tipo="incorporacao") for e in f["incorporacoes"]]
               + [dict(e, tipo="mudanca") for e in f["mudancas"]])
    # mesma data: fusão/incorporação antes de renomear
    ordem = {"fusao": 0, "incorporacao": 1, "mudanca": 2}
    eventos.sort(key=lambda e: (e["data"], ordem[e["tipo"]]))
    ents: list[dict] = []
    ativo: dict[str, dict] = {}      # chave de nome → entidade viva com esse nome
    aposentado: dict[str, dict] = {}  # chave de nome antigo → última entidade que o usou
    arestas: list[dict] = []

    def nova(nome: str, desde: str | None, processo: str | None = None) -> dict:
        n, s = _nome_sigla(nome)
        e = {"nomes": [{"nome": n, "sigla": s, "desde": desde, "processo": processo}], "inicio": None, "fim": None, "vivo": True}
        ents.append(e)
        ativo[_chave(nome)] = e
        return e

    def obter(nome: str) -> dict:
        k = _chave(nome)
        if k in ativo:
            return ativo[k]
        if k in aposentado and aposentado[k]["vivo"]:   # ex.: nota ** da fonte: “PEN” incorporou em 2019, já chamado Patriota
            return aposentado[k]
        return nova(nome, None)                          # existia antes do 1º evento citado; início fora da fonte

    for ev in eventos:
        if ev["tipo"] == "mudanca":
            e = obter(ev["de"][0])
            k_velho = _chave(ev["de"][0])
            if ativo.get(k_velho) is e:
                del ativo[k_velho]
            aposentado[k_velho] = e
            n, s = _nome_sigla(ev["para"])
            e["nomes"].append({"nome": n, "sigla": s, "desde": ev["data"], "processo": ev["processo"]})
            ativo[_chave(ev["para"])] = e
        elif ev["tipo"] == "incorporacao":
            alvo = obter(ev["para"])
            for nome in ev["de"]:
                e = obter(nome)
                e.update(vivo=False, fim={"data": ev["data"], "tipo": "incorporacao", "processo": ev["processo"]})
                for k in [k for k, v in ativo.items() if v is e]:
                    del ativo[k]
                    aposentado[k] = e
                arestas.append({"de": id(e), "para": id(alvo), "tipo": "incorporacao", "data": ev["data"], "processo": ev["processo"]})
        else:  # fusão
            entradas = [obter(n) for n in ev["de"]]
            e_novo_nome = ev["para"]
            for e in entradas:
                e.update(vivo=False, fim={"data": ev["data"], "tipo": "fusao", "processo": ev["processo"]})
                for k in [k for k, v in ativo.items() if v is e]:
                    del ativo[k]
                    aposentado[k] = e
            novo = nova(e_novo_nome, ev["data"], ev["processo"])
            novo["inicio"] = {"data": ev["data"], "tipo": "fusao", "processo": ev["processo"]}
            for e in entradas:
                arestas.append({"de": id(e), "para": id(novo), "tipo": "fusao", "data": ev["data"], "processo": ev["processo"]})

    # Casa a lista oficial de registrados com as entidades vivas.
    conferencia, sem_evento = [], []
    for reg in f["registrados"]:
        achada = ativo.get(_chave(reg["sigla"])) or ativo.get(_chave(reg["nome"]))
        if achada is None:
            achada = nova(f"{reg['nome']} ({reg['sigla']})", None)
            sem_evento.append(reg["sigla"])
        reg["_e"] = achada
        if achada["inicio"] and achada["inicio"]["tipo"] == "fusao":
            conferencia.append({"sigla": reg["sigla"], "registro_tse": reg["deferimento"], "fusao_tse": achada["inicio"]["data"],
                                "confere": reg["deferimento"] == achada["inicio"]["data"]})
        elif achada["inicio"] is None:
            achada["inicio"] = {"data": reg["deferimento"], "tipo": "registro", "processo": None}
        achada["registrado"] = reg
    vivas_sem_registro = [e["nomes"][-1]["sigla"] for e in ents if e["vivo"] and "registrado" not in e]
    if vivas_sem_registro:
        log.warning("Entidades vivas fora da lista de registrados: %s", vivas_sem_registro)

    # ids estáveis: sigla final + ano de fim/início para desambiguar siglas repetidas (PSD, PL, PP…)
    usados: dict[str, int] = {}
    for e in ents:
        base = re.sub(r"[^a-z0-9]", "", _sem_acento(e["nomes"][-1]["sigla"]).lower()) or "p"
        sufixo = ""
        if usados.get(base):
            sufixo = str((e["fim"] or e["inicio"] or {}).get("data", "x")[:4])
        usados[base] = usados.get(base, 0) + 1
        e["id"] = base + sufixo
    mapa = {id(e): e["id"] for e in ents}
    for a in arestas:
        a["de"], a["para"] = mapa[a["de"]], mapa[a["para"]]
    nos = []
    for e in ents:
        nos.append({
            "id": e["id"], "vivo": e["vivo"], "nomes": e["nomes"], "inicio": e["inicio"], "fim": e["fim"],
            "registro_tse": e["registrado"]["deferimento"] if "registrado" in e else None,
        })
    return {"nos": nos, "arestas": arestas, "conferencia": conferencia, "sem_evento": sem_evento}


def _familias(nos: list[dict], arestas: list[dict]) -> list[list[str]]:
    pai = {n["id"]: n["id"] for n in nos}

    def raiz(x: str) -> str:
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x
    for a in arestas:
        pai[raiz(a["de"])] = raiz(a["para"])
    grupos: dict[str, list[str]] = {}
    for n in nos:
        grupos.setdefault(raiz(n["id"]), []).append(n["id"])
    return list(grupos.values())


def evolucao() -> dict:
    def produzir() -> dict:
        f = _ler_tse()
        g = _construir(f)
        por_id = {n["id"]: n for n in g["nos"]}
        fams = []
        for ids in _familias(g["nos"], g["arestas"]):
            vivos = sorted((por_id[i] for i in ids if por_id[i]["vivo"]), key=lambda n: n["nomes"][-1]["sigla"].casefold())
            ids_o = sorted(ids, key=lambda i: ((por_id[i]["fim"] or {}).get("data") or "9999", por_id[i]["nomes"][-1]["sigla"].casefold()))
            fams.append({"ids": ids_o, "rotulo": " / ".join(n["nomes"][-1]["sigla"] for n in vivos)})
        fams.sort(key=lambda x: x["rotulo"].casefold())
        return {"nos": g["nos"], "arestas": g["arestas"], "familias": fams, "conferencia": g["conferencia"],
                "n_registrados": len(f["registrados"])}
    base = _memo("evolucao", produzir)
    return {
        "consultado_em": _agora(), **base,
        "fonte": {"nome": "TSE — Partidos políticos registrados no TSE (lista, fusões, incorporações e mudanças de nome/sigla)",
                  "url": URL_TSE},
        "lacunas": [
            "O Portal de Dados Abertos do TSE não publica conjunto de dados de registro, fusão ou mudança de nome de partidos; a única fonte oficial estruturada é a página acima, lida aqui diretamente.",
            "A relação de fusões, incorporações e mudanças vale a partir da Lei 9.096/1995. Fusões e mudanças anteriores, e partidos extintos antes disso, não constam nela.",
            "O TSE não informa a data de registro dos partidos já extintos nem a de partidos criados antes de 1995 que mudaram de nome: nesses casos a barra começa tracejada, sem data inventada.",
            "Federações partidárias (união temporária de partidos para eleições, Lei 14.208/2021) não são fusões e não aparecem nesta tela.",
        ],
        "notas": [
            "Cada barra é um registro de partido no TSE; quando muda de nome ou sigla, a barra continua e o novo nome é marcado.",
            "Linhas que ligam barras são fusões ou incorporações deferidas pelo TSE, com processo e data da decisão.",
            "O atual PSD (registrado em 2011) não tem relação com o PSD extinto em 2003, incorporado ao PTB — nota do próprio TSE.",
        ],
    }


# --------------------------------------------------------------------------- #
# Módulo 2: afinidade de votação
# --------------------------------------------------------------------------- #
ARQS_VOTACOES = [RAIZ / "dados" / "votacoes" / "alinhamento_camara_completo.json"]
MIN_VOTOS_MAPA = 100        # votos Sim/Não em votações com orientação do Governo, no ano, para o partido entrar no critério de pragmatismo
MIN_VOTACOES_PAR = 20       # votações em comum para a afinidade de um par valer
MIN_VOTACOES_PARTIDO = 50   # votações nominais no ano (em algum par) para o partido entrar no mapa
LIMIAR_ALINHA = 60.0        # % de votos iguais à orientação do Governo
FRACAO_ANOS = 0.8           # fração dos anos avaliáveis que precisam atingir o limiar
MIN_GOVERNOS = 3            # em quantos governos distintos o padrão precisa aparecer

# Presidente em exercício na maior parte de cada ano (fato público). 2016 é de transição e fica fora da contagem de governos.
GOVERNO_DO_ANO = {2010: "Lula", 2011: "Dilma", 2012: "Dilma", 2013: "Dilma", 2014: "Dilma", 2015: "Dilma", 2016: None,
                  2017: "Temer", 2018: "Temer", 2019: "Bolsonaro", 2020: "Bolsonaro", 2021: "Bolsonaro", 2022: "Bolsonaro"}
_votos_cache: dict[str, Any] = {"mtimes": None, "anos": None}


def _anos_votacao() -> dict[int, dict]:
    mts = []
    for a in ARQS_VOTACOES:
        try:
            mts.append(a.stat().st_mtime)
        except OSError:
            mts.append(None)
    with _trava:
        if _votos_cache["mtimes"] != mts:
            anos: dict[int, dict] = {}
            for a in ARQS_VOTACOES:
                if a.exists():
                    for k, v in json.loads(a.read_text(encoding="utf-8"))["anos"].items():
                        anos[int(k)] = v
            _votos_cache.update(mtimes=mts, anos=anos)
        if not _votos_cache["anos"]:
            raise ErroFonte("Votações nominais ainda não coletadas (rode backend/coletor_votacoes.py).")
        return _votos_cache["anos"]


def _eigen2(B: list[list[float]]) -> list[tuple[float, list[float]]]:
    """Dois maiores autovalores/vetores de matriz simétrica (iteração de potência com deflação)."""
    n = len(B)
    # desloca o espectro (soma c·I) para que o maior autovalor POSITIVO seja também o de maior módulo
    c = max(sum(abs(x) for x in r) for r in B)
    M = [[B[i][j] + (c if i == j else 0.0) for j in range(n)] for i in range(n)]
    res = []
    for k in range(2):
        v = [1.0 / math.sqrt(n) + 0.01 * ((i * 7 + k * 3) % 5) for i in range(n)]
        lam = 0.0
        for _ in range(600):
            w = [sum(M[i][j] * v[j] for j in range(n)) for i in range(n)]
            norma = math.sqrt(sum(x * x for x in w))
            if norma < 1e-12:
                break
            w = [x / norma for x in w]
            if sum(abs(a - b) for a, b in zip(w, v)) < 1e-12:
                v = w
                break
            v = w
        lam = sum(v[i] * sum(M[i][j] * v[j] for j in range(n)) for i in range(n))
        res.append((lam - c, v))
        M = [[M[i][j] - lam * v[i] * v[j] for j in range(n)] for i in range(n)]
    return res


def _mds(siglas: list[str], dist: dict[tuple[str, str], float]) -> dict[str, tuple[float, float]]:
    n = len(siglas)
    D2 = [[(0.0 if i == j else dist[tuple(sorted((siglas[i], siglas[j])))] ** 2) for j in range(n)] for i in range(n)]
    lin = [sum(r) / n for r in D2]
    tot = sum(lin) / n
    B = [[-0.5 * (D2[i][j] - lin[i] - lin[j] + tot) for j in range(n)] for i in range(n)]
    (l1, v1), (l2, v2) = _eigen2(B)
    return {s: (v1[i] * math.sqrt(max(l1, 0)), v2[i] * math.sqrt(max(l2, 0))) for i, s in enumerate(siglas)}


def _alinhar(pts: dict[str, tuple[float, float]], ref: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """Procrustes ortogonal (rotação ou reflexão, sem mudar escala) para manter os eixos comparáveis entre anos."""
    comuns = [s for s in pts if s in ref]
    if len(comuns) < 3:
        return pts
    cp = (sum(pts[s][0] for s in comuns) / len(comuns), sum(pts[s][1] for s in comuns) / len(comuns))
    cr = (sum(ref[s][0] for s in comuns) / len(comuns), sum(ref[s][1] for s in comuns) / len(comuns))
    melhor = None
    for refl in (1, -1):
        a = b = 0.0
        for s in comuns:
            x, y = pts[s][0] - cp[0], (pts[s][1] - cp[1]) * refl
            u, w = ref[s][0] - cr[0], ref[s][1] - cr[1]
            a += x * u + y * w
            b += x * w - y * u
        th = math.atan2(b, a)
        c, sn = math.cos(th), math.sin(th)
        erro = 0.0
        for s in comuns:
            x, y = pts[s][0] - cp[0], (pts[s][1] - cp[1]) * refl
            erro += (c * x - sn * y + cr[0] - ref[s][0]) ** 2 + (sn * x + c * y + cr[1] - ref[s][1]) ** 2
        if melhor is None or erro < melhor[0]:
            melhor = (erro, refl, c, sn)
    _, refl, c, sn = melhor
    return {s: (c * (p[0] - cp[0]) - sn * (p[1] - cp[1]) * refl + cr[0], sn * (p[0] - cp[0]) + c * (p[1] - cp[1]) * refl + cr[1])
            for s, p in pts.items()}


def _resolvedor() -> "callable":
    """sigla+ano → id do registro no TSE (módulo 1), para tratar PMDB→MDB, PRB→REPUBLICANOS etc. como o mesmo partido.
    Sem a fonte do TSE, cai para a própria sigla."""
    try:
        nos = evolucao()["nos"]
    except Exception as exc:  # noqa: BLE001
        log.warning("Sem histórico do TSE para unir siglas: %s", exc)
        return lambda sigla, ano: sigla
    tabela: dict[str, list[tuple[str, str, str]]] = {}
    for n in nos:
        for i, nm in enumerate(n["nomes"]):
            ini = nm["desde"] or (n["inicio"] or {}).get("data") or "0000-01-01"
            fim = n["nomes"][i + 1]["desde"] if i + 1 < len(n["nomes"]) else ((n["fim"] or {}).get("data") or "9999-12-31")
            tabela.setdefault(_chave(nm["sigla"]), []).append((ini, fim, n["id"]))

    def resolver(sigla: str, ano: int) -> str:
        meio = f"{ano}-07-01"
        cand = tabela.get(_chave(sigla), [])
        for ini, fim, i in cand:
            if ini <= meio < fim:
                return i
        # a Câmara costuma adotar a sigla nova/antiga fora do dia exato da decisão do TSE: vale o registro mais próximo no tempo
        if cand:
            return min(cand, key=lambda c: 0 if c[0] <= meio < c[1] else min(abs(int(c[0][:4]) - ano), abs(int(c[1][:4]) - ano)))[2]
        return sigla
    return resolver


def _mapa() -> dict:
    anos = _anos_votacao()
    lista = sorted(anos)
    quadros: dict[int, dict] = {}
    ref = None
    for ano in lista:
        a = anos[ano]
        maxn: dict[str, int] = {}
        for k, (c, n) in a["pares_todas"].items():
            for s in k.split("|"):
                maxn[s] = max(maxn.get(s, 0), n)
        ok = [s for s, n in maxn.items() if n >= MIN_VOTACOES_PARTIDO and s.upper() not in ("S.PART.", "SEM PARTIDO")]
        agree: dict[tuple[str, str], float] = {}
        for k, (c, n) in a["pares_todas"].items():
            x, y = k.split("|")
            if n >= MIN_VOTACOES_PAR and x in ok and y in ok:
                agree[tuple(sorted((x, y)))] = c / n
        # só entra quem tem afinidade medida com a maioria dos demais
        ok = [s for s in ok if sum(1 for o in ok if o != s and tuple(sorted((s, o))) in agree) >= 0.6 * (len(ok) - 1)]
        if len(ok) < 4:
            continue
        medio = {s: sum(v for k, v in agree.items() if s in k) / max(1, sum(1 for k in agree if s in k)) for s in ok}
        dist = {}
        for i, x in enumerate(ok):
            for y in ok[i + 1:]:
                k = tuple(sorted((x, y)))
                dist[k] = 1 - agree[k] if k in agree else 1 - (medio[x] + medio[y]) / 2
        pts = _mds(ok, dist)
        if ref is None:
            # convenção de orientação do 1º ano: PT no lado esquerdo e MDB acima — só fixa o sinal dos eixos
            ancora = {s: p for s, p in pts.items()}
            if "PT" in ancora and ancora["PT"][0] > 0:
                pts = {s: (-p[0], p[1]) for s, p in pts.items()}
            m = "PMDB" if "PMDB" in pts else "MDB"
            if m in pts and pts[m][1] < 0:
                pts = {s: (p[0], -p[1]) for s, p in pts.items()}
        else:
            pts = _alinhar(pts, ref)
        ref = {**(ref or {}), **pts}
        quadros[ano] = {"pts": pts, "votacoes": a["votacoes_todas"], "n": {s: maxn[s] for s in pts}}
    if not quadros:
        raise ErroFonte("Dados de votação insuficientes para o mapa.")
    m = max(abs(c) for q in quadros.values() for p in q["pts"].values() for c in p) or 1.0
    return {"quadros": quadros, "escala": m}


def _pragmaticos(anos: dict[int, dict], resolver) -> dict[str, dict]:  # noqa: ANN001
    """Critério objetivo e público: apoia as orientações de governos distintos. Avaliado por registro no TSE (siglas do
    mesmo registro somam), contando cada ano uma vez."""
    votos: dict[str, dict[int, list[int]]] = {}
    for ano, a in anos.items():
        for s, t in a["partidos"].items():
            if s.upper() in ("S.PART.", "SEM PARTIDO"):
                continue
            acc = votos.setdefault(resolver(s, ano), {}).setdefault(ano, [0, 0])
            acc[0] += t["gov_ok"]
            acc[1] += t["gov_n"]
    gov_ano = {**GOVERNO_DO_ANO, **{a: "Lula" for a in anos if a >= 2023}}
    res = {}
    for ent, por_ano in votos.items():
        h = {ano: 100 * ok / n for ano, (ok, n) in por_ano.items() if n >= MIN_VOTOS_MAPA}
        if not h:
            continue
        governos = sorted({gov_ano[a] for a in h if gov_ano.get(a)})
        acima = sum(1 for v in h.values() if v >= LIMIAR_ALINHA)
        res[ent] = {
            "anos_avaliados": len(h), "anos_acima": acima, "governos": governos, "minimo": round(min(h.values()), 1),
            "pragmatico": len(governos) >= MIN_GOVERNOS and acima >= FRACAO_ANOS * len(h),
        }
    return res


def _alinh(a: dict, s: str) -> float | None:
    t = a["partidos"].get(s)
    return round(100 * t["gov_ok"] / t["gov_n"], 1) if t and t["gov_n"] >= MIN_VOTOS_MAPA else None


def escala() -> dict:
    chave = "mapa_" + str(sum(x for x in (_votos_cache.get("mtimes") or []) if x))
    base = _memo(chave, _mapa)
    anos = _anos_votacao()
    resolver = _resolvedor()
    prag = _pragmaticos(anos, resolver)
    escala_ = base["escala"]
    quadros = []
    for ano in sorted(base["quadros"]):
        q = base["quadros"][ano]
        por_ent: dict[str, dict] = {}
        for s, p in q["pts"].items():
            ent = resolver(s, ano)
            if ent in por_ent and q["n"][por_ent[ent]["sigla"]] >= q["n"][s]:
                continue   # duas siglas do mesmo registro no mesmo ano (ex.: PMDB e MDB em 2018): fica a de mais votações
            por_ent[ent] = {"sigla": s, "entidade": ent, "x": round(p[0] / escala_, 4), "y": round(p[1] / escala_, 4),
                            "alinhamento_governo": _alinh(anos[ano], s), "pragmatico": prag.get(ent, {}).get("pragmatico", False)}
        quadros.append({"ano": ano, "votacoes": q["votacoes"], "partidos": sorted(por_ent.values(), key=lambda x: x["sigla"].casefold())})
    ultima: dict[str, str] = {}
    for q in quadros:
        for p in q["partidos"]:
            ultima[p["entidade"]] = p["sigla"]
    detalhe = {ultima[e]: dict(v, entidade=e) for e, v in prag.items() if e in ultima}
    siglas_prag = sorted((s for s, v in detalhe.items() if v["pragmatico"]), key=str.casefold)
    return {
        "consultado_em": _agora(),
        "anos": [q["ano"] for q in quadros], "quadros": quadros,
        "pragmatismo": {
            "criterio": (f"Perfil pragmático = nos anos em que o partido teve ao menos {MIN_VOTOS_MAPA} votos válidos em votações "
                         f"com orientação do Governo, votou de acordo com essa orientação em pelo menos {LIMIAR_ALINHA:.0f}% dos votos em "
                         f"{FRACAO_ANOS * 100:.0f}% ou mais desses anos, e isso ocorreu sob pelo menos {MIN_GOVERNOS} governos distintos "
                         "(Lula, Dilma, Temer, Bolsonaro; 2016, ano de transição, não conta como governo). "
                         "É um critério sobre comportamento observado, não um juízo sobre o partido."),
            "partidos": {s: detalhe[s] for s in sorted(detalhe, key=str.casefold)},
            "classificados": siglas_prag,
            "texto_card": ("Partido de perfil pragmático. Sua posição no espectro varia de acordo com alianças regionais e participação "
                           "no governo federal, priorizando o controle de recursos públicos em vez de pautas ideológicas rígidas."),
        },
        "fonte": {"nome": "Câmara dos Deputados — votações nominais e orientações de bancada (dados abertos)",
                  "url": "https://dadosabertos.camara.leg.br/swagger/api.html"},
        "metodo": [
            "Para cada ano, medimos a semelhança entre cada par de partidos: em quantas votações nominais a posição da maioria dos dois (Sim ou Não) coincidiu.",
            "A distância entre dois partidos é 100% menos essa semelhança. Um escalonamento multidimensional clássico (MDS) transforma as distâncias em pontos em 2 dimensões: partidos que votam parecido ficam perto.",
            "Para os anos poderem ser comparados, cada mapa é girado/refletido (sem mudar distâncias) para se encaixar no do ano anterior, nos partidos que aparecem em ambos. No 1º ano, o sinal dos eixos segue uma convenção: PT à esquerda e MDB acima — isso não classifica ninguém.",
            f"Entram todas as votações nominais do ano em que cada partido teve ao menos 3 votos Sim/Não (empates ficam de fora); só aparecem partidos com ao menos {MIN_VOTACOES_PARTIDO} votações no ano e afinidade medida com a maioria dos demais.",
            "A mesma sigla pode representar partidos diferentes ao longo do tempo (fusões, mudanças de nome — veja a evolução histórica); a conexão é só pela sigla vigente em cada ano.",
        ],
        "limitacoes": [
            "Isto NÃO é uma escala esquerda–direita. A primeira dimensão das votações da Câmara mede sobretudo governo × oposição, e muda de significado quando muda o governo. A segunda dimensão não tem nome.",
            "Só votações nominais entram (grande parte das decisões é simbólica), em votações escolhidas pelo plenário e pelo Governo; o comportamento varia com o tema.",
            "O TSE não classifica ideologia e a Câmara também não. Rótulos de esquerda/centro/direita seriam opinião e não são usados.",
            "Os anos mais recentes são parciais (o ano corrente ainda está em andamento) e a posição de cada ano depende dos demais partidos presentes.",
        ],
        "lacunas": [
            "Pesquisas de parlamentares (Brazilian Legislative Surveys, Power & Zucco): os microdados da última rodada não estão abertos de forma confirmada por nós, e as estimativas públicas de pontos ideais (Lauderdale & Zucco) vão só até 2011 — não usamos.",
            "Radar do Congresso (Congresso em Foco) e Basômetro (Estadão) são produtos próprios de cada veículo; não localizamos licença que permita reutilizar seus números. Usamos o mesmo tipo de insumo (votos nominais abertos), com método próprio e público.",
            "Antes de 2010 as votações nominais não foram coletadas nesta versão.",
        ],
    }


# --------------------------------------------------------------------------- #
# Bússola: posição segundo especialistas (V-Party, V-Dem)
# --------------------------------------------------------------------------- #
ARQ_VPARTY = RAIZ / "frontend" / "assets" / "vparty.json"


def bussola() -> dict:
    """Posições dos partidos em 2 eixos, da pesquisa de especialistas V-Party. Quem tem perfil pragmático (critério de
    votação) vem marcado: a tela o coloca no Círculo de Pragmatismo e mostra a posição dos especialistas só no card."""
    try:
        v = json.loads(ARQ_VPARTY.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ErroFonte("Dados do V-Party ainda não gerados (rode scripts/gerar_vparty.py).") from exc
    resolver = _resolvedor()
    prag = _pragmaticos(_anos_votacao(), resolver)
    try:
        nos = {n["id"]: n for n in evolucao()["nos"]}
    except Exception:  # noqa: BLE001
        nos = {}
    quadros, cobertos = [], set()
    for ano, lista in sorted(v["anos"].items(), key=lambda kv: int(kv[0])):
        itens = []
        for p in lista:
            ent = next((resolver(t.strip(), int(ano)) for t in reversed(p["sigla"].split("/")) if _chave(t) and
                        resolver(t.strip(), int(ano)) in nos), None) or resolver(p["sigla"].split("/")[-1].strip(), int(ano))
            cobertos.add(ent)
            sigla = nos[ent]["nomes"][-1]["sigla"] if ent in nos else p["sigla"]
            itens.append({"entidade": ent, "sigla": sigla, "sigla_fonte": p["sigla"], "eco": p["eco"], "eco_sd": p["eco_sd"],
                          "lgbt": p["lgbt"], "culsup": p["culsup"], "relig": p["relig"], "cultura": p["cultura"],
                          "pragmatico": prag.get(ent, {}).get("pragmatico", False)})
        quadros.append({"ano": int(ano), "partidos": sorted(itens, key=lambda x: x["sigla"].casefold())})
    sem = sorted((n["nomes"][-1]["sigla"] for n in nos.values() if n["vivo"] and n["id"] not in cobertos), key=str.casefold)
    return {
        "consultado_em": _agora(), "quadros": quadros, "sem_cobertura": sem,
        "pragmatismo": {"texto_card": ("Partido de perfil pragmático. Sua posição no espectro varia de acordo com alianças regionais e participação "
                                       "no governo federal, priorizando o controle de recursos públicos em vez de pautas ideológicas rígidas."),
                        "classificados": sorted({p["sigla"] for q in quadros[-1:] for p in q["partidos"] if p["pragmatico"]}, key=str.casefold)},
        "fonte": {"nome": v["fonte"], "url": v["doi"]},
        "metodo": [
            "Fonte: V-Party v2 (V-Dem), pesquisa em que especialistas em cada país avaliam a posição de cada partido na eleição; os valores são uma agregação estatística dessas avaliações, não uma opinião do ImpactoCidadão.",
            "Eixo horizontal (economia): escala oficial do V-Party de 0 (extrema-esquerda) a 6 (extrema-direita), onde esquerda = mais papel do Estado na economia (impostos, regulação, gasto, bem-estar) e direita = menos.",
            "Eixo vertical (costumes e identidade): média simples de três perguntas do V-Party, cada uma de 0 a 4 — igualdade social LGBT, rejeição à superioridade cultural de um grupo ou da nação, e não invocar religião para justificar posições. Perto de 0: perfil de tradição/identidade; perto de 4: perfil de liberdades individuais e secularismo. O ponto neutro é 2. Esta média é nossa composição; cada item aparece no card do partido.",
            "Cada posição vale para a eleição indicada (2002–2018). Mude o ano para ver a migração.",
        ],
        "limitacoes": [
            "A pesquisa cobre até a eleição de 2018 (versão 2 de fev/2022): partidos criados ou fundidos depois, e partidos pequenos que a pesquisa não codificou, aparecem em “Sem posição na fonte”.",
            "São percepções de especialistas sobre o partido como organização, não o voto de cada parlamentar nem o que o partido diz de si.",
            "Os valores têm margem de incerteza (desvio no card). Diferenças pequenas entre partidos próximos não são significativas.",
            "Siglas mudam: aparece a sigla mais recente do mesmo registro no TSE (ex.: PFL → DEM) e, no card, a sigla usada pela fonte.",
        ],
    }
