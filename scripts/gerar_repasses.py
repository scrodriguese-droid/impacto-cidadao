"""Gera frontend/assets/repasses.json a partir das Transferências Constitucionais do Tesouro Transparente.

O Tesouro publica um CSV por mês, separado para estados e para municípios
(https://www.tesourotransparente.gov.br/ckan/dataset/transferencias-constitucionais-para-estados e
 .../transferencias-constitucionais-para-municipios). Não há API de valores: o catálogo (CKAN) só lista os arquivos.

O script soma, para o ano pedido, o que a União repassou diretamente por tributo (FPE, FPM, IPI-Exportação, ITR e
Cide-Combustíveis). Os valores são líquidos do desconto de 20% que fica retido para o Fundeb, que vem em linhas à parte.
Também confere os arquivos e guarda em "avisos" cada problema encontrado (mês faltando, nome de arquivo trocado,
valor sem separador decimal), porque a página mostra essas falhas em nota de rodapé.

Uso (Python 3.9+, só biblioteca padrão):
    python scripts/gerar_repasses.py                   # ano ANO, baixa os arquivos que faltam
    python scripts/gerar_repasses.py --ano 2023 --cache pasta_com_csv

Os arquivos baixados ficam em --cache (padrão: pasta temporária), então rodar de novo é rápido.
"""
import argparse
import csv
import datetime
import json
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ANO = 2024
CKAN = "https://www.tesourotransparente.gov.br/ckan/api/3/action/package_show?id=transferencias-constitucionais-para-{}"
SAIDA = Path(__file__).resolve().parent.parent / "frontend" / "assets" / "repasses.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (ImpactoCidadao; dados abertos)"}

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

# (conjunto, categoria na coluna "Transferência") -> item mostrado. Só entram as linhas em que o item e a categoria
# coincidem (o repasse direto); as linhas "FUNDEB" são parcelas retidas e não são somadas aqui.
ITENS = [
    ("estados", "FPE", "fpe", "FPE", "Estados"),
    ("estados", "IPI-Exp", "ipi_exp", "IPI-Exportação", "Estados"),
    ("estados", "CIDE-Combustíveis", "cide_estados", "Cide-Combustíveis (parte dos estados)", "Estados"),
    ("municipios", "FPM", "fpm", "FPM", "Municípios"),
    ("municipios", "ITR", "itr", "ITR", "Municípios"),
    ("municipios", "CIDE-Combustíveis", "cide_municipios", "Cide-Combustíveis (parte dos municípios)", "Municípios"),
]


def baixar(url, destino, tentativas=3):
    if destino.exists() and destino.stat().st_size > 0:
        return
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            destino.write_bytes(urllib.request.urlopen(req, timeout=90).read())
            return
        except Exception as exc:  # noqa: BLE001 — servidor do Tesouro oscila; tenta de novo
            erro = exc
            time.sleep(3)
    print(f"  ! falhou: {url} ({erro})", file=sys.stderr)


def ler_csv(caminho):
    bruto = caminho.read_bytes()
    try:
        texto = bruto.decode("utf-8")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    return list(csv.reader(texto.splitlines(), delimiter=";"))


def numero(txt):
    try:
        return float(txt)
    except ValueError:
        return 0.0


def arquivos_do_ano(conjunto, ano, cache):
    """Baixa os CSV mensais cujo nome indica o ano; devolve [(nome, caminho)]."""
    req = urllib.request.Request(CKAN.format(conjunto), headers=HEADERS)
    recursos = json.loads(urllib.request.urlopen(req, timeout=90).read())["result"]["resources"]
    saida = []
    for r in recursos:
        nome = r.get("name") or ""
        if r.get("format") != "CSV" or not re.search(rf"Mensal_.*_{ano}\d\d", nome):
            continue
        destino = cache / f"{conjunto}_{nome}_{r['id'][:6]}.csv"
        baixar(r["url"], destino)
        saida.append((nome, destino))
    return saida


def processar(conjunto, ano, cache):
    """Soma o ano por categoria e coleta os avisos de qualidade dos arquivos."""
    somas, meses_vistos, avisos = {}, {}, []
    for nome, caminho in sorted(arquivos_do_ano(conjunto, ano, cache)):
        if not caminho.exists():
            avisos.append({"tipo": "arquivo", "conjunto": conjunto, "texto": f"O arquivo {nome} não pôde ser baixado."})
            continue
        linhas = ler_csv(caminho)
        if len(linhas) < 2:
            avisos.append({"tipo": "vazio", "conjunto": conjunto, "texto": f"O arquivo {nome} veio vazio."})
            continue
        cab = [c.strip() for c in linhas[0]]
        i_ano, i_item, i_cat = cab.index("ANO"), cab.index("Item transferência"), cab.index("Transferência")
        i_d1 = next(i for i, c in enumerate(cab) if c.startswith("1º"))
        i_mes = i_d1 - 1
        corpo = [r for r in linhas[1:] if len(r) > i_cat and r[i_ano].strip() == str(ano)]
        meses_arq = sorted({int(r[i_mes]) for r in corpo})
        # nome do arquivo (AAAAMM) quase nunca é o mês dos dados
        m_nome = int(re.search(rf"{ano}(\d\d)", nome).group(1))
        if meses_arq and meses_arq != [m_nome]:
            avisos.append({"tipo": "nome", "conjunto": conjunto, "arquivo": nome, "mes_nome": m_nome, "mes_dados": meses_arq[0],
                           "texto": f"O arquivo “{nome}” traz os dados de {MESES[meses_arq[0] - 1]} de {ano}, não de {MESES[m_nome - 1]}."})
        # setembro de 2024: todos os valores vêm sem ponto decimal (em centavos)
        valores = [x for r in corpo for x in r[i_d1:i_d1 + 3] if x not in ("", "0")]
        fator = 0.01 if valores and all("." not in x for x in valores) else 1.0
        if fator != 1.0:
            ref = "FPE" if conjunto == "estados" else "FPM"
            exemplo = somar_linhas(corpo, i_item, i_cat, i_d1, 1.0, ref).get(ref, 0)
            rot = "municípios" if conjunto == "municipios" else conjunto
            avisos.append({"tipo": "centavos", "conjunto": conjunto, "arquivo": nome, "mes_dados": meses_arq[0], "lido_literal_rs": exemplo,
                           "texto": f"No arquivo de {MESES[meses_arq[0] - 1]} de {ano} ({rot}) todos os valores vêm sem separador decimal."})
        for m in meses_arq:
            meses_vistos.setdefault(m, []).append(nome)
        for k, v in somar_linhas(corpo, i_item, i_cat, i_d1, fator).items():
            somas[k] = somas.get(k, 0.0) + v
    for m, nomes in meses_vistos.items():
        if len(nomes) > 1:
            avisos.append({"tipo": "duplicado", "conjunto": conjunto, "mes_dados": m, "texto": f"O mês {m} aparece em mais de um arquivo ({', '.join(nomes)})."})
    faltam = [m for m in range(1, 13) if m not in meses_vistos]
    for m in faltam:
        avisos.append({"tipo": "faltando", "conjunto": conjunto, "mes_dados": m,
                       "texto": f"Não existe arquivo de {MESES[m - 1]} de {ano} nas transferências para {"municípios" if conjunto == "municipios" else conjunto}."})
    return somas, sorted(meses_vistos), faltam, avisos


def somar_linhas(corpo, i_item, i_cat, i_d1, fator, so_categoria=None):
    out = {}
    for r in corpo:
        cat = r[i_cat].strip()
        if so_categoria and cat != so_categoria:
            continue
        out[cat] = out.get(cat, 0.0) + sum(numero(x) for x in r[i_d1:i_d1 + 3]) * fator
    return out


def somar_diretos(conjunto, ano, cache):
    """Como somar_linhas, mas só onde o item é igual à categoria (repasse direto, líquido do Fundeb)."""
    soma = {}
    for nome, caminho in arquivos_do_ano(conjunto, ano, cache):
        if not caminho.exists():
            continue
        linhas = ler_csv(caminho)
        if len(linhas) < 2:
            continue
        cab = [c.strip() for c in linhas[0]]
        i_ano, i_item, i_cat = cab.index("ANO"), cab.index("Item transferência"), cab.index("Transferência")
        i_d1 = next(i for i, c in enumerate(cab) if c.startswith("1º"))
        corpo = [r for r in linhas[1:] if len(r) > i_cat and r[i_ano].strip() == str(ano)]
        valores = [x for r in corpo for x in r[i_d1:i_d1 + 3] if x not in ("", "0")]
        fator = 0.01 if valores and all("." not in x for x in valores) else 1.0
        for r in corpo:
            item, cat = r[i_item].strip(), r[i_cat].strip()
            if not (item == cat or (item.startswith("CIDE") and cat == "CIDE-Combustíveis") or (item == "IPI-EXP" and cat == "IPI-Exp")):
                continue
            soma[cat] = soma.get(cat, 0.0) + sum(numero(x) for x in r[i_d1:i_d1 + 3]) * fator
    return soma


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ano", type=int, default=ANO)
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "repasses_tesouro")
    a = ap.parse_args()
    a.cache.mkdir(parents=True, exist_ok=True)

    avisos, cobertura, diretos = [], {}, {}
    for conjunto in ("estados", "municipios"):
        print(f"Lendo {conjunto}…")
        _, meses, faltam, av = processar(conjunto, a.ano, a.cache)
        avisos += av
        cobertura[conjunto] = {"meses": meses, "faltam": faltam}
        diretos[conjunto] = somar_diretos(conjunto, a.ano, a.cache)

    itens = []
    for conjunto, cat, id_, nome, destino in ITENS:
        valor = diretos[conjunto].get(cat)
        if valor is None:
            continue
        itens.append({"id": id_, "nome": nome, "destino": destino, "rs": round(valor, 2),
                      "meses": len(cobertura[conjunto]["meses"]), "completo": not cobertura[conjunto]["faltam"]})
    saida = {
        "ano": a.ano,
        "gerado_em": datetime.date.today().isoformat(),
        "fonte": "Tesouro Nacional, Transferências Constitucionais para Estados e para Municípios (Tesouro Transparente)",
        "url": "https://www.tesourotransparente.gov.br/ckan/dataset/transferencias-constitucionais-para-municipios",
        "criterio": "Repasse direto da União, líquido do desconto de 20% que fica retido para o Fundeb. Registro pelo critério de caixa (SIAFI).",
        "cobertura": cobertura,
        "itens": itens,
        "avisos": avisos,
    }
    SAIDA.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Gravado {SAIDA} com {len(itens)} itens e {len(avisos)} avisos.")


if __name__ == "__main__":
    main()
