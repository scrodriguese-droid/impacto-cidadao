"""Coletor de alinhamento de votação dos partidos na Câmara — quem vota com o Governo e quem vota junto.

Fonte: arquivos abertos oficiais da Câmara dos Deputados (um conjunto por ano):
  https://dadosabertos.camara.leg.br/arquivos/votacoesOrientacoes/csv/votacoesOrientacoes-AAAA.csv
  https://dadosabertos.camara.leg.br/arquivos/votacoesVotos/csv/votacoesVotos-AAAA.csv

Não há rótulo ideológico aqui (a Câmara não classifica partidos e qualquer rótulo seria opinião). O que se mede
é comportamento em votações nominais, e as regras ficam declaradas na tela:
  * só entram votações com orientação do Governo "Sim" ou "Não"; só votos "Sim" e "Não" contam;
  * alinhamento com o Governo = votos do partido iguais à orientação do Governo ÷ votos do partido (Sim/Não);
  * coesão = em cada votação, |sim − não| ÷ (sim + não) entre os votos do partido (100% = todos iguais),
    média das votações em que o partido teve ao menos 3 votos;
  * afinidade entre dois partidos = % das votações em que a posição da maioria de cada um coincide
    (empates e votações com menos de 3 votos de algum dos dois ficam de fora).

Os arquivos são grandes (~60 MB por ano), por isso o trabalho é feito AQUI e a API só lê o resultado:
dados/votacoes/alinhamento_camara.json.

Uso (a partir da raiz do projeto):
    python backend/coletor_votacoes.py                 # de 2023 (legislatura atual) até o ano corrente
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import logging
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import RAIZ, cfg  # noqa: E402

log = logging.getLogger("impacto.votacoes")

BASE = "https://dadosabertos.camara.leg.br/arquivos"
SAIDA = RAIZ / "dados" / "votacoes" / "alinhamento_camara.json"
MIN_VOTOS = 3
csv.field_size_limit(10_000_000)


def _baixar(nome: str, ano: int, destino: Path) -> Path:
    url = f"{BASE}/{nome}/csv/{nome}-{ano}.csv"
    arq = destino / f"{nome}-{ano}.csv"
    log.info("Baixando %s", url)
    with httpx.stream("GET", url, headers={"User-Agent": cfg.user_agent}, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        with arq.open("wb") as f:
            for bloco in r.iter_bytes(1 << 20):
                f.write(bloco)
    return arq


def _linhas(arq: Path):
    with arq.open(encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f, delimiter=";")


def _ano(ano: int, pasta: Path) -> dict | None:
    try:
        arq_o = _baixar("votacoesOrientacoes", ano, pasta)
        arq_v = _baixar("votacoesVotos", ano, pasta)
    except httpx.HTTPError as exc:
        log.warning("Sem arquivos de votação de %s: %s", ano, exc)
        return None
    # 1) votações em que o Governo orientou Sim ou Não
    gov: dict[str, str] = {}
    for r in _linhas(arq_o):
        # anos antigos rotulam a bancada como "GOV." e variam a caixa do texto
        if r["siglaBancada"].strip().casefold() in ("governo", "gov.") and r["orientacao"].strip().casefold() in ("sim", "não"):
            gov[r["idVotacao"]] = "Sim" if r["orientacao"].strip().casefold() == "sim" else "Não"
    # 2) votos Sim/Não de cada partido, por votação
    cont: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])  # (votação, partido) -> [sim, não]
    todas: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])  # idem, em TODAS as votações nominais
    for r in _linhas(arq_v):
        voto = r["voto"].strip().casefold()
        if voto not in ("sim", "não"):
            continue
        chave = (r["idVotacao"], (r["deputado_siglaPartido"] or "").strip() or "Sem partido")
        todas[chave][0 if voto == "sim" else 1] += 1
        if r["idVotacao"] in gov:
            cont[chave][0 if voto == "sim" else 1] += 1
    # 3) agregação
    partidos: dict[str, dict] = defaultdict(lambda: {"gov_ok": 0, "gov_n": 0, "rice_soma": 0.0, "rice_n": 0})
    maioria: dict[str, dict[str, int]] = defaultdict(dict)  # votação -> partido -> +1 (sim) / -1 (não)
    votacoes_validas = set()
    for (v, p), (sim, nao) in cont.items():
        votacoes_validas.add(v)
        a = partidos[p]
        a["gov_n"] += sim + nao
        a["gov_ok"] += sim if gov[v] == "Sim" else nao
        if sim + nao >= MIN_VOTOS:
            a["rice_soma"] += abs(sim - nao) / (sim + nao)
            a["rice_n"] += 1
            if sim != nao:
                maioria[v][p] = 1 if sim > nao else -1
    pares: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # "A|B" -> [coincidem, total]
    for v, m in maioria.items():
        for a, b in itertools.combinations(sorted(m), 2):
            x = pares[f"{a}|{b}"]
            x[1] += 1
            x[0] += m[a] == m[b]
    # afinidade em todas as votações nominais (independe de o Governo ter orientado) — alimenta o mapa de afinidade
    maioria_t: dict[str, dict[str, int]] = defaultdict(dict)
    for (v, p), (sim, nao) in todas.items():
        if sim + nao >= MIN_VOTOS and sim != nao:
            maioria_t[v][p] = 1 if sim > nao else -1
    pares_t: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for v, m in maioria_t.items():
        for a, b in itertools.combinations(sorted(m), 2):
            x = pares_t[f"{a}|{b}"]
            x[1] += 1
            x[0] += m[a] == m[b]
    return {"votacoes": len(votacoes_validas), "partidos": partidos, "pares": pares,
            "votacoes_todas": len(maioria_t), "pares_todas": pares_t}


def coletar(desde: int, ate: int) -> dict:
    anos: dict[int, dict] = {}
    with tempfile.TemporaryDirectory(prefix="votacoes_") as tmp:
        for ano in range(desde, ate + 1):
            r = _ano(ano, Path(tmp))
            if r and (r["votacoes"] or r["votacoes_todas"]):
                anos[ano] = r
                log.info("%s: %d votações, %d partidos", ano, r["votacoes"], len(r["partidos"]))
    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_votos": MIN_VOTOS,
        "anos": {str(a): {"votacoes": r["votacoes"], "partidos": r["partidos"], "pares": r["pares"],
                        "votacoes_todas": r["votacoes_todas"], "pares_todas": r["pares_todas"]} for a, r in anos.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--desde", type=int, default=2023, help="primeiro ano (padrão: início da legislatura atual)")
    ap.add_argument("--ate", type=int, default=date.today().year)
    ap.add_argument("--saida", type=Path, default=SAIDA, help="arquivo de saída (padrão: alinhamento_camara.json)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dados = coletar(a.desde, a.ate)
    a.saida.parent.mkdir(parents=True, exist_ok=True)
    a.saida.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("Gravado %s (anos: %s)", a.saida, ", ".join(dados["anos"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
