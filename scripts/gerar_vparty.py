"""Gera frontend/assets/vparty.json a partir do V-Party (V-Dem), pesquisa de especialistas sobre partidos.

O V-Party v2 (Lindberg et al., 2022, https://doi.org/10.23696/vpartydsv2) é aberto, mas o download exige um
cadastro no site do V-Dem (https://v-dem.net/data/v-party-dataset/country-party-date-v2/). Baixe o
"Country-Party-Date: V-Party" em CSV e aponte o arquivo:

    python scripts/gerar_vparty.py --arq CPD_V-Party_CSV_v2.zip      # ou o próprio CSV

Só biblioteca padrão. Extrai o Brasil, eleições de 2002 em diante, na escala original (_osp) do V-Dem:
  * eco   (v2pariglef_osp, 0–6): 0 extrema-esquerda … 3 centro … 6 extrema-direita, em política econômica;
  * lgbt  (v2palgbt_osp, 0–4):  0 opõe-se forte … 4 apoia forte a igualdade social LGBT;
  * culsup(v2paculsup_osp, 0–4): 0 promove forte … 4 rejeita forte a superioridade cultural de um grupo/nação;
  * relig (v2parelig_osp, 0–4):  0 invoca religião quase sempre … 4 nunca invoca religião para justificar posições;
  * cultura = média simples dos três itens culturais (0–4; 2 = ponto neutro).
"""
import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

SAIDA = Path(__file__).resolve().parent.parent / "frontend" / "assets" / "vparty.json"
ANO_INI = 2002


def _linhas(arq: Path):
    if arq.suffix.lower() == ".zip":
        with zipfile.ZipFile(arq) as z:
            nome = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            texto = io.TextIOWrapper(z.open(nome), encoding="utf-8-sig", newline="")
            yield from csv.DictReader(texto)
    else:
        with arq.open(encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arq", type=Path, required=True, help="zip ou CSV do V-Party v2 (Country-Party-Date)")
    a = ap.parse_args()
    por_ano: dict[int, list[dict]] = {}
    for r in _linhas(a.arq):
        if r["country_name"] != "Brazil" or int(r["year"]) < ANO_INI:
            continue
        eco, lgbt, cul, rel = (_num(r[c]) for c in ("v2pariglef_osp", "v2palgbt_osp", "v2paculsup_osp", "v2parelig_osp"))
        if None in (eco, lgbt, cul, rel):
            continue
        por_ano.setdefault(int(r["year"]), []).append({
            "sigla": r["v2pashname"].strip(), "nome_en": r["v2paenname"].strip(),
            "eco": round(eco, 2), "eco_sd": round(_num(r["v2pariglef_osp_sd"]) or 0, 2),
            "lgbt": round(lgbt, 2), "culsup": round(cul, 2), "relig": round(rel, 2),
            "cultura": round((lgbt + cul + rel) / 3, 2), "votos": _num(r["v2pavote"]),
        })
    if not por_ano:
        print("Nenhum dado do Brasil encontrado.", file=sys.stderr)
        return 1
    saida = {"fonte": "V-Party v2 (V-Dem Institute, Universidade de Gotemburgo, fev/2022)", "doi": "https://doi.org/10.23696/vpartydsv2",
             "anos": {str(y): sorted(p, key=lambda x: x["sigla"].casefold()) for y, p in sorted(por_ano.items())}}
    SAIDA.write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Gravado {SAIDA} (eleições: {', '.join(saida['anos'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
