"""Resultados das eleições: votos por candidato, partido, estado e município; quociente eleitoral e vagas.

Lê o resultado do coletor (`coletor_tse.py` → dados/eleicoes/resultado_AAAA.json e dados/eleicoes/AAAA/cand_*.json);
não consulta o TSE em tempo de requisição. Só números oficiais, sem previsão, sem ranking de "melhores" e sem juízo político.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from config import RAIZ
from fontes import ErroFonte

PASTA = RAIZ / "dados" / "eleicoes"
LIMITE_CANDIDATOS = 60

_trava = threading.Lock()
_cache: dict[int, dict[str, Any]] = {}


def anos_disponiveis() -> list[int]:
    return sorted((int(m.group(1)) for p in PASTA.glob("resultado_*.json") if (m := re.fullmatch(r"resultado_(\d{4})\.json", p.name))), reverse=True)


def _dados(ano: int) -> dict:
    arq = PASTA / f"resultado_{ano}.json"
    try:
        mt = arq.stat().st_mtime
    except OSError as exc:
        raise ErroFonte(f"Resultados de {ano} ainda não coletados (rode backend/coletor_tse.py --ano {ano}).") from exc
    with _trava:
        c = _cache.get(ano)
        if not c or c["mtime"] != mt:
            c = _cache[ano] = {"mtime": mt, "dados": json.loads(arq.read_text(encoding="utf-8"))}
        return c["dados"]


def _cargo(ano: int, ck: str) -> dict:
    d = _dados(ano)
    cargo = d["cargos"].get(ck)
    if not cargo:
        raise KeyError(ck)
    return cargo


def _uf(ano: int, ck: str, uf: str) -> dict:
    u = _cargo(ano, ck)["ufs"].get(uf.upper())
    if not u:
        raise KeyError(uf)
    return u


def _fonte(d: dict) -> dict:
    return {"fonte": d["fonte"], "gerado_em": d["gerado_em"]}


def indice() -> dict:
    anos = anos_disponiveis()
    if not anos:
        raise ErroFonte("Resultados ainda não coletados (rode backend/coletor_tse.py).")
    saida = []
    for ano in anos:
        d = _dados(ano)
        saida.append({"ano": ano, "cargos": [{"id": ck, "nome": c["nome"], "turno": c["turno"], "proporcional": c["proporcional"]} for ck, c in d["cargos"].items()],
                      **_fonte(d)})
    return {"anos": saida}


def _lider(u: dict, proporcional: bool) -> dict | None:
    """Quem lidera o estado: proporcional = unidade (partido/federação) com mais votos; majoritário = candidato mais votado."""
    if proporcional:
        if not u["unidades"]:
            return None
        x = u["unidades"][0]
        base = u["totais"]["validos"] or 1
        return {"nome": x["nome"], "votos": x["total"], "pct": round(100 * x["total"] / base, 2), "cadeiras": x["cadeiras"]}
    if not u["candidatos"]:
        return None
    x = u["candidatos"][0]
    base = u["totais"]["validos"] or 1
    return {"nome": x[0], "partido": x[3], "votos": x[4], "pct": round(100 * x[4] / base, 2), "situacao": x[5]}


def mapa(ano: int, ck: str) -> dict:
    c = _cargo(ano, ck)
    ufs = {}
    for sigla, u in c["ufs"].items():
        t = u["totais"]
        ufs[sigla] = {"nome": u["nome"], "validos": t["validos"], "nominais": t["nominais"], "legenda": t["legenda"], "brancos": t["brancos"], "nulos": t["nulos"],
                      "comparecimento": t["comparecimento"], "aptos": t["aptos"], "cadeiras": u["cadeiras"], "qe": u["qe"], "lider": _lider(u, c["proporcional"])}
    return {"ano": ano, "cargo": {"id": ck, "nome": c["nome"], "turno": c["turno"], "proporcional": c["proporcional"]}, "ufs": ufs, **_fonte(_dados(ano))}


def detalhe_uf(ano: int, ck: str, uf: str, q: str | None = None, partido: str | None = None, deslocamento: int = 0) -> dict:
    c = _cargo(ano, ck)
    u = _uf(ano, ck, uf)
    t = u["totais"]
    base = t["validos"] or 1
    cands = u["candidatos"]
    if partido:
        cands = [x for x in cands if x[2] == partido or x[3] == partido]
    if q:
        norm = lambda s: re.sub(r"[^a-z0-9 ]", "", __import__("unicodedata").normalize("NFD", s or "").lower().encode("ascii", "ignore").decode())  # noqa: E731
        termos = norm(q).split()
        cands = [x for x in cands if all(tm in norm(x[0]) or tm == str(x[1]) for tm in termos)]
    pagina = cands[deslocamento:deslocamento + LIMITE_CANDIDATOS]
    unidades = [{**x, "pct": round(100 * x["total"] / base, 2),
                 "sobras_calc": None} for x in u["unidades"]]
    return {
        "ano": ano, "cargo": {"id": ck, "nome": c["nome"], "turno": c["turno"], "proporcional": c["proporcional"]},
        "uf": uf.upper(), "nome": u["nome"], "totais": t, "cadeiras": u["cadeiras"], "qe": u["qe"],
        "pct_legenda": round(100 * t["legenda"] / base, 2) if t["validos"] else 0,
        "unidades": unidades,
        "candidatos": [{"nome": x[0], "numero": x[1], "unidade": x[2], "partido": x[3], "votos": x[4], "situacao": x[5], "id": x[6],
                        "pct": round(100 * x[4] / base, 2)} for x in pagina],
        "total_candidatos": len(cands), "limite": LIMITE_CANDIDATOS,
        **_fonte(_dados(ano)),
    }


def municipios(ano: int, ck: str, uf: str) -> dict:
    c = _cargo(ano, ck)
    u = _uf(ano, ck, uf)
    saida = {}
    for cod, m in u["municipios"].items():
        item = {"nome": m["n"], "validos": m["v"]}
        if c["proporcional"]:
            unid = m.get("u") or {}
            item["u"] = unid
            if unid:
                lider = max(unid.items(), key=lambda kv: kv[1])
                item["lider"] = {"unidade": lider[0], "votos": lider[1], "pct": round(100 * lider[1] / m["v"], 2) if m["v"] else 0}
        saida[cod] = item
    return {"ano": ano, "cargo": ck, "uf": uf.upper(), "municipios": saida, **_fonte(_dados(ano))}


def candidato(ano: int, ck: str, uf: str, sq: str) -> dict:
    """Votos de um candidato por município (só candidatos eleitos ou com votação mínima nas proporcionais)."""
    u = _uf(ano, ck, uf)
    arq = PASTA / str(ano) / f"cand_{ck}_{uf.upper()}.json"
    if not arq.exists():
        raise ErroFonte("Detalhe municipal ainda não coletado.")
    det = json.loads(arq.read_text(encoding="utf-8"))
    info = next((x for x in u["candidatos"] if x[6] == sq), None)
    if not info:
        raise KeyError(sq)
    mun = det.get(sq)
    nomes = u["municipios"]
    return {"candidato": {"nome": info[0], "numero": info[1], "unidade": info[2], "partido": info[3], "votos": info[4], "situacao": info[5]},
            "municipios": None if mun is None else [{"cod": cod, "nome": nomes.get(cod, {}).get("n", cod), "votos": v, "validos": nomes.get(cod, {}).get("v", 0)}
                                                   for cod, v in sorted(mun.items(), key=lambda kv: -kv[1])],
            "sem_detalhe": mun is None, "limite_detalhe": _dados(ano).get("limite_detalhe"), **_fonte(_dados(ano))}
