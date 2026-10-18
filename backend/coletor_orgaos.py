"""Coletor do orçamento federal por ministério e por órgão vinculado.

Fonte: "Orçamento da Despesa" do Portal da Transparência (CGU), arquivo aberto oficial (~1 MB zipado):
  https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/orcamento-despesa/AAAA_OrcamentoDespesa.zip

Cada linha do arquivo é uma ação do orçamento. Aqui somamos por órgão superior (ministérios e órgãos da Presidência) e,
dentro de cada um, por órgão subordinado (autarquias, fundações, fundos e unidades, como INSS, FNDE, Anvisa) e por função
(Saúde, Educação...). O resultado compacto é gravado em dados/orcamento/orgaos.json e a API só lê esse arquivo.

Regras (declaradas na tela):
  * "Orçamento inicial" = o que a lei autorizou; "atualizado" = com créditos e cortes do ano;
    "empenhado" = reservado para um gasto; "pago/realizado" = o valor que o Portal chama de "orçamento realizado";
  * só entram órgãos do Poder Executivo e seus órgãos de assessoramento (ministérios, Presidência, AGU, CGU);
  * a estrutura (secretarias, unidades e entidades vinculadas) vem do SIORG, o cadastro oficial da estrutura do Executivo federal
    (https://estruturaorganizacional.dados.gov.br); o orçamento não a detalha, então não há verba por secretaria;
  * o Ministério da Fazenda inclui a renovação (refinanciamento) da dívida pública, que não é gasto com serviços.

Uso (a partir da raiz do projeto):
    python backend/coletor_orgaos.py              # ano corrente
    python backend/coletor_orgaos.py --ano 2026
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "dados" / "orcamento" / "orgaos.json"
URL = "https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/orcamento-despesa/{ano}_OrcamentoDespesa.zip"
log = logging.getLogger("coletor_orgaos")

EXECUTIVO_EXTRA = {"20000", "37000", "63000", "25201"}  # Presidência, CGU, AGU, Banco Central
# Página oficial de cada órgão (código SIAFI -> endereço); todos conferidos em 18/10/2026. A estrutura interna
# (secretarias e organograma) fica nessas páginas: o orçamento não a detalha.
SITES = {c: f"https://www.gov.br/{s}/pt-br" for c, s in {
    "25000": "fazenda", "33000": "previdencia", "55000": "mds", "36000": "saude", "26000": "mec", "52000": "defesa",
    "40000": "trabalho-e-emprego", "32000": "mme", "20000": "planalto", "53000": "mdr", "44000": "mma", "46000": "gestao",
    "24000": "mcti", "30000": "mj", "68000": "portos-e-aeroportos", "22000": "agricultura", "39000": "transportes",
    "56000": "cidades", "47000": "planejamento", "28000": "mdic", "35000": "mre", "42000": "cultura", "54000": "turismo",
    "51000": "esporte", "41000": "mcom", "49000": "mda", "65000": "mulheres", "67000": "igualdaderacial", "81000": "mdh",
    "84000": "povosindigenas", "58000": "mpa", "69000": "mempe", "37000": "cgu", "63000": "agu"}.items()}
SITES["25201"] = "https://www.bcb.gov.br/"
URL_SIORG = "https://estruturaorganizacional.dados.gov.br/doc/estrutura-organizacional/resumida?codigoPoder=1&codigoEsfera=1"
# código do órgão no SIORG -> código SIAFI usado no orçamento
SIORG_PARA_SIAFI = {
    "26": "20000", "46": "63000", "214460": "37000", "244": "26000", "263": "35000", "304": "36000", "1927": "44000",
    "2852": "32000", "41066": "52000", "72084": "54000", "235881": "30000", "264359": "41000", "267175": "24000",
    "308793": "22000", "308794": "49000", "308795": "58000", "308796": "55000", "308797": "51000", "308798": "56000",
    "308799": "53000", "308800": "25000", "308803": "46000", "308804": "47000", "308814": "28000", "308815": "65000",
    "308816": "81000", "308817": "68000", "308818": "39000", "308819": "33000", "308820": "40000", "308821": "42000",
    "308823": "67000", "308824": "84000", "330683": "69000",
}
MAX_UNIDADES, MAX_SUB = 40, 14
CAMPOS = ("inicial", "atualizado", "empenhado", "realizado")


def _num(txt: str) -> float:
    return float(txt.replace(".", "").replace(",", ".")) if txt else 0.0


def _executivo(codigo: str, nome: str) -> bool:
    return nome.startswith("Ministério") or codigo in EXECUTIVO_EXTRA


def _zerar() -> dict:
    return dict.fromkeys(CAMPOS, 0.0)


def _somar(alvo: dict, linha: list[float]) -> None:
    for campo, v in zip(CAMPOS, linha):
        alvo[campo] += v


def _arred(d: dict) -> dict:
    return {k: round(v, 2) for k, v in d.items()}


def processar(csv_bytes: io.TextIOBase) -> dict:
    leitor = csv.reader(csv_bytes, delimiter=";")
    next(leitor)
    orgaos: dict[str, dict] = {}
    for r in leitor:
        cod, nome = r[1], r[2]
        if not _executivo(cod, nome):
            continue
        o = orgaos.setdefault(cod, {"codigo": cod, "nome": nome, "totais": _zerar(), "subs": {}, "funcoes": {}})
        v = [_num(r[i]) for i in (21, 22, 23, 24)]
        _somar(o["totais"], v)
        s = o["subs"].setdefault(r[3], {"codigo": r[3], "nome": r[4], "totais": _zerar()})
        _somar(s["totais"], v)
        f = o["funcoes"].setdefault(r[8], _zerar())
        _somar(f, v)
    saida = []
    for o in orgaos.values():
        subs = sorted(o["subs"].values(), key=lambda s: -s["totais"]["atualizado"])
        funcoes = sorted(({"nome": n, **_arred(t)} for n, t in o["funcoes"].items()), key=lambda f: -f["atualizado"])
        saida.append({
            "codigo": o["codigo"], "nome": o["nome"], "site": SITES.get(o["codigo"]), "totais": _arred(o["totais"]),
            "vinculados": [{"codigo": s["codigo"], "nome": s["nome"], **_arred(s["totais"])} for s in subs if s["totais"]["atualizado"] > 0],
            "funcoes": [f for f in funcoes if f["atualizado"] > 0][:5],
        })
    saida.sort(key=lambda o: -o["totais"]["atualizado"])
    return {"orgaos": saida}


def _id(url: str | None) -> str | None:
    return url.rsplit("/", 1)[-1] if url else None


def estrutura() -> dict[str, dict]:
    """Secretarias (unidades administrativas), entidades vinculadas e nº de colegiados de cada ministério (SIORG)."""
    with tempfile.TemporaryDirectory() as tmp:
        arq = Path(tmp) / "siorg.json"
        with httpx.stream("GET", URL_SIORG, timeout=300, follow_redirects=True) as r:
            r.raise_for_status()
            with arq.open("wb") as f:
                for pedaco in r.iter_bytes():
                    f.write(pedaco)
        unidades = json.loads(arq.read_text(encoding="utf-8"))["unidades"]
    filhos: dict[str, list[dict]] = defaultdict(list)
    for u in unidades:
        filhos[_id(u["codigoUnidadePai"])].append(u)
    tipo = lambda u: _id(u["codigoTipoUnidade"])  # noqa: E731
    saida: dict[str, dict] = {}
    for u in unidades:
        siafi = SIORG_PARA_SIAFI.get(_id(u["codigoUnidade"])) if tipo(u) == "orgao" else None
        if not siafi:
            continue
        diretos = filhos[_id(u["codigoUnidade"])]
        unid = [c for c in diretos if tipo(c) in ("unidade-administrativa", "orgao")]
        saida[siafi] = {
            "sigla": u.get("sigla"),
            "unidades": [{
                "nome": c["nome"], "sigla": c.get("sigla"),
                "subunidades": [x["nome"] for x in filhos[_id(c["codigoUnidade"])] if tipo(x) == "unidade-administrativa"][:MAX_SUB],
                "n_sub": sum(1 for x in filhos[_id(c["codigoUnidade"])] if tipo(x) == "unidade-administrativa"),
            } for c in unid[:MAX_UNIDADES]],
            "n_unidades": len(unid),
            "entidades": [{"nome": c["nome"], "sigla": c.get("sigla")} for c in diretos if tipo(c) == "entidade"],
            "n_colegiados": sum(1 for c in diretos if tipo(c) == "unidade-colegiada"),
        }
    return saida


def coletar(ano: int) -> dict:
    url = URL.format(ano=ano)
    with tempfile.TemporaryDirectory() as tmp:
        arq = Path(tmp) / "o.zip"
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
            r.raise_for_status()
            with arq.open("wb") as f:
                for pedaco in r.iter_bytes():
                    f.write(pedaco)
        with zipfile.ZipFile(arq) as z:
            with z.open(z.namelist()[0]) as bruto:
                dados = processar(io.TextIOWrapper(bruto, encoding="cp1252", newline=""))
    est = estrutura()
    for o in dados["orgaos"]:
        o["estrutura"] = est.get(o["codigo"])
    dados.update({
        "ano": ano, "fonte_estrutura": URL_SIORG, "fonte": url,
        "fonte_nome": "Portal da Transparência (CGU) — Orçamento da Despesa",
        "coletado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return dados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ano", type=int, default=date.today().year)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dados = coletar(args.ano)
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("%d órgãos gravados em %s", len(dados["orgaos"]), DESTINO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
