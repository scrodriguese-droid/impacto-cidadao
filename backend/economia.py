"""Economia do setor público municipal — dados do SICONFI (Tesouro Nacional).

Fonte: https://apidatalake.tesouro.gov.br/ords/siconfi/tt/  (aberta, sem chave)

* RREO Anexo 01 — balanço orçamentário: receitas e despesas por categoria
                  (previsto, empenhado, liquidado, pago);
* RREO Anexo 02 — despesas por função (Saúde, Educação, Segurança…);
* RREO Anexo 03 — receita corrente líquida (IPTU, ISS, ITBI, FPM, ICMS…);
* RGF  Anexo 01 — despesa com pessoal × limite da Lei de Responsabilidade Fiscal.

Os mínimos constitucionais de Saúde (15%) e Educação (25%) ficam nos anexos 12 e 08 do RREO,
que a API aberta não devolve para os municípios testados; por isso mostramos apenas a fatia
da despesa por função e declaramos a lacuna, sem inventar o percentual.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import macro
from fontes import DISJUNTOR_BCB, ClienteHTTP, ErroFonte, normalizar

log = logging.getLogger("impacto.economia")

BASE = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt"
_http = ClienteHTTP(pausa=0.05, timeout=60.0)
_http_sgs = ClienteHTTP(pausa=0.05, timeout=12.0, tentativas=1)   # o SGS segura pedidos recusados por ~20 s

TTL_S = 6 * 3600
_cache: dict[tuple, tuple[float, Any]] = {}

# Funções de governo (Portaria MOG 42/1999) — distingue função de subfunção em "conta".
FUNCOES = {
    "legislativa": "Legislativo", "judiciaria": "Judiciário", "essencialajustica": "Justiça",
    "administracao": "Administração", "defesanacional": "Defesa", "segurancapublica": "Segurança pública",
    "relacoesexteriores": "Relações exteriores", "assistenciasocial": "Assistência social",
    "previdenciasocial": "Previdência", "saude": "Saúde", "trabalho": "Trabalho", "educacao": "Educação",
    "cultura": "Cultura", "direitosdacidadania": "Direitos da cidadania", "urbanismo": "Urbanismo",
    "habitacao": "Habitação", "saneamento": "Saneamento", "gestaoambiental": "Meio ambiente",
    "cienciaetecnologia": "Ciência e tecnologia", "agricultura": "Agricultura",
    "organizacaoagraria": "Organização agrária", "industria": "Indústria",
    "comercioeservicos": "Comércio e serviços", "comunicacoes": "Comunicações", "energia": "Energia",
    "transporte": "Transporte", "desportoelazer": "Esporte e lazer", "encargosespeciais": "Encargos especiais",
}
ICONES = {
    "Saúde": "fa-heart-pulse", "Educação": "fa-graduation-cap", "Segurança pública": "fa-shield-halved",
    "Urbanismo": "fa-city", "Transporte": "fa-bus", "Assistência social": "fa-hand-holding-heart",
    "Saneamento": "fa-droplet", "Administração": "fa-building-columns", "Previdência": "fa-person-cane",
    "Meio ambiente": "fa-leaf", "Cultura": "fa-masks-theater", "Esporte e lazer": "fa-futbol",
    "Habitação": "fa-house", "Legislativo": "fa-landmark",
}


def _itens(endpoint: str, **params: Any) -> list[dict]:
    """Todas as linhas de um demonstrativo (paginação do ORDS)."""
    chave = (endpoint, tuple(sorted(params.items())))
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < TTL_S:
        return _cache[chave][1]
    saida: list[dict] = []
    deslocamento = 0
    while True:
        pagina = _http.json(f"{BASE}/{endpoint}", {**params, "offset": deslocamento, "limit": 5000})
        saida += pagina.get("items", [])
        if not pagina.get("hasMore"):
            break
        deslocamento += 5000
    _cache[chave] = (agora, saida)
    return saida


def _periodo_mais_recente(ente: int) -> tuple[int, int] | None:
    """(ano, bimestre) mais recente com RREO Anexo 01 publicado."""
    hoje = date.today()
    for ano in (hoje.year, hoje.year - 1, hoje.year - 2):
        for bim in range(6, 0, -1):
            try:
                achado = _http.json(f"{BASE}/rreo", {
                    "an_exercicio": ano, "nr_periodo": bim, "co_tipo_demonstrativo": "RREO",
                    "id_ente": ente, "no_anexo": "RREO-Anexo 01", "limit": 1})
            except ErroFonte:
                continue
            if achado.get("items"):
                return ano, bim
    return None


def _rreo(ente: int, ano: int, bim: int, anexo: str) -> list[dict]:
    return _itens("rreo", an_exercicio=ano, nr_periodo=bim, co_tipo_demonstrativo="RREO",
                  id_ente=ente, no_anexo=anexo)


def _valor(linhas: list[dict], conta: str, coluna_inicio: str) -> float | None:
    """Valor da primeira linha cujo cod_conta é `conta` e cuja coluna começa por `coluna_inicio`."""
    alvo = normalizar(coluna_inicio)
    for r in linhas:
        if r.get("cod_conta") == conta and normalizar(r.get("coluna", "")).startswith(alvo):
            return r.get("valor")
    return None


def _num(v: float | None) -> float:
    return round(float(v or 0), 2)


def _pct(parte: float, total: float) -> float | None:
    return round(100 * parte / total, 1) if total else None


# --------------------------------------------------------------------------- #
# Blocos
# --------------------------------------------------------------------------- #
def _balanco(a1: list[dict]) -> dict:
    receita = _num(_valor(a1, "TotalReceitas", "Até o Bimestre"))
    previsao = _num(_valor(a1, "TotalReceitas", "PREVISÃO ATUALIZADA"))
    despesa = _num(_valor(a1, "TotalDespesas", "DESPESAS EMPENHADAS ATÉ"))
    saldo = round(receita - despesa, 2)
    return {
        "receita_arrecadada": receita, "receita_prevista": previsao, "despesa_empenhada": despesa,
        "resultado": saldo, "situacao": "superavit" if saldo >= 0 else "deficit",
    }


def _esfera(ente: int) -> str:
    """1 = União; 2 dígitos = Estado/DF (código IBGE da UF); 7 dígitos = município."""
    return "uniao" if ente == 1 else "estado" if ente < 100 else "municipio"


# (rótulo, cod_conta no RREO Anexo 03) por esfera — últimos 12 meses
_PROPRIOS_A3 = {
    "municipio": (("IPTU", "IPTULiquidoExcetoTransferenciasEFUNDEB"), ("ISS", "ISSLiquidoExcetoTransferenciasEFUNDEB"),
                  ("ITBI", "ITBILiquidoExcetoTransferenciasEFUNDEB"), ("IRRF", "IRRFLiquidoExcetoTransferenciasEFUNDEB")),
    "estado": (("ICMS", "ICMSLiquidoExcetoTransferenciasEFUNDEB"), ("IPVA", "IPVALiquidoExcetoTransferenciasEFUNDEB"),
               ("ITCD", "ITCDLiquidoExcetoTransferenciasEFUNDEB"), ("IRRF", "IRRFLiquidoExcetoTransferenciasEFUNDEB")),
    "uniao": (),
}
_REPASSES_A3 = {
    "municipio": (("FPM (Fundo de Participação)", "RREO3CotaParteDoFPM"), ("ICMS (cota do Estado)", "RREO3CotaParteDoICMS"),
                  ("IPVA (cota do Estado)", "RREO3CotaParteDoIPVA"), ("FUNDEB", "RREO3TransferenciasDoFUNDEB")),
    "estado": (("FPE (Fundo de Participação)", "RREO3CotaParteDoFPE"), ("FUNDEB", "RREO3TransferenciasDoFUNDEB")),
    "uniao": (),
}


def _receitas(a1: list[dict], a3: list[dict], esfera: str = "municipio") -> dict:
    def r1(cod: str) -> float:
        return _num(_valor(a1, cod, "Até o Bimestre"))

    def r3(cod: str) -> float:
        return _num(_valor(a3, cod, "TOTAL (ÚLTIMOS 12 MESES)"))

    total = r1("TotalReceitas")
    grupos = [
        {"id": "proprios", "nome": "Impostos próprios", "valor": r1("Impostos"),
         "detalhe": [{"nome": n, "valor": r3(c)} for n, c in _PROPRIOS_A3[esfera] if r3(c)],
         "detalhe_periodo": "últimos 12 meses"},
        {"id": "transferencias", "nome": "Transferências e repasses",
         "valor": r1("TransferenciasCorrentes") + r1("TransferenciasDeCapital"),
         "detalhe": [{"nome": n, "valor": r3(c)} for n, c in _REPASSES_A3[esfera] if r3(c)],
         "detalhe_periodo": "últimos 12 meses"},
        {"id": "outras", "nome": "Outras receitas",
         "valor": r1("Taxas") + r1("ReceitaDeContribuicoes") + r1("ReceitaPatrimonial") + r1("ReceitaDeServicos")
         + r1("OutrasReceitasCorrentes") + r1("AlienacaoDeBens") + r1("OutrasReceitasDeCapital")
         + r1("ReceitasIntraOrcamentariasTotal"),
         "detalhe": [{"nome": n, "valor": v} for n, v in (
             ("Taxas", r1("Taxas")), ("Contribuições", r1("ReceitaDeContribuicoes")),
             ("Patrimônio e serviços", r1("ReceitaPatrimonial") + r1("ReceitaDeServicos")),
             ("Multas e juros", r1("MultasEJurosDeMora")), ("Venda de bens", r1("AlienacaoDeBens"))) if v],
         "detalhe_periodo": "acumulado no exercício"},
        {"id": "credito", "nome": "Empréstimos (operações de crédito)", "valor": r1("ReceitasDeOperacoesDeCredito") + r1("OperacoesDeCreditoRefinanciamento"),
         "detalhe": [], "detalhe_periodo": ""},
    ]
    for g in grupos:
        g["valor"] = round(g["valor"], 2)
        g["pct"] = _pct(g["valor"], total)
    return {"total": total, "grupos": grupos}


def _despesas(a1: list[dict], a2: list[dict]) -> dict:
    def d(cod: str, col: str = "DESPESAS LIQUIDADAS ATÉ") -> float:
        return _num(_valor(a1, cod, col))

    total = d("TotalDespesas")
    pessoal = d("PessoalEEncargosSociais") + d("PessoalEEncargosSociaisIntra")
    divida = (d("JurosEEncargosDaDivida") + d("AmortizacaoDaDivida") + d("JurosEEncargosDaDividaIntra")
              + d("AmortizacaoDaDividaIntra") + d("AmortizacaoRefinanciamentoDaDivida"))
    invest = d("Investimentos") + d("InversoesFinanceiras")
    custeio = round(total - invest - divida, 2)
    tipos = [
        {"id": "custeio", "nome": "Custeio (salários e manutenção)", "valor": custeio, "extra": {"pessoal": round(pessoal, 2)}},
        {"id": "investimento", "nome": "Investimentos (obras e equipamentos)", "valor": round(invest, 2)},
        {"id": "divida", "nome": "Serviço da dívida (juros e amortização)", "valor": round(divida, 2)},
    ]
    for t in tipos:
        t["pct"] = _pct(t["valor"], total)

    funcoes: dict[str, float] = {}
    for r in a2:
        if r.get("cod_conta") != "RREO2TotalDespesas":
            continue
        if not normalizar(r.get("coluna", "")).startswith(normalizar("DESPESAS LIQUIDADAS ATÉ")):
            continue
        nome = FUNCOES.get(normalizar(r.get("conta", "")))
        if nome and nome not in funcoes:
            funcoes[nome] = _num(r.get("valor"))
    soma = sum(funcoes.values()) or total
    por_funcao = sorted(
        ({"nome": n, "valor": v, "pct": _pct(v, soma), "icone": ICONES.get(n, "fa-circle")} for n, v in funcoes.items() if v > 0),
        key=lambda x: -x["valor"])
    return {"total_liquidado": total, "tipos": tipos, "funcoes": por_funcao}


def _execucao(a1: list[dict]) -> dict:
    return {
        "dotado": _num(_valor(a1, "TotalDespesas", "DOTAÇÃO ATUALIZADA")),
        "dotacao_inicial": _num(_valor(a1, "TotalDespesas", "DOTAÇÃO INICIAL")),
        "empenhado": _num(_valor(a1, "TotalDespesas", "DESPESAS EMPENHADAS ATÉ")),
        "liquidado": _num(_valor(a1, "TotalDespesas", "DESPESAS LIQUIDADAS ATÉ")),
        "pago": _num(_valor(a1, "TotalDespesas", "DESPESAS PAGAS ATÉ")),
    }


def _pessoal(ente: int, ano: int) -> dict | None:
    """Despesa com pessoal (RGF, Poder Executivo) no último quadrimestre publicado."""
    for ano_rgf in (ano, ano - 1):
        for quad in (3, 2, 1):
            try:
                linhas = _itens("rgf", an_exercicio=ano_rgf, in_periodicidade="Q", nr_periodo=quad,
                                co_tipo_demonstrativo="RGF", id_ente=ente, no_anexo="RGF-Anexo 01", co_poder="E")
            except ErroFonte:
                continue
            if ente == 1:  # o RGF federal traz uma linha por órgão; o limite que interessa é o da União
                linhas = [r for r in linhas if r.get("rotulo") == "União"]
            uso = _valor(linhas, "DespesaComPessoalTotal", "% sobre a RCL")
            if uso is None:
                continue
            return {
                "pct_rcl": uso,
                "limite_maximo": _valor(linhas, "LimiteMaximoDespesaComPessoalTotal", "% sobre a RCL"),
                "limite_prudencial": _valor(linhas, "LimitePrudencialDespesaComPessoalTotal", "% sobre a RCL"),
                "limite_alerta": _valor(linhas, "LimiteDeAlertaDespesaComPessoalTotal", "% sobre a RCL"),
                "rcl": _num(_valor(linhas, "ReceitaCorrenteLiquidaLimiteLegal", "Valor")),
                "referencia": f"{quad}º quadrimestre de {ano_rgf}",
            }
    return None


def _alerta_lrf(p: dict | None) -> dict | None:
    if not p:
        return None
    uso, lim, pru, ale = p["pct_rcl"], p["limite_maximo"], p["limite_prudencial"], p["limite_alerta"]
    if lim is None:
        nivel = "indefinido"
    elif uso > lim:
        nivel = "acima_limite"
    elif pru is not None and uso > pru:
        nivel = "prudencial"
    elif ale is not None and uso > ale:
        nivel = "alerta"
    else:
        nivel = "ok"
    return {**p, "nivel": nivel}


# --------------------------------------------------------------------------- #
# Déficits: nominal, primário e operacional
# --------------------------------------------------------------------------- #
BCB_SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs"
IPCA_MENSAL = 433  # IPCA — variação mensal (%), IBGE/BCB


def _ipca_acumulado(ano: int, mes_fim: int) -> float | None:
    """IPCA acumulado de janeiro até `mes_fim` (em %), pela composição das variações mensais (SGS 433)."""
    chave = ("ipca", ano, mes_fim)
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < TTL_S:
        return _cache[chave][1]
    try:
        DISJUNTOR_BCB.exigir_fechado()
        serie = _http_sgs.json(f"{BCB_SGS}.{IPCA_MENSAL}/dados", {
            "formato": "json", "dataInicial": f"01/01/{ano}", "dataFinal": f"28/{mes_fim:02d}/{ano}"})
        DISJUNTOR_BCB.ok()
    except ErroFonte:
        DISJUNTOR_BCB.falhou()
        return None
    if not isinstance(serie, list) or len(serie) < mes_fim:
        return None  # série incompleta: não estimamos os meses que faltam
    fator = 1.0
    for ponto in serie[:mes_fim]:
        fator *= 1 + float(ponto["valor"]) / 100
    resultado = round((fator - 1) * 100, 2)
    _cache[chave] = (agora, resultado)
    return resultado


def _deficits(a1: list[dict], a6: list[dict], ano: int, bim: int) -> dict:
    """Déficit nominal, primário e operacional (positivo = déficit; negativo = superávit).

    * nominal    = despesa total empenhada − receita total arrecadada (RREO Anexo 01);
    * primário   = despesa primária empenhada − receita primária realizada (RREO Anexo 06, a
                   classificação oficial de receitas e despesas financeiras). Sem o Anexo 06,
                   aproxima pelo Anexo 01 excluindo crédito, juros, amortização e inversões financeiras;
    * operacional = primário + juros reais, com juros reais = juros nominais − correção monetária
                   (IPCA acumulado do ano × dívida consolidada no início do ano). Cálculo NOSSO, não
                   é série oficial; fica indisponível quando falta a dívida consolidada ou o IPCA.
    """
    def r1(cod: str) -> float:
        return _num(_valor(a1, cod, "Até o Bimestre"))

    def d1(cod: str) -> float:
        return _num(_valor(a1, cod, "DESPESAS EMPENHADAS ATÉ"))

    receita, despesa = r1("TotalReceitas"), d1("TotalDespesas")
    nominal = round(despesa - receita, 2)

    rec_prim = _valor(a6, "RREO6TotalReceitaPrimaria", "RECEITAS REALIZADAS")
    des_prim = _valor(a6, "RREO6TotalDespesaPrimaria", "DESPESAS EMPENHADAS")
    if rec_prim is not None and des_prim is not None:
        primario = round(float(des_prim) - float(rec_prim), 2)
        base_primario = "RREO Anexo 06 (receitas e despesas primárias, classificação oficial)"
        receitas_nf, gastos_nf = round(float(rec_prim), 2), round(float(des_prim), 2)
    else:
        rec_fin = (r1("ReceitasDeOperacoesDeCredito") + r1("OperacoesDeCreditoRefinanciamento")
                   + r1("AmortizacoesDeEmprestimos") + r1("ReceitasDeValoresMobiliarios"))
        des_fin = (d1("JurosEEncargosDaDivida") + d1("JurosEEncargosDaDividaIntra") + d1("AmortizacaoDaDivida")
                   + d1("AmortizacaoDaDividaIntra") + d1("AmortizacaoRefinanciamentoDaDivida")
                   + d1("InversoesFinanceiras") + d1("InversoesFinanceirasIntra"))
        receitas_nf, gastos_nf = round(receita - rec_fin, 2), round(despesa - des_fin, 2)
        primario = round(gastos_nf - receitas_nf, 2)
        base_primario = "aproximação pelo RREO Anexo 01 (o Anexo 06 não está disponível)"

    # Juros nominais: preferimos os "incorridos, incluindo variações monetárias" (Anexo 06), que são
    # comparáveis à correção monetária; sem eles, usamos os juros empenhados (Anexo 01).
    incorridos = _valor(a6, "JurosEncargosEVariacoesMonetariasPassivosExcetoRPPS", "VALOR INCORRIDO")
    if incorridos is not None:
        juros, juros_base = round(float(incorridos), 2), "juros, encargos e variações monetárias incorridos (RREO Anexo 06)"
    else:
        juros = round(d1("JurosEEncargosDaDivida") + d1("JurosEEncargosDaDividaIntra"), 2)
        juros_base = "juros e encargos empenhados (RREO Anexo 01; não inclui variação monetária)"
    divida = None
    for r in a6:
        if r.get("cod_conta") == "DividaConsolidada" and normalizar(r.get("coluna", "")).startswith(normalizar("Em 31/12")):
            divida = r.get("valor")
            break
    ipca = _ipca_acumulado(ano, bim * 2)
    operacional = None
    componentes: dict[str, Any] = {"juros_nominais": juros, "juros_base": juros_base, "divida_inicio_ano": divida, "ipca_acumulado_pct": ipca}
    motivo = None
    if divida is None:
        motivo = "Sem o saldo da dívida consolidada no início do ano nesta fonte, não é possível separar os juros reais."
    elif ipca is None:
        motivo = "A série do IPCA (Banco Central) está indisponível ou incompleta para o período."
    else:
        correcao = round(float(divida) * ipca / 100, 2)
        juros_reais = round(juros - correcao, 2)
        operacional = round(primario + juros_reais, 2)
        componentes.update({"correcao_monetaria": correcao, "juros_reais": juros_reais})
    return {
        "periodo": f"acumulado até o {bim}º bimestre de {ano}",
        "nominal": {"valor": nominal, "gastos_totais": despesa, "receitas_totais": receita},
        "primario": {"valor": primario, "gastos_nao_financeiros": gastos_nf,
                     "receitas_nao_financeiras": receitas_nf, "base": base_primario},
        "operacional": {"valor": operacional, "indisponivel_motivo": motivo, **componentes},
    }


# --------------------------------------------------------------------------- #
# Situação fiscal em 4 dimensões (fluxo, estoque, custo e capacidade)
# --------------------------------------------------------------------------- #
def _capacidade_uniao() -> dict:
    """Receita e despesa primárias do Governo Federal no último exercício encerrado, em % do PIB."""
    ano = date.today().year - 1
    try:
        a6 = _rreo(1, ano, 6, "RREO-Anexo 06")
    except ErroFonte:
        a6 = []
    rec = _valor(a6, "RREO6TotalReceitaPrimaria", "RECEITAS REALIZADAS")
    des = _valor(a6, "RREO6TotalDespesaPrimaria", "DESPESAS EMPENHADAS")
    pib = None
    try:
        pib = macro.pib_12_meses(ano)
    except Exception:  # noqa: BLE001 — série do BCB fora do ar: a dimensão fica indisponível
        pib = None
    if rec is None or des is None or not pib:
        return {"itens": [], "indisponivel": "Não foi possível obter a receita e a despesa primárias do último exercício ou o PIB de referência."}
    return {
        "periodo": f"exercício de {ano} (Governo Federal, sem operações financeiras)",
        "itens": [
            {"id": "receita", "nome": "Receita primária total", "valor_rs": float(rec), "pct": round(float(rec) / pib * 100, 1), "rotulo_pct": "% do PIB"},
            {"id": "despesa", "nome": "Despesa primária total", "valor_rs": float(des), "pct": round(float(des) / pib * 100, 1), "rotulo_pct": "% do PIB"},
        ],
        "indisponivel": None,
        "nota": "O PIB de referência é derivado do saldo da dívida líquida (R$ ÷ % do PIB) divulgado pelo BCB. A carga tributária bruta não tem série aberta de API.",
    }


def _situacao_subnacional(ente: int, a1: list[dict], a6: list[dict], deficits: dict, pessoal: dict | None, instituicao: str | None) -> dict:
    prim, nom = deficits["primario"]["valor"], deficits["nominal"]["valor"]
    itens_fluxo = [
        {"id": "primario", "nome": "Primário (sem juros)", "valor_rs": prim, "pct_pib": None, "situacao": "deficit" if prim > 0 else "superavit"},
        {"id": "nominal", "nome": "Nominal (com juros da dívida)", "valor_rs": nom, "pct_pib": None, "situacao": "deficit" if nom > 0 else "superavit"},
    ]
    if prim <= 0 < nom:
        leitura = "Sem contar os juros, arrecadou mais do que gastou; a conta de juros da dívida tornou o saldo final negativo."
    elif prim > 0:
        leitura = "Mesmo sem contar os juros, gastou mais do que arrecadou; os juros da dívida ampliam o buraco."
    else:
        leitura = "Arrecadou mais do que gastou, mesmo contando os juros da dívida."

    atual = ini = None
    for r in a6:
        if r.get("cod_conta") == "DividaConsolidada":
            col = normalizar(r.get("coluna", ""))
            if col.startswith(normalizar("Até o Bimestre")):
                atual = r.get("valor")
            elif col.startswith(normalizar("Em 31/12")):
                ini = r.get("valor")
    rcl = pessoal["rcl"] if pessoal and pessoal.get("rcl") else None
    itens_estoque = []
    var_pct = None
    if atual is not None:
        var_pct = round((float(atual) / float(ini) - 1) * 100, 1) if ini else None
        item = {"id": "divida", "nome": "Dívida consolidada", "valor_rs": float(atual), "variacao_pct": var_pct}
        if rcl:
            item.update({"pct": round(float(atual) / rcl * 100, 1), "rotulo_pct": "% da receita corrente líquida"})
        itens_estoque.append(item)
    tendencia = None if var_pct is None else ("alta" if var_pct > 0.5 else "queda" if var_pct < -0.5 else "estavel")

    op = deficits["operacional"]
    receita = deficits["nominal"]["receitas_totais"]
    juros = op.get("juros_nominais")
    itens_custo = []
    if juros is not None:
        itens_custo.append({"id": "juros", "nome": "Juros e encargos da dívida (no exercício)", "valor_rs": juros,
                            "pct": round(juros / receita * 100, 1) if receita else None, "rotulo_pct": "% da receita arrecadada"})
    if op.get("juros_reais") is not None:
        itens_custo.append({"id": "juros_reais", "nome": "Juros reais (acima da inflação, aprox.)", "valor_rs": op["juros_reais"],
                            "nota": "Cálculo nosso: juros − IPCA acumulado × dívida no início do ano."})
    return {
        "universo": instituicao or "Ente consultado",
        "referencia": deficits.get("periodo"),
        "fluxo": {"periodo": deficits.get("periodo"), "itens": itens_fluxo, "leitura": leitura},
        "estoque": {"itens": itens_estoque, "tendencia": tendencia, "variacao_pct": var_pct,
                    "nota": "Estados e municípios não têm série do PIB aberta e atual; comparamos a dívida com a receita corrente líquida (limite da LRF)."},
        "custo": {"itens": itens_custo,
                  "leitura": "Quanto da receita vai para pagar a dívida; depende de juros e da inflação sobre o saldo devedor."},
        "capacidade": {"itens": [], "indisponivel": "A proporção da receita e da despesa em relação ao PIB só faz sentido para o país. "
                                                    "O PIB estadual e municipal do IBGE sai com cerca de dois anos de defasagem."},
        "fonte": {"nome": "SICONFI / Tesouro Nacional (RREO anexos 01 e 06, RGF anexo 01)", "url": "https://siconfi.tesouro.gov.br/"},
    }


def _situacao_fiscal(ente: int, esfera: str, a1: list[dict], a6: list[dict], deficits: dict, pessoal: dict | None, instituicao: str | None) -> dict | None:
    try:
        if esfera == "uniao":
            s = macro.situacao_fiscal_brasil()
            s["capacidade"] = _capacidade_uniao()
            return s
        return _situacao_subnacional(ente, a1, a6, deficits, pessoal, instituicao)
    except Exception as exc:  # noqa: BLE001 — o painel antigo continua útil sem esta seção
        log.warning("situação fiscal indisponível (%s): %s", ente, exc)
        return None


# --------------------------------------------------------------------------- #
# API pública do módulo
# --------------------------------------------------------------------------- #
def painel_economia(ente: int) -> dict | None:
    per = _periodo_mais_recente(ente)
    if not per:
        return None
    ano, bim = per
    a1 = _rreo(ente, ano, bim, "RREO-Anexo 01")
    try:
        a2 = _rreo(ente, ano, bim, "RREO-Anexo 02")
    except ErroFonte:
        a2 = []
    try:
        a3 = _rreo(ente, ano, bim, "RREO-Anexo 03")
    except ErroFonte:
        a3 = []
    try:
        a6 = _rreo(ente, ano, bim, "RREO-Anexo 06")
    except ErroFonte:
        a6 = []
    try:
        pessoal = _alerta_lrf(_pessoal(ente, ano))
    except ErroFonte:
        pessoal = None

    despesas = _despesas(a1, a2)
    funcoes = {f["nome"]: f for f in despesas["funcoes"]}
    esfera = _esfera(ente)
    deficits = _deficits(a1, a6, ano, bim)
    instituicao = next((r.get("instituicao") for r in a1 if r.get("instituicao")), None)
    return {
        "ente": ente,
        "situacao_fiscal": _situacao_fiscal(ente, esfera, a1, a6, deficits, pessoal, instituicao),
        "esfera": esfera,
        "instituicao": next((r.get("instituicao") for r in a1 if r.get("instituicao")), None),
        "populacao": next((r.get("populacao") for r in a1 if r.get("populacao")), None),
        "periodo": {"ano": ano, "bimestre": bim, "rotulo": f"{bim}º bimestre de {ano}"},
        "nivel1": {
            "balanco": _balanco(a1),
            "lrf_pessoal": pessoal,
            "saude": funcoes.get("Saúde"),
            "educacao": funcoes.get("Educação"),
        },
        "deficits": deficits,
        "nivel2": {"receitas": _receitas(a1, a3, esfera), "despesas": despesas},
        "nivel3": {"execucao": _execucao(a1)},
        "fonte": "SICONFI / Tesouro Nacional (RREO e RGF declarados pelos próprios entes)",
    }
