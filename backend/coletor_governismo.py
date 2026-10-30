"""Coletor do "De olho no governo": governismo, fidelidade ao partido e presença de cada deputado.

Fonte: arquivos abertos oficiais da Câmara dos Deputados (um conjunto por ano):
  votacoesOrientacoes-AAAA.csv · votacoesVotos-AAAA.csv · votacoes-AAAA.csv

Só entram votações NOMINAIS DO PLENÁRIO (o voto de cada deputado fica no painel). Regras, também declaradas na tela:
  * Governismo = votos IGUAIS à orientação do Governo ÷ votações em que o Governo orientou "Sim" ou "Não" e o deputado
    estava no exercício. Votar o contrário, abster-se, obstruir ou faltar contam contra; presidir a sessão (Art. 17) fica de fora.
  * "No exercício" = do primeiro ao último voto do deputado na legislatura: quem entrou como suplente depois não é cobrado
    pelo que veio antes. Licenças no meio do caminho não têm como ser separadas nesses dados e entram como falta.
  * Fidelidade ao partido = % dos votos "Sim"/"Não" do deputado iguais ao da maioria do próprio partido na mesma votação
    (partido com ao menos 3 votantes e sem empate). Usa a maioria porque a Câmara publica a orientação por bloco/federação.
  * Coesão do partido = em cada votação, |sim − não| ÷ (sim + não) entre os votos do partido, média das votações.
  * Presença = votações em que o deputado registrou voto (qualquer um) ÷ votações nominais do Plenário no seu período.
    Falta justificada e não justificada não são distinguíveis nesses arquivos.

Os arquivos são grandes, então o trabalho é feito AQUI e a API só lê o resultado: dados/votacoes/governismo_camara.json.
O Senado não publica a orientação do Governo nos dados abertos de votação, por isso o governismo é só da Câmara.

Uso (a partir da raiz do projeto):
    python backend/coletor_governismo.py          # legislatura atual (2023) até o ano corrente
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coletor_votacoes import _baixar, _linhas  # noqa: E402
from config import RAIZ, cfg  # noqa: E402

log = logging.getLogger("impacto.governismo")

SAIDA = RAIZ / "dados" / "votacoes" / "governismo_camara.json"
APELIDOS = RAIZ / "dados" / "votacoes" / "apelidos.json"
MIN_VOTANTES = 3          # votantes mínimos do partido numa votação para valer na fidelidade e na coesão
MAX_PAUTAS = 15           # votações recentes de projetos relevantes mostradas como "pautas quentes"
API = "https://dadosabertos.camara.leg.br/api/v2"

CODIGOS = {"sim": "S", "não": "N", "abstenção": "A", "obstrução": "O", "artigo 17": "P"}
# Só projetos de peso interessam ao cidadão: emendas à Constituição, leis (complementares ou ordinárias), MPs e decretos legislativos.
TIPOS = [("proposta de emenda à constituição", "PEC"), ("projeto de lei complementar", "PLP"),
         ("projeto de lei de conversão", "PLV"), ("projeto de lei", "PL"), ("medida provisória", "MPV"),
         ("projeto de decreto legislativo", "PDL")]
_RE_PROP = re.compile(r"(proposta de emenda à constituição|projeto de lei complementar|projeto de lei de conversão|"
                      r"projeto de lei|medida provisória|projeto de decreto legislativo)\s+n[º°o]?\s*([\d.]+)"
                      r"(?:/\d+)?,?\s+de\s+(\d{4})", re.I)
_RE_PLACAR = re.compile(r"\s*Sim:\s*\d+.*$", re.S)


def _codigo(voto: str) -> str:
    return CODIGOS.get((voto or "").strip().casefold(), "")


def rotulo_proposicao(descricao: str) -> str | None:
    """'Aprovada a Subemenda … ao Projeto de Lei Complementar nº 281, de 2019 …' → 'PLP 281/2019'."""
    m = _RE_PROP.search(descricao or "")
    if not m:
        return None
    sigla = dict(TIPOS)[m.group(1).casefold()]
    return f"{sigla} {m.group(2).replace('.', '')}/{m.group(3)}"


def consolidar(plen: dict[str, str], gov: dict[str, str], votos: list[tuple]) -> dict:
    """Calcula as métricas por deputado e ano.

    plen:  idVotação → data (AAAA-MM-DD) das votações do Plenário.
    gov:   idVotação → orientação do Governo ("Sim"/"Não"); só votações do Plenário.
    votos: (idVotação, idDeputado, nome, uf, foto, partido, código) com código S/N/A/O/P ou "" (sem voto registrado).
    """
    por_vid: dict[str, dict[str, str]] = defaultdict(dict)          # votação → deputado → código
    partido_em: dict[tuple[str, str], str] = {}                      # (votação, deputado) → partido no dia
    info: dict[str, dict] = {}
    janela: dict[str, list[str]] = {}
    for vid, dep, nome, uf, foto, partido, cod in votos:
        d = plen[vid]
        por_vid[vid][dep] = cod
        partido_em[(vid, dep)] = partido
        i = info.get(dep)
        if i is None or d >= i["_data"]:
            info[dep] = {"id": int(dep), "nome": nome, "uf": uf, "foto": foto, "partido": partido, "_data": d}
        j = janela.setdefault(dep, [d, d])
        j[0], j[1] = min(j[0], d), max(j[1], d)
    nominais = sorted((plen[v], v) for v in por_vid)                # votações com ao menos um voto registrado
    gov_nom = [(d, v) for d, v in nominais if v in gov]

    # maioria de cada partido em cada votação (fidelidade) e coesão do partido
    contagem: dict[str, dict[str, list[int]]] = {v: defaultdict(lambda: [0, 0]) for _, v in nominais}
    for vid, deps in por_vid.items():
        for dep, cod in deps.items():
            if cod in ("S", "N"):
                contagem[vid][partido_em[(vid, dep)]][0 if cod == "S" else 1] += 1
    maioria: dict[tuple[str, str], str] = {}
    coesao: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for vid, partidos in contagem.items():
        ano = plen[vid][:4]
        for p, (s, n) in partidos.items():
            if s + n >= MIN_VOTANTES:
                c = coesao[ano][p]
                c[0] += abs(s - n) / (s + n)
                c[1] += 1
                if s != n:
                    maioria[(vid, p)] = "S" if s > n else "N"

    anos_dep: dict[str, dict[str, list[int]]] = defaultdict(dict)
    for dep, (ini, fim) in janela.items():
        a: dict[str, list[int]] = defaultdict(lambda: [0] * 8)  # ok, contra, abst, aus, fid_ok, fid_n, presente, nominais
        for d, v in nominais:
            if d < ini or d > fim:
                continue
            x = a[d[:4]]
            cod = por_vid[v].get(dep)
            x[7] += 1
            if cod:
                x[6] += 1
            if cod in ("S", "N"):
                m = maioria.get((v, partido_em[(v, dep)]))
                if m:
                    x[5] += 1
                    x[4] += cod == m
            if v in gov and cod != "P":
                orient = "S" if gov[v] == "Sim" else "N"
                if cod == orient:
                    x[0] += 1
                elif cod in ("S", "N"):
                    x[1] += 1
                elif cod in ("A", "O"):
                    x[2] += 1
                else:
                    x[3] += 1
        anos_dep[dep] = a

    ultima = nominais[-1][0] if nominais else None
    deputados = []
    for dep, i in sorted(info.items(), key=lambda kv: kv[1]["nome"].casefold()):
        deputados.append({"id": i["id"], "nome": i["nome"], "uf": i["uf"], "partido": i["partido"], "foto": i["foto"],
                          "primeiro_voto": janela[dep][0], "ultimo_voto": janela[dep][1],
                          "anos": {a: v for a, v in sorted(anos_dep[dep].items())}})
    anos = sorted({d[:4] for d, _ in nominais})
    return {
        "ate_data": ultima,
        "anos": {a: {"nominais": sum(1 for d, _ in nominais if d[:4] == a),
                     "com_orientacao": sum(1 for d, _ in gov_nom if d[:4] == a)} for a in anos},
        "deputados": deputados,
        "coesao": {a: {p: [round(c[0], 4), int(c[1])] for p, c in sorted(ps.items())} for a, ps in sorted(coesao.items())},
        "_por_vid": por_vid, "_partido_em": partido_em, "_contagem": contagem,
    }


def _pautas(resumo: dict, vt: dict[str, dict], gov: dict[str, str], deputados: dict) -> list[dict]:
    """Votações recentes de projetos relevantes (PEC, PLP, PL, MPV, PDL) em que o Governo orientou Sim ou Não."""
    por_vid, contagem = resumo["_por_vid"], resumo["_contagem"]
    apelidos = json.loads(APELIDOS.read_text(encoding="utf-8")) if APELIDOS.exists() else {}
    cand: dict[str, tuple[str, str, str]] = {}  # proposição → (data, id da votação, rótulo): fica a mais recente
    for vid in por_vid:
        r = vt.get(vid)
        if not r or vid not in gov or re.match(r"\s*(aprovad|rejeitad)\w*\s+o\s+requerimento", r["descricao"], re.I):
            continue
        rot = rotulo_proposicao(r["descricao"])
        if rot and (rot not in cand or (r["data"], vid) > cand[rot][:2]):
            cand[rot] = (r["data"], vid, rot)
    escolhidas = sorted(cand.values(), reverse=True)[:MAX_PAUTAS]
    pautas = []
    with httpx.Client(headers={"User-Agent": cfg.user_agent}, timeout=30, follow_redirects=True) as http:
        for data, vid, rot in escolhidas:
            r = vt[vid]
            ementa = None
            sigla, resto = rot.split(" ")
            numero, ano = resto.split("/")
            try:  # a ementa é a do PROJETO (a "proposição" do arquivo de votações costuma ser o parecer do relator)
                achados = http.get(f"{API}/proposicoes", params={"siglaTipo": sigla, "numero": numero, "ano": ano,
                                                                 "itens": 1}).json().get("dados") or []
                ementa = achados[0].get("ementa") if achados else None
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Ementa de %s indisponível: %s", rot, exc)
            time.sleep(0.1)
            pautas.append({
                "id": vid, "data": data, "proposicao": rot, "apelido": apelidos.get(rot),
                "ementa": ementa, "descricao": _RE_PLACAR.sub("", r["descricao"]).strip(),
                "orientacao_governo": gov[vid], "aprovada": r["aprovacao"] == "1",
                "sim": int(r["sim"] or 0), "nao": int(r["nao"] or 0),
                "url": f"https://dadosabertos.camara.leg.br/api/v2/votacoes/{vid}",
                "partidos": {p: [s, n] for p, (s, n) in sorted(contagem[vid].items()) if s + n},
            })
    return pautas


def coletar(desde: int, ate: int) -> dict:
    plen: dict[str, str] = {}
    gov: dict[str, str] = {}
    vt: dict[str, dict] = {}
    votos: list[tuple] = []
    with tempfile.TemporaryDirectory(prefix="governismo_") as tmp:
        for ano in range(desde, ate + 1):
            try:
                arq_o = _baixar("votacoesOrientacoes", ano, Path(tmp))
                arq_v = _baixar("votacoesVotos", ano, Path(tmp))
                arq_t = _baixar("votacoes", ano, Path(tmp))
            except httpx.HTTPError as exc:
                log.warning("Sem arquivos de %s: %s", ano, exc)
                continue
            for r in _linhas(arq_t):
                if r["siglaOrgao"] == "PLEN":
                    plen[r["id"]] = r["data"]
                    vt[r["id"]] = {"data": r["data"], "descricao": r["descricao"], "aprovacao": r["aprovacao"],
                                   "sim": r["votosSim"], "nao": r["votosNao"]}
            for r in _linhas(arq_o):
                o = r["orientacao"].strip().casefold()
                if r["idVotacao"] in plen and r["siglaBancada"].strip().casefold() in ("governo", "gov.") and o in ("sim", "não"):
                    gov[r["idVotacao"]] = "Sim" if o == "sim" else "Não"
            for r in _linhas(arq_v):
                if r["idVotacao"] in plen:
                    votos.append((r["idVotacao"], r["deputado_id"], r["deputado_nome"], r["deputado_siglaUf"],
                                  r["deputado_urlFoto"], (r["deputado_siglaPartido"] or "").strip() or "Sem partido",
                                  _codigo(r["voto"])))
            log.info("%s: %d votos acumulados, %d votações com orientação do Governo", ano, len(votos), len(gov))
    if not votos:
        raise SystemExit("Nenhum voto coletado.")
    r = consolidar(plen, gov, votos)
    pautas = _pautas(r, vt, gov, r["deputados"])
    # voto de cada deputado nas pautas quentes (S/N/A/O/P ou '-' se não registrou), na ordem de `pautas`
    for d in r["deputados"]:
        d["pautas"] = "".join(r["_por_vid"][p["id"]].get(str(d["id"])) or "-" for p in pautas)
    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_votantes": MIN_VOTANTES, "ate_data": r["ate_data"], "anos": r["anos"],
        "deputados": r["deputados"], "coesao": r["coesao"], "pautas": pautas,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--desde", type=int, default=2023, help="primeiro ano (padrão: início da legislatura atual)")
    ap.add_argument("--ate", type=int, default=date.today().year)
    ap.add_argument("--saida", type=Path, default=SAIDA)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dados = coletar(a.desde, a.ate)
    a.saida.parent.mkdir(parents=True, exist_ok=True)
    a.saida.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("Gravado %s: %d deputados, %d pautas", a.saida, len(dados["deputados"]), len(dados["pautas"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
