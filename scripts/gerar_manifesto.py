"""Gera frontend/assets/manifesto.json: programas de candidatura à Presidência do Brasil no Manifesto Project.

Fonte: Manifesto Project Main Dataset (MPDS2026a) + lista longa de partes (parties_long), WZB Berlim,
https://manifesto-project.wzb.eu/ (download exige cadastro gratuito; o acesso por API exige chave pessoal).

    python scripts/gerar_manifesto.py --dados MPDataset_MPDS2026a.csv --partes parties_long_MPDataset_MPDS2026a.csv

No Brasil a base codifica programas de candidatura/coalizões (não partidos individuais). Guardamos o que a base traz:
rile (esquerda–direita, -100 a +100), planeco (economia planejada), markeco (economia de mercado), welfare (bem-estar).
"""
import argparse
import csv
import json
import sys
from pathlib import Path

SAIDA = Path(__file__).resolve().parent.parent / "frontend" / "assets" / "manifesto.json"


def _ler(arq: Path):
    with arq.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dados", type=Path, required=True)
    ap.add_argument("--partes", type=Path, required=True)
    a = ap.parse_args()
    partes = [p for p in _ler(a.partes) if p["countryname"] == "Brazil"]
    abrev = {p["party"]: p["abbrev"] for p in partes if p["abbrev"] != "NA"}
    lider: dict[tuple[str, int], str] = {}   # (id, ano da eleição) -> sigla do partido que lidera a coalizão
    for p in partes:
        lid = p["coalition_member_leading"]
        if lid == "NA":
            continue
        sigla = abrev.get(lid, lid) if lid.isdigit() else lid.split(" ")[0]
        for ano in range(int(p["change_year_min"]), int(p["change_year_max"]) + 1):
            lider[(p["party"], ano)] = sigla
    itens = []
    for r in _ler(a.dados):
        if r["countryname"] != "Brazil" or _num(r["rile"]) is None:
            continue
        itens.append({"eleicao": int(r["date"][:4]), "id": r["party"], "nome_en": r["partyname"], "candidato": r["candidatename"],
                      "lider": lider.get((r["party"], int(r["date"][:4]))) or (r["partyabbrev"] if r["partyabbrev"] != "NA" else None),
                      "rile": _num(r["rile"]), "planeco": _num(r["planeco"]), "markeco": _num(r["markeco"]), "welfare": _num(r["welfare"])})
    itens.sort(key=lambda x: (x["eleicao"], x["nome_en"].casefold()))
    SAIDA.write_text(json.dumps({"fonte": "Manifesto Project Main Dataset MPDS2026a (WZB Berlim)", "url": "https://manifesto-project.wzb.eu/",
                                 "itens": itens}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Gravado {SAIDA} ({len(itens)} programas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
