"""Indicadores macroeconômicos e fiscais do Brasil — séries oficiais, sem estimativas nossas.

Fontes (todas abertas, sem chave):
* Banco Central do Brasil — SGS  https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados
* IBGE — SIDRA                   https://apisidra.ibge.gov.br  (PNAD Contínua, tabela 6381)
* Tesouro Nacional — Tesouro Transparente (CKAN)  https://www.tesourotransparente.gov.br

Regras de qualidade: valores e unidades preservados; nada é interpolado ou estimado; cada série
leva a URL da fonte, a data da consulta e o período de referência. Os códigos do SGS foram
conferidos nos metadados do BCB (dadosabertos.bcb.gov.br) e entre si: nominal = primário + juros.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from fontes import ClienteHTTP, ErroFonte

log = logging.getLogger("impacto.macro")

_http = ClienteHTTP(pausa=0.05, timeout=60.0)
_http_arquivos = ClienteHTTP(pausa=0.05, timeout=240.0)  # CSV/XLSX do Tesouro (10–15 MB)

TTL_S = 6 * 3600
_cache: dict[tuple, tuple[float, Any]] = {}

SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs"
CKAN = "https://www.tesourotransparente.gov.br/ckan/api/3/action"
SIDRA = "https://apisidra.ibge.gov.br/values"
PAGINA_SGS = "https://www3.bcb.gov.br/sgspub/"
PAGINA_TESOURO = "https://www.tesourotransparente.gov.br/ckan/dataset/"


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_travas: dict[tuple, threading.Lock] = defaultdict(threading.Lock)


def _memo(chave: tuple, produzir):  # noqa: ANN001
    """Cache com TTL; requisições simultâneas à mesma fonte esperam uma só consulta (sem baixar 2x)."""
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < TTL_S:
        return _cache[chave][1]
    with _travas[chave]:
        agora = time.time()
        if chave in _cache and agora - _cache[chave][0] < TTL_S:
            return _cache[chave][1]
        valor = produzir()
        _cache[chave] = (agora, valor)
        return valor


# --------------------------------------------------------------------------- #
# Catálogo de séries
# --------------------------------------------------------------------------- #
FISCAL_NOTA = ("Setor público consolidado (Governo Central + estados + municípios + empresas estatais, "
               "exceto Petrobras e Eletrobras), conceito NFSP “sem desvalorização cambial”. "
               "Sinal do BCB: positivo = déficit (necessidade de financiamento); negativo = superávit.")

CATALOGO: dict[str, dict] = {
    "ipca": dict(fonte="bcb", codigo=433, grupo="Inflação", nome="IPCA — variação mensal", unidade="% ao mês",
                 frequencia="mensal", tipo="taxa",
                 mede="Variação mensal do índice oficial de preços ao consumidor (IBGE).",
                 interpreta="Mostra a inflação do mês. Compare com a meta de inflação e com o acumulado em 12 meses."),
    "ipca12": dict(fonte="bcb", codigo=13522, grupo="Inflação", nome="IPCA — acumulado em 12 meses", unidade="% em 12 meses",
                   frequencia="mensal", tipo="taxa",
                   mede="Inflação acumulada nos últimos 12 meses (a referência da meta de inflação).",
                   interpreta="Suaviza a sazonalidade. Difere do IPCA mensal, que mostra só o mês."),
    "selic": dict(fonte="bcb", codigo=432, grupo="Juros", nome="Meta da taxa Selic", unidade="% ao ano",
                  frequencia="diária (muda a cada reunião do Copom)", tipo="taxa", alteracoes=True,
                  mede="Meta para a taxa básica de juros definida pelo Copom.",
                  interpreta="Juros mais altos encarecem o crédito e tendem a esfriar a economia e a inflação; também elevam o custo da dívida pública."),
    "desemprego": dict(fonte="sidra", codigo=6381, grupo="Mercado de trabalho", nome="Taxa de desocupação (PNAD Contínua)",
                       unidade="% da força de trabalho", frequencia="trimestre móvel, divulgada todo mês", tipo="taxa",
                       mede="Pessoas de 14 anos ou mais que procuraram trabalho e não encontraram, como % da força de trabalho.",
                       interpreta="Cada observação cobre um trimestre móvel (ex.: jul-ago-set). Não é o mesmo que o desemprego mensal de outras fontes."),
    "primario": dict(fonte="bcb", codigo=4649, grupo="Resultado fiscal", nome="Resultado primário — fluxo mensal",
                     unidade="R$ milhões", frequencia="mensal", tipo="valor", nota=FISCAL_NOTA,
                     mede="Receitas menos despesas, sem contar juros. Mostra se o governo gasta mais do que arrecada antes de pagar a dívida.",
                     interpreta="Déficit primário = a dívida cresce mesmo sem contar juros. Difere do nominal, que inclui os juros."),
    "juros": dict(fonte="bcb", codigo=4616, grupo="Resultado fiscal", nome="Juros nominais — fluxo mensal",
                  unidade="R$ milhões", frequencia="mensal", tipo="valor", nota=FISCAL_NOTA,
                  mede="Juros pagos e incorridos sobre a dívida pública, incluindo a correção monetária.",
                  interpreta="Sozinho não é déficit: somado ao resultado primário dá o resultado nominal."),
    "nominal": dict(fonte="bcb", codigo=4583, grupo="Resultado fiscal", nome="Resultado nominal — fluxo mensal",
                    unidade="R$ milhões", frequencia="mensal", tipo="valor", nota=FISCAL_NOTA,
                    mede="Resultado primário + juros nominais: quanto o setor público precisou se financiar no mês.",
                    interpreta="É o que faz o estoque da dívida crescer (além de efeitos de câmbio e outros ajustes)."),
    "primario12": dict(fonte="bcb", codigo=5793, grupo="Resultado fiscal", nome="Resultado primário — 12 meses, % do PIB",
                       unidade="% do PIB (acumulado 12 meses)", frequencia="mensal", tipo="taxa", nota=FISCAL_NOTA,
                       mede="Soma dos últimos 12 meses do resultado primário, em proporção do PIB.",
                       interpreta="Permite comparar anos e países, ao contrário do valor em reais."),
    "nominal12": dict(fonte="bcb", codigo=5727, grupo="Resultado fiscal", nome="Resultado nominal — 12 meses, % do PIB",
                      unidade="% do PIB (acumulado 12 meses)", frequencia="mensal", tipo="taxa", nota=FISCAL_NOTA,
                      mede="Soma dos últimos 12 meses do resultado nominal, em proporção do PIB.",
                      interpreta="Inclui os juros: costuma ser bem pior que o primário em anos de juros altos."),
    "juros12": dict(fonte="bcb", codigo=5760, grupo="Resultado fiscal", nome="Juros nominais — 12 meses, % do PIB",
                    unidade="% do PIB (acumulado 12 meses)", frequencia="mensal", tipo="taxa", nota=FISCAL_NOTA,
                    mede="Soma dos últimos 12 meses dos juros nominais, em proporção do PIB.",
                    interpreta="Mostra o peso dos juros na economia."),
    "dlsp_pib": dict(fonte="bcb", codigo=4513, grupo="Dívida pública", nome="Dívida líquida do setor público — % do PIB",
                     unidade="% do PIB (saldo)", frequencia="mensal", tipo="taxa",
                     nota="Dívida LÍQUIDA do setor público consolidado: desconta ativos (reservas, créditos). Não é o mesmo que a Dívida Pública Federal (bruta, só da União).",
                     mede="Saldo da dívida do setor público descontados os ativos financeiros, como % do PIB.",
                     interpreta="Estoque, não fluxo: sobe com déficits nominais. Não confundir com a dívida bruta do governo geral nem com a DPF."),
    "dlsp_rs": dict(fonte="bcb", codigo=4478, grupo="Dívida pública", nome="Dívida líquida do setor público — R$ milhões",
                    unidade="R$ milhões (saldo)", frequencia="mensal", tipo="valor",
                    nota="Dívida LÍQUIDA do setor público consolidado.",
                    mede="Saldo em reais da dívida líquida do setor público.", interpreta="Mesmo indicador do % do PIB, em reais correntes."),
}

# Séries mensais podem ir bem longe; a Selic (diária) é buscada em janelas de até 10 anos.
INICIO_PADRAO = date(2010, 1, 1)


def _iso(d: str) -> str:
    dia, mes, ano = d.split("/")
    return f"{ano}-{mes}-{dia}"


def _num(txt: Any) -> float | None:
    try:
        return float(str(txt).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _bcb(codigo: int) -> list[dict]:
    """Série completa do SGS desde INICIO_PADRAO (janelas ≤ 10 anos), sem alterar valores."""
    def produzir() -> list[dict]:
        hoje = date.today()
        pontos: list[dict] = []
        ano = INICIO_PADRAO.year
        while ano <= hoje.year:
            fim_ano = min(ano + 9, hoje.year)
            dados = _http.json(f"{SGS}.{codigo}/dados", {
                "formato": "json", "dataInicial": f"01/01/{ano}",
                "dataFinal": f"{hoje.day:02d}/{hoje.month:02d}/{hoje.year}" if fim_ano == hoje.year else f"31/12/{fim_ano}"})
            if isinstance(dados, list):
                for p in dados:
                    v = _num(p.get("valor"))
                    if v is not None:  # lacuna da fonte: simplesmente não há ponto
                        pontos.append({"data": _iso(p["data"]), "valor": v})
            ano = fim_ano + 1
        unicos = {p["data"]: p for p in pontos}
        return [unicos[k] for k in sorted(unicos)]
    return _memo(("bcb", codigo), produzir)


def _sidra_desemprego() -> list[dict]:
    def produzir() -> list[dict]:
        linhas = _http.json(f"{SIDRA}/t/6381/n1/all/v/4099/p/all", {"formato": "json"})
        pontos = []
        for r in linhas[1:]:
            v = _num(r.get("V"))
            cod = str(r.get("D3C", ""))
            if v is None or len(cod) != 6:
                continue
            pontos.append({"data": f"{cod[:4]}-{cod[4:]}-01", "valor": v, "referencia": r.get("D3N")})
        return sorted(pontos, key=lambda p: p["data"])
    return _memo(("sidra", 6381), produzir)


def _so_alteracoes(pontos: list[dict]) -> list[dict]:
    """Selic: mantém só os dias em que a meta mudou (+ o último dia observado)."""
    saida: list[dict] = []
    for p in pontos:
        if not saida or p["valor"] != saida[-1]["valor"]:
            saida.append(dict(p))
    if pontos and saida[-1]["data"] != pontos[-1]["data"]:
        saida.append({**pontos[-1], "mantida": True})  # marca “meta mantida até aqui”
    return saida


def serie(chave: str, inicio: str | None = None, fim: str | None = None) -> dict:
    meta = CATALOGO.get(chave)
    if not meta:
        raise KeyError(chave)
    pontos = _sidra_desemprego() if meta["fonte"] == "sidra" else _bcb(meta["codigo"])
    if meta.get("alteracoes"):
        pontos = _so_alteracoes(pontos)
    if inicio:
        pontos = [p for p in pontos if p["data"] >= inicio]
    if fim:
        pontos = [p for p in pontos if p["data"] <= fim]
    if meta["fonte"] == "bcb":
        url = f"{SGS}.{meta['codigo']}/dados?formato=json"
        fonte = f"Banco Central do Brasil — SGS, série {meta['codigo']}"
    else:
        url = f"{SIDRA}/t/6381/n1/all/v/4099/p/all"
        fonte = "IBGE — SIDRA, tabela 6381 (PNAD Contínua), variável 4099"
    return {
        "id": chave, **{k: meta[k] for k in ("nome", "unidade", "frequencia", "grupo", "tipo", "mede", "interpreta")},
        "nota": meta.get("nota"), "codigo": meta["codigo"], "fonte": fonte, "url_fonte": url,
        "consultado_em": _agora(),
        "periodo_referencia": {"inicio": pontos[0]["data"] if pontos else None, "fim": pontos[-1]["data"] if pontos else None},
        "pontos": pontos,
    }


def catalogo() -> list[dict]:
    return [{"id": k, **{c: v[c] for c in ("nome", "unidade", "frequencia", "grupo", "tipo", "mede", "interpreta")},
             "codigo": v["codigo"], "nota": v.get("nota")} for k, v in CATALOGO.items()]


def resumo() -> dict:
    """Valor mais recente de cada indicador (com o anterior, para a variação)."""
    cartoes, erros = [], []
    for chave in CATALOGO:
        try:
            s = serie(chave)
        except Exception as exc:  # noqa: BLE001 — uma fonte fora do ar não derruba as outras
            log.warning("série %s indisponível: %s", chave, exc)
            erros.append({"id": chave, "erro": "Fonte indisponível no momento."})
            continue
        if not s["pontos"]:
            continue
        ult = s["pontos"][-1]
        ant = s["pontos"][-2] if len(s["pontos"]) > 1 else None
        cartoes.append({"id": chave, "nome": s["nome"], "unidade": s["unidade"], "grupo": s["grupo"],
                        "frequencia": s["frequencia"], "valor": ult["valor"], "data": ult["data"],
                        "referencia": ult.get("referencia"), "anterior": ant["valor"] if ant else None,
                        "fonte": s["fonte"], "url_fonte": s["url_fonte"]})
    return {"consultado_em": _agora(), "cartoes": cartoes, "erros": erros}


# --------------------------------------------------------------------------- #
# Tesouro Transparente (CKAN → CSV/XLSX)
# --------------------------------------------------------------------------- #
def _recurso(pacote: str, formato: str) -> dict:
    p = _http.json(f"{CKAN}/package_show", {"id": pacote})
    recursos = [r for r in p["result"]["resources"] if (r.get("format") or "").upper() == formato]
    if not recursos:
        raise ErroFonte(f"Conjunto {pacote} sem arquivo {formato}")
    return {"url": recursos[0]["url"], "atualizado": p["result"].get("metadata_modified"),
            "titulo": p["result"].get("title"), "pagina": PAGINA_TESOURO + pacote}


def _csv(pacote: str) -> tuple[list[list[str]], dict]:
    rec = _recurso(pacote, "CSV")
    bruto = _http_arquivos.get(rec["url"], accept="*/*").content
    try:
        texto = bruto.decode("utf-8")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    leitor = csv.reader(io.StringIO(texto), delimiter=";")
    next(leitor)
    return list(leitor), rec


def _br(txt: str) -> float | None:
    try:
        return float(txt.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _mes_iso(mm_aaaa: str) -> str:
    mm, aaaa = mm_aaaa.split("/")
    return f"{aaaa}-{mm}"


def _grupo_titulo(nome: str) -> str:
    m = re.match(r"^[A-Za-zÀ-ú]+(?:-[A-Za-z]+)?", nome.strip())
    return m.group(0).upper() if m else "OUTROS"


def _estoque_dpf() -> dict:
    def produzir() -> dict:
        linhas, rec = _csv("estoque-da-divida-publica-federal")
        total: dict[str, float] = defaultdict(float)
        por_classe: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        por_tipo: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        por_titulo: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        venc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in linhas:
            if len(r) < 7:
                continue
            v = _br(r[2])
            if v is None:
                continue
            mes = _mes_iso(r[4])
            total[mes] += v
            por_classe[mes][r[5]] += v
            por_tipo[mes][r[6]] += v
            por_titulo[mes][_grupo_titulo(r[0])] += v
            venc[mes][r[1][-4:] if re.search(r"\d{4}$", r[1]) else "s/d"] += v
        meses = sorted(total)
        ultimo = meses[-1]
        titulos = sorted(por_titulo[ultimo].items(), key=lambda kv: -kv[1])
        principais, resto = titulos[:8], sum(v for _, v in titulos[8:])
        composicao = [{"nome": n, "valor": round(v, 2)} for n, v in principais]
        if resto:
            composicao.append({"nome": "Demais", "valor": round(resto, 2)})
        perfil = [{"ano": a, "valor": round(v, 2)} for a, v in sorted(venc[ultimo].items())]
        return {
            "referencia": ultimo, "atualizado": rec["atualizado"], "pagina": rec["pagina"], "url": rec["url"],
            "evolucao": [{"mes": m, "valor": round(total[m], 2)} for m in meses],
            "total": round(total[ultimo], 2),
            "por_tipo": [{"nome": n, "valor": round(v, 2)} for n, v in por_tipo[ultimo].items()],
            "por_classe": [{"nome": n, "valor": round(v, 2)} for n, v in por_classe[ultimo].items()],
            "composicao": composicao, "vencimentos": perfil,
            "total_por_mes": {m: total[m] for m in meses},
        }
    return _memo(("dpf",), produzir)


def _emissoes_resgates() -> dict:
    def produzir() -> dict:
        from openpyxl import load_workbook

        rec = _recurso("emissoes-e-resgates-divida-publica-federal", "XLSX")
        bruto = _http_arquivos.get(rec["url"], accept="*/*").content
        ws = load_workbook(io.BytesIO(bruto), read_only=True, data_only=True).worksheets[0]
        linhas = list(ws.iter_rows(values_only=True))
        cab = next(r for r in linhas if sum(bool(isinstance(c, str) and re.fullmatch(r"[A-Za-zç]{3}/\d{2}", c.strip())) for c in r) > 12)
        meses_pt = {"Jan": 1, "Fev": 2, "Mar": 3, "Abr": 4, "Mai": 5, "Jun": 6, "Jul": 7, "Ago": 8, "Set": 9, "Out": 10, "Nov": 11, "Dez": 12}
        colunas: list[tuple[int, str]] = []
        for i, c in enumerate(cab):
            if isinstance(c, str) and re.fullmatch(r"[A-Za-zç]{3}/\d{2}", c.strip()):
                mes, ano = c.strip().split("/")
                if mes.capitalize() in meses_pt:
                    colunas.append((i, f"20{ano}-{meses_pt[mes.capitalize()]:02d}"))

        def linha(rotulo: str, ocorrencia: int = 0) -> dict[str, float | None]:
            achadas = [r for r in linhas if isinstance(r[0], str) and r[0].strip().startswith(rotulo)]
            r = achadas[ocorrencia]
            return {m: (float(r[i]) if isinstance(r[i], (int, float)) else None) for i, m in colunas}

        emissoes, resgates = linha("EMISSÕES"), linha("RESGATES")
        meses = [m for _, m in colunas]
        return {
            "unidade": "R$ milhões (conforme a planilha do Tesouro)", "atualizado": rec["atualizado"],
            "pagina": rec["pagina"], "url": rec["url"],
            "serie": [{"mes": m, "emissoes": emissoes.get(m), "resgates": resgates.get(m)} for m in meses
                      if emissoes.get(m) is not None or resgates.get(m) is not None],
        }
    return _memo(("emissoes",), produzir)


def _tesouro_direto() -> dict:
    def produzir() -> dict:
        linhas, rec_e = _csv("estoque-do-tesouro-direto")
        total: dict[str, float] = defaultdict(float)
        por_tipo: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in linhas:
            if len(r) < 6:
                continue
            v = _br(r[5])
            if v is None:
                continue
            mes = _mes_iso(r[2])
            total[mes] += v
            por_tipo[mes][r[0]] += v
        meses = sorted(total)
        ultimo = meses[-1]
        taxas_linhas, rec_t = _csv("taxas-dos-titulos-ofertados-pelo-tesouro-direto")

        def chave_data(d: str) -> str:
            dia, mes, ano = d.split("/")
            return f"{ano}-{mes}-{dia}"

        data_base = max((r[2] for r in taxas_linhas if len(r) >= 7), key=chave_data)
        taxas = []
        for r in taxas_linhas:
            if len(r) >= 7 and r[2] == data_base:
                taxas.append({"titulo": r[0], "vencimento": _iso(r[1]), "taxa_compra": _br(r[3]), "taxa_venda": _br(r[4]),
                              "pu_compra": _br(r[5]), "pu_venda": _br(r[6])})
        taxas.sort(key=lambda t: (t["titulo"], t["vencimento"]))
        return {
            "estoque": {"referencia": ultimo, "total": round(total[ultimo], 2),
                        "por_tipo": [{"nome": n, "valor": round(v, 2)} for n, v in sorted(por_tipo[ultimo].items(), key=lambda kv: -kv[1])],
                        "evolucao": [{"mes": m, "valor": round(total[m], 2)} for m in meses],
                        "atualizado": rec_e["atualizado"], "pagina": rec_e["pagina"], "url": rec_e["url"],
                        "total_por_mes": {m: total[m] for m in meses}},
            "taxas": {"data_base": _iso(data_base), "titulos": taxas, "atualizado": rec_t["atualizado"],
                      "pagina": rec_t["pagina"], "url": rec_t["url"]},
        }
    return _memo(("td",), produzir)


def divida() -> dict:
    """Dívida Pública Federal (bruta) e, separadamente, a parcela do Tesouro Direto."""
    partes: dict[str, Any] = {"consultado_em": _agora(), "erros": []}

    try:
        d = _estoque_dpf()
        partes["dpf"] = {k: v for k, v in d.items() if k != "total_por_mes"}
    except Exception as exc:  # noqa: BLE001
        log.warning("DPF indisponível: %s", exc)
        d = None
        partes["erros"].append("Estoque da Dívida Pública Federal indisponível no momento.")

    try:
        e = _emissoes_resgates()
        partes["emissoes_resgates"] = e
    except Exception as exc:  # noqa: BLE001
        log.warning("Emissões/resgates indisponíveis: %s", exc)
        partes["erros"].append("Emissões e resgates da DPF indisponíveis no momento.")

    try:
        td = _tesouro_direto()
        est = td["estoque"]
        partes["tesouro_direto"] = {"estoque": {k: v for k, v in est.items() if k != "total_por_mes"}, "taxas": td["taxas"]}
        if d and est["referencia"] in d["total_por_mes"]:
            ref = est["referencia"]
            partes["tesouro_direto"]["participacao_na_dpf"] = {
                "mes": ref, "tesouro_direto": est["total_por_mes"][ref], "dpf": d["total_por_mes"][ref],
                "pct": round(100 * est["total_por_mes"][ref] / d["total_por_mes"][ref], 2)}
    except Exception as exc:  # noqa: BLE001
        log.warning("Tesouro Direto indisponível: %s", exc)
        partes["erros"].append("Dados do Tesouro Direto indisponíveis no momento.")
    return partes
