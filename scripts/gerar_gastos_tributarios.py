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

# O Quadro X agrupa dispositivos legais em "principais gastos tributários" sem dizer quais entram em cada grupo.
# A composição abaixo foi montada comparando os nomes dos itens (Quadros XI a XXV) e conferida pela soma:
# o script avisa se um grupo não fecha com o valor do Quadro X (tolerância de 0,5%).
COMPOSICAO = {
    "Agricultura e Agroindústria": ["Agricultura e Agroindústria", "Exportação da Produção Rural", "Funrural"],
    "Rendimentos Isentos e Não Tributáveis - IRPF": ["Aposentadoria por Moléstia Grave ou Acidente", "Aposentadoria de Declarante com 65 Anos ou Mais",
                                                     "Indenizações por Rescisão de Contrato de Trabalho", "Seguro ou Pecúlio Pago por Morte ou Invalidez"],
    "Entidades Sem Fins Lucrativos - Imunes / Isentas": ["Entidades sem Fins Lucrativos", "Entidades Filantrópicas", "Doações de Bens para Entidades Filantrópicas"],
    "Deduções do Rendimento Tributável - IRPF": ["Despesas Médicas", "Despesas com Educação"],
    "Poupança e Títulos de Crédito - Setor Imobiliário e do Agronegócio": ["Títulos de Crédito", "Poupança"],
    "Zona Franca de Manaus e Áreas de Livre Comércio": ["Zona Franca de Manaus e Amazônia Ocidental", "Zona Franca de Manaus e Área de Livre Comércio", "Áreas de Livre Comércio",
                                                        "Amazônia Ocidental", "Zona Franca de Manaus"],
    "Desenvolvimento Regional": ["SUDENE", "SUDAM", "Mercadorias Norte e Nordeste"],
    "Benefícios do Trabalhador": ["Assistência Médica, Odontológica e Farmacêutica a Empregados", "PAT", "Benefícios Previdenciários a Empregados e FAPI",
                                  "Previdência Privada Fechada", "Empresa cidadã"],
    "Medicamentos, Produtos Farmacêuticos e Equipamentos Médicos": ["Produtos Químicos e Farmacêuticos", "Medicamentos", "Equipamentos para uso médico, hospitalar, clínico ou laboratorial"],
    "MEI - Microempreendedor Individual": ["MEI"],
    "Pesquisas Científicas e Inovação Tecnológica": ["Inovação Tecnológica", "Pesquisas Científicas"],
    "Embarcações e Aeronaves": ["Embarcações e Aeronaves", "Leasing de Aeronaves", "Embarcações"],
    "Cultura e Audiovisual": ["PRONAC", "Atividade Audiovisual", "Programa Nacional de Apoio à Cultura", "Academia Brasileira de Letras",
                             "Instituto Histórico e Geográfico Brasileiro", "Associação Brasileira de Imprensa"],
    "Transporte de Passageiros": ["Transporte Aéreo de Passageiros", "Transporte Rodoviário de Passageiros"],
    "Investimentos em Infra-Estrutura": ["Debêntures de sociedades de propósito específico para investimento na área de infraestrutura", "FIP-IE"],
    "Automóveis - Pessoas Portadoras de Deficiência": ["Automóveis"],
    "Fundos da Criança e do Adolescente": ["Fundos de Direitos da Criança e do Adolescente"],
    "Doações a Instituições de Ensino e Pesquisa e Entidades Civis Sem Fins Lucrativos": ["Doações a Entidades Civis Sem Fins Lucrativos", "Doações a Instituições de Ensino e Pesquisa"],
    "Máquinas e Equipamentos - CNPq": ["Máquinas e Equipamentos"],
    "Reciclagem": ["Incentivo à Reciclagem"],
    "TEF - Tributação Específica do Futebol": ["TEF"],
    "TI e TIC - Tecnologia de Informação e Tecnologia da Informação e da Comunicação": ["TI e TIC"],
}
QUADROS_ITENS = ["Q XI", "Q XII", "Q XIII", "Q XIV", "Q XV", "Q XVI", "Q XVII", "Q XVIII", "Q XIX", "Q XX", "Q XXI", "Q XXII", "Q XXIII", "Q XXIV", "Q XXV"]


def sigla_tributo(titulo):
    t = nome_limpo(titulo)
    return t.split(" - ")[-1] if " - " in t else t.title()


def itens_detalhados(wb):
    """Quadros XI a XXV: cada dispositivo legal tem uma linha com o valor e duas com a descrição e a base legal."""
    itens = []
    for q in QUADROS_ITENS:
        rows = linhas(wb, q)
        tributo = sigla_tributo(rows[2][0])
        i = 0
        while i < len(rows):
            r = rows[i]
            if len(r) >= 6 and isinstance(r[0], int) and isinstance(r[3], (int, float)):
                vig = r[2].strftime("%d/%m/%Y") if isinstance(r[2], datetime.datetime) else str(r[2])
                desc = rows[i + 1][0] if i + 1 < len(rows) and len(rows[i + 1]) == 1 else ""
                base = rows[i + 2][0] if i + 2 < len(rows) and len(rows[i + 2]) == 1 else ""
                itens.append({"tributo": tributo, "nome": nome_limpo(r[1]), "vigencia": vig, "rs": r[3],
                              "desc": nome_limpo(desc), "base": nome_limpo(base)})
                i += 3
            else:
                i += 1
    return itens


def montar_detalhe(principais, itens):
    """Liga cada grupo do Quadro X aos seus dispositivos e confere a soma."""
    por_prefixo = {}
    for x in itens:
        por_prefixo.setdefault(x["nome"].split(" - ")[0].strip(), []).append(x)
    detalhe = {}
    for g in principais:
        nome = g["nome"].strip()
        prefixos = COMPOSICAO.get(nome, [nome])
        lista = [x for p in prefixos for x in por_prefixo.get(p, [])]
        soma = sum(x["rs"] for x in lista)
        if abs(soma - g["rs"]) > max(0.005 * g["rs"], 1e5):
            print(f"  ! o grupo “{nome}” não fecha: itens somam R$ {soma / 1e9:.3f} bi e o Quadro X diz R$ {g['rs'] / 1e9:.3f} bi")
            if not lista:
                continue
        detalhe[g["nome"]] = sorted(lista, key=lambda x: -x["rs"])
    return detalhe



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
        "itens": itens_detalhados(wb),
    }
    saida["detalhe_beneficio"] = montar_detalhe(saida["por_beneficio"], saida["itens"])
    saida["detalhe_tributo"] = {}
    for x in saida["itens"]:
        saida["detalhe_tributo"].setdefault(x["tributo"], []).append(x)
    del saida["itens"]
    SAIDA.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Gravado {SAIDA}: total R$ {total['rs'] / 1e9:.1f} bi, {len(principais)} benefícios, {len(por_tributo)} tributos, {len(por_funcao)} áreas.")


if __name__ == "__main__":
    main()
