"""Gera frontend/assets/gastos_tributarios.json a partir do Demonstrativo dos Gastos Tributários (DGT) da Receita Federal.

O DGT é publicado junto com o projeto de lei orçamentária (PLOA) em planilha XLSX, sem API. Cada quadro da planilha
tem um recorte: o VI soma por tributo, o V por função orçamentária e o X lista os principais gastos tributários.
O valor é uma projeção da renúncia de receita da União para o ano da lei, em reais correntes.

Uso (Python 3.9+, precisa de openpyxl: pip install openpyxl):
    python scripts/gerar_gastos_tributarios.py                  # baixa o PLOA ANO e grava o JSON
    python scripts/gerar_gastos_tributarios.py --xlsx arq.xlsx  # usa uma planilha já baixada

Quando sair o PLOA seguinte (em geral em agosto), troque ANO e rode de novo.
Página das edições: https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/relatorios/renuncia/gastos-tributarios-ploa
"""
import argparse
import datetime
import json
import tempfile
import urllib.request
import warnings
from pathlib import Path

ANO = 2026
BASE = "https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/relatorios/renuncia/gastos-tributarios-ploa"
URL_XLSX = BASE + "/dgt-previsao-ploa-{ano}-quadros.xlsx"
SAIDA = Path(__file__).resolve().parent.parent / "frontend" / "assets" / "gastos_tributarios.json"


def linhas(wb, aba):
    """Linhas não vazias da aba, sem as células vazias."""
    return [[c for c in r if c is not None] for r in wb[aba].iter_rows(values_only=True) if any(c is not None for c in r)]


def nome_limpo(txt):
    return " ".join(str(txt).split())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ano", type=int, default=ANO)
    ap.add_argument("--xlsx", type=Path)
    a = ap.parse_args()
    try:
        import openpyxl
    except ImportError:
        raise SystemExit("Falta o openpyxl: pip install openpyxl")
    warnings.filterwarnings("ignore")

    caminho = a.xlsx
    if not caminho:
        caminho = Path(tempfile.gettempdir()) / f"dgt_ploa_{a.ano}.xlsx"
        req = urllib.request.Request(URL_XLSX.format(ano=a.ano), headers={"User-Agent": "Mozilla/5.0 (ImpactoCidadao)"})
        caminho.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    wb = openpyxl.load_workbook(caminho, data_only=True, read_only=True)

    # Quadro VI: [tributo, valor, % do PIB, % da arrecadação, % do total de gastos tributários]
    por_tributo, total, pib, arrecadacao = [], None, None, None
    for r in linhas(wb, "Q VI"):
        if len(r) == 5 and isinstance(r[1], (int, float)):
            item = {"nome": nome_limpo(r[0]), "rs": r[1], "pct_pib": r[2], "pct_arrecadacao": r[3], "pct_total": r[4]}
            if item["nome"] == "TOTAL":
                total = item
            else:
                por_tributo.append(item)
        elif r and str(r[0]).startswith("ARRECADA"):
            arrecadacao = r[1]
        elif r and str(r[0]) == "PIB" and len(r) > 1 and isinstance(r[1], (int, float)):
            pib = r[1]
    if not total or not pib:
        raise SystemExit("Não achei o total do Quadro VI: a planilha mudou de formato.")

    # Quadro V: [função, valor, fração do total]
    por_funcao = [{"nome": nome_limpo(r[0]), "rs": r[1], "pct_total": r[2] * 100}
                  for r in linhas(wb, "Q V") if len(r) == 3 and isinstance(r[1], (int, float)) and str(r[0]).upper() != "TOTAL"]

    # Quadro X: principais gastos tributários [nome, valor, fração do total]
    principais = [{"nome": nome_limpo(r[0]), "rs": r[1], "pct_total": r[2] * 100}
                  for r in linhas(wb, "Q X") if len(r) == 3 and isinstance(r[1], (int, float)) and r[1] > 0 and str(r[0]).strip().upper() != "TOTAL"]

    saida = {
        "ano": a.ano,
        "gerado_em": datetime.date.today().isoformat(),
        "fonte": f"Receita Federal, Demonstrativo dos Gastos Tributários (DGT), projeções do PLOA {a.ano}",
        "url": BASE,
        "unidade": "R$ correntes (projeção da renúncia de receita da União)",
        "total_rs": total["rs"], "total_pct_pib": total["pct_pib"], "total_pct_arrecadacao": total["pct_arrecadacao"],
        "pib_rs": pib, "arrecadacao_rs": arrecadacao,
        "por_beneficio": sorted(principais, key=lambda x: -x["rs"]),
        "por_tributo": sorted(por_tributo, key=lambda x: -x["rs"]),
        "por_area": sorted(por_funcao, key=lambda x: -x["rs"]),
    }
    SAIDA.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Gravado {SAIDA}: total R$ {total['rs'] / 1e9:.1f} bi, {len(principais)} benefícios, {len(por_tributo)} tributos, {len(por_funcao)} áreas.")


if __name__ == "__main__":
    main()
