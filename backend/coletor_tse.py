"""Coletor de resultados das eleições (TSE, dados abertos) — votos por candidato, por partido, por estado e por município.

Fonte oficial (um conjunto por ano; CSV dentro de ZIP, UTF-8 não: latin-1):
  https://cdn.tse.jus.br/estatistica/sead/odsele/detalhe_votacao_munzona/detalhe_votacao_munzona_AAAA.zip
  https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_partido_munzona/votacao_partido_munzona_AAAA.zip
  https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_candidato_munzona/votacao_candidato_munzona_AAAA.zip
  (catálogo: https://dadosabertos.tse.jus.br/)

Os arquivos são grandes (o de candidatos passa de 600 MB), então o trabalho é feito AQUI, fora da API: o resultado
(compacto) vai para dados/eleicoes/resultado_AAAA.json (resumo, partidos e candidatos por estado/município) e para
dados/eleicoes/AAAA/cand_<cargo>_<UF>.json (votos dos candidatos por município), que a API só lê.

Regras:
  * cada arquivo "BRASIL.csv" já reúne todos os estados e cargos; usamos só ele (os por-UF repetiriam as linhas);
  * "votos válidos" = nominais válidos + de legenda válidos (brancos e nulos ficam de fora), como na regra do quociente;
  * nas eleições proporcionais a unidade que disputa é o partido isolado ou a federação (2022); em 2018 e antes,
    partido isolado ou coligação. O quociente partidário é calculado por essa unidade;
  * quociente eleitoral = votos válidos ÷ cadeiras (fração até 0,5 é desprezada, acima de 0,5 arredonda para cima);
  * cadeiras, "eleito por QP" e "eleito por média" vêm da situação oficial de cada candidato (DS_SIT_TOT_TURNO).

Uso (a partir da raiz do projeto):
    python backend/coletor_tse.py --ano 2022
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import RAIZ, cfg  # noqa: E402

log = logging.getLogger("impacto.tse")

BASE = "https://cdn.tse.jus.br/estatistica/sead/odsele"
CARGOS = {"1": "Presidente", "3": "Governador", "5": "Senador", "6": "Deputado Federal", "7": "Deputado Estadual", "8": "Deputado Distrital"}
PROPORCIONAIS = {"6", "7", "8"}
SAIDA = RAIZ / "dados" / "eleicoes"
LIMITE_DETALHE = 1500   # candidatos proporcionais com menos votos que isso (e não eleitos) não têm detalhe municipal

csv.field_size_limit(10_000_000)


def _baixar(nome: str, ano: int, destino: Path) -> Path:
    existente = destino / f"{nome}_{ano}.zip"
    if existente.exists():          # --pasta: reaproveita os arquivos já baixados
        return existente
    url = f"{BASE}/{nome}/{nome}_{ano}.zip"
    arq = destino / f"{nome}_{ano}.zip"
    log.info("Baixando %s", url)
    with httpx.stream("GET", url, headers={"User-Agent": cfg.user_agent or "ImpactoCidadao/1.0 (dados abertos do TSE)"}, timeout=900, follow_redirects=True) as r:
        r.raise_for_status()
        with arq.open("wb") as f:
            for bloco in r.iter_bytes(1 << 20):
                f.write(bloco)
    return arq


def _linhas(zip_path: Path, nome: str):
    """Linhas do CSV `<nome>_BRASIL.csv` dentro do ZIP (todas as UFs e cargos)."""
    with zipfile.ZipFile(zip_path) as zf:
        alvo = next(n for n in zf.namelist() if n.endswith("_BRASIL.csv") and n.startswith(nome))
        with zf.open(alvo) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="latin-1", newline=""), delimiter=";")


def _num(v: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _unidade(r: dict) -> tuple[str, str, list[str]]:
    """(id, nome, partidos) de quem disputa: federação, coligação ou partido isolado."""
    fed = (r.get("SG_FEDERACAO") or "").strip()
    if fed and fed != "#NULO":
        return fed, fed, [p.strip() for p in (r.get("DS_COMPOSICAO_FEDERACAO") or "").split("/") if p.strip()] or [r["SG_PARTIDO"]]
    if (r.get("TP_AGREMIACAO") or "").upper().startswith("COLIGA"):
        nome = (r.get("NM_COLIGACAO") or "").strip()
        comp = [p.strip() for p in (r.get("DS_COMPOSICAO_COLIGACAO") or "").split("/") if p.strip()]
        return nome or r["SG_PARTIDO"], nome or r["SG_PARTIDO"], comp or [r["SG_PARTIDO"]]
    return r["SG_PARTIDO"], r["SG_PARTIDO"], [r["SG_PARTIDO"]]


def quociente_eleitoral(validos: int, cadeiras: int) -> int:
    if not cadeiras:
        return 0
    q = validos / cadeiras
    return int(q) + (1 if q - int(q) > 0.5 else 0)


def coletar(ano: int, pasta: Path) -> dict:
    z_det = _baixar("detalhe_votacao_munzona", ano, pasta)
    z_par = _baixar("votacao_partido_munzona", ano, pasta)
    z_can = _baixar("votacao_candidato_munzona", ano, pasta)

    # 1) totais por (cargo-turno, UF) e por município
    totais: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))
    muni_total: dict[tuple, dict] = defaultdict(dict)       # (ck, uf) -> cod -> [nome, validos]
    nomes_uf: dict[str, str] = {}
    for r in _linhas(z_det, "detalhe_votacao_munzona"):
        if r["CD_CARGO"] not in CARGOS:
            continue
        ck, uf = f"{r['CD_CARGO']}-{r['NR_TURNO']}", r["SG_UF"]
        nomes_uf[uf] = r["NM_UE"] if r["SG_UE"] == uf else nomes_uf.get(uf, uf)
        t = totais[(ck, uf)]
        for campo, col in (("aptos", "QT_APTOS"), ("comparecimento", "QT_COMPARECIMENTO"), ("validos", "QT_TOTAL_VOTOS_VALIDOS"),
                           ("nominais", "QT_VOTOS_NOMINAIS_VALIDOS"), ("legenda", "QT_TOTAL_VOTOS_LEG_VALIDOS"),
                           ("brancos", "QT_VOTOS_BRANCOS"), ("nulos", "QT_TOTAL_VOTOS_NULOS")):
            t[campo] += _num(r[col])
        m = muni_total[(ck, uf)].setdefault(r["CD_MUNICIPIO"], [r["NM_MUNICIPIO"], 0])
        m[1] += _num(r["QT_TOTAL_VOTOS_VALIDOS"])
    log.info("Detalhe: %d combinações cargo-turno/UF", len(totais))

    # 2) candidatos: votos totais, por município e situação
    cands: dict[tuple, dict] = {}
    cand_mun: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unidades: dict[tuple, dict[str, dict]] = defaultdict(dict)
    uni_mun: dict[tuple, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for r in _linhas(z_can, "votacao_candidato_munzona"):
        if r["CD_CARGO"] not in CARGOS:
            continue
        ck, uf, sq = f"{r['CD_CARGO']}-{r['NR_TURNO']}", r["SG_UF"], r["SQ_CANDIDATO"]
        votos = _num(r["QT_VOTOS_NOMINAIS_VALIDOS"])
        uid, unome, partidos = _unidade(r)
        c = cands.get((ck, uf, sq))
        if c is None:
            c = cands[(ck, uf, sq)] = {"n": r["NM_URNA_CANDIDATO"] if r["NM_URNA_CANDIDATO"] not in ("#NULO", "#NE") else r["NM_CANDIDATO"],
                                       "num": r["NR_CANDIDATO"], "p": r["SG_PARTIDO"], "u": uid, "v": 0, "s": r["DS_SIT_TOT_TURNO"]}
        c["v"] += votos
        cand_mun[(ck, uf, sq)][r["CD_MUNICIPIO"]] += votos
        un = unidades[(ck, uf)].setdefault(uid, {"id": uid, "nome": unome, "partidos": partidos, "nominais": 0, "legenda": 0})
        un["nominais"] += votos
        uni_mun[(ck, uf)][r["CD_MUNICIPIO"]][uid] += votos
    log.info("Candidatos: %d", len(cands))

    # 3) votos de legenda por unidade (arquivo de partidos)
    for r in _linhas(z_par, "votacao_partido_munzona"):
        if r["CD_CARGO"] not in PROPORCIONAIS:
            continue
        ck, uf = f"{r['CD_CARGO']}-{r['NR_TURNO']}", r["SG_UF"]
        uid, unome, partidos = _unidade(r)
        un = unidades[(ck, uf)].setdefault(uid, {"id": uid, "nome": unome, "partidos": partidos, "nominais": 0, "legenda": 0})
        leg = _num(r["QT_VOTOS_LEGENDA_VALIDOS"])
        un["legenda"] += leg
        uni_mun[(ck, uf)][r["CD_MUNICIPIO"]][uid] += leg

    # 4) monta o resultado
    resultado: dict = {}
    detalhes: dict[tuple, dict] = {}
    for (ck, uf), t in sorted(totais.items()):
        cargo = ck.split("-")[0]
        proporcional = cargo in PROPORCIONAIS
        lista = [(sq, c) for (k, u, sq), c in cands.items() if k == ck and u == uf]
        eleitos = [c for _, c in lista if (c["s"] or "").upper().startswith("ELEITO")]
        cadeiras = len(eleitos)
        qe = quociente_eleitoral(t["validos"], cadeiras) if proporcional else 0
        por_unidade = unidades.get((ck, uf), {})
        saida_unid = []
        if proporcional:
            for uid, un in por_unidade.items():
                total = un["nominais"] + un["legenda"]
                mine = [c for c in eleitos if c["u"] == uid]
                por_qp = sum(1 for c in mine if "QP" in (c["s"] or "").upper())
                por_media = sum(1 for c in mine if "QP" not in (c["s"] or "").upper() and ("MÉDIA" in (c["s"] or "").upper() or "MEDIA" in (c["s"] or "").upper()))
                saida_unid.append({**un, "total": total, "qp_calc": (total // qe) if qe else 0,
                                   "cadeiras": len(mine), "por_qp": por_qp, "por_media": por_media})
            saida_unid.sort(key=lambda x: -x["total"])
        munis = {}
        for cod, nm in muni_total[(ck, uf)].items():
            entrada = {"n": nm[0], "v": nm[1]}
            if proporcional:
                entrada["u"] = {k: v for k, v in uni_mun[(ck, uf)].get(cod, {}).items() if v}
            munis[cod] = entrada
        cands_saida = sorted(([c["n"], c["num"], c["u"], c["p"], c["v"], c["s"], sq] for sq, c in lista), key=lambda x: -x[4])
        resultado.setdefault(ck, {"nome": CARGOS[cargo], "turno": int(ck.split("-")[1]), "proporcional": proporcional, "ufs": {}})["ufs"][uf] = {
            "nome": nomes_uf.get(uf, uf), "totais": dict(t), "cadeiras": cadeiras, "qe": qe,
            "unidades": saida_unid, "candidatos": cands_saida, "municipios": munis}
        # detalhe municipal dos candidatos (os proporcionais pouco votados e não eleitos ficam de fora)
        det = {}
        for sq, c in lista:
            if proporcional and c["v"] < LIMITE_DETALHE and not (c["s"] or "").upper().startswith("ELEITO"):
                continue
            det[sq] = {k: v for k, v in cand_mun[(ck, uf, sq)].items() if v}
        detalhes[(ck, uf)] = det
    return {"ano": ano, "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fonte": {"nome": "TSE — Dados Abertos (votação por município e zona)", "url": "https://dadosabertos.tse.jus.br/"},
            "limite_detalhe": LIMITE_DETALHE, "cargos": resultado}, detalhes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ano", type=int, required=True)
    ap.add_argument("--pasta", type=Path, help="pasta com os ZIPs já baixados (evita baixar de novo)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.pasta:
        dados, detalhes = coletar(a.ano, a.pasta)
    else:
        with tempfile.TemporaryDirectory(prefix="tse_") as tmp:
            dados, detalhes = coletar(a.ano, Path(tmp))
    SAIDA.mkdir(parents=True, exist_ok=True)
    (SAIDA / f"resultado_{a.ano}.json").write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    pasta = SAIDA / str(a.ano)
    pasta.mkdir(exist_ok=True)
    for (ck, uf), det in detalhes.items():
        (pasta / f"cand_{ck}_{uf}.json").write_text(json.dumps(det, separators=(",", ":")), encoding="utf-8")
    log.info("Gravado em %s", SAIDA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
