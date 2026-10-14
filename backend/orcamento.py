"""Ciclo orçamentário (PPA, LDO, LOA) e calculadora "para onde vai o meu imposto".

Fontes verificadas em out/2026 (nada presumido):

* União — Câmara dos Deputados, Dados Abertos (proposições do tipo PLN, "Projeto de Lei do Congresso
  Nacional"): o PPA, a LDO e a LOA entram no Congresso como PLN, com ementa, situação, data e link do
  texto. Não traz os valores da LOA; para isso há o SIOP e o Portal da Transparência (links).
* Estados e municípios — o SICONFI (extrato de entregas) só recebe DCA, MSC, RREO e RGF; PPA, LDO e
  LOA NÃO são entregáveis dele. Não existe base nacional estruturada desses documentos: a lacuna é
  declarada, e o que a LOA fixou aparece como "dotação inicial" do RREO (já em economia.py).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

import economia
from fontes import ClienteHTTP, ErroFonte

log = logging.getLogger("impacto.orcamento")

CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
_http = ClienteHTTP(pausa=0.05, timeout=60.0)

TTL_S = 6 * 3600
_cache: dict[tuple, tuple[float, Any]] = {}
_travas: dict[tuple, threading.Lock] = defaultdict(threading.Lock)


def _memo(chave: tuple, produzir):  # noqa: ANN001
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


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# União: PPA, LDO e LOA como proposições do Congresso Nacional
# --------------------------------------------------------------------------- #
_LOA = re.compile(r"estima a receita e fixa a despesa da uni[ãa]o para o exerc[íi]cio (?:financeiro )?de (\d{4})", re.I)
_LDO = re.compile(r"diretrizes para a elabora[çc][ãa]o e a execu[çc][ãa]o da lei or[çc]ament[áa]ria de (\d{4})", re.I)
_PPA = re.compile(r"plano plurianual da uni[ãa]o para o per[íi]odo de (\d{4}) a (\d{4})", re.I)
_ALTERA = re.compile(r"\baltera\b", re.I)
_LEI = re.compile(r"Lei\s*(?:n[ºo°.]?\s*)?(\d{1,2}\.?\d{3})\s*/\s*(\d{4})", re.I)

TIPOS = {"LOA": "Lei Orçamentária Anual (LOA)", "LDO": "Lei de Diretrizes Orçamentárias (LDO)",
         "PPA": "Plano Plurianual (PPA)"}
SENTIDO = {
    "PPA": "Planeja, por 4 anos, programas e metas; começa no 2º ano de um mandato e vai até o 1º do seguinte.",
    "LDO": "Define as regras e as metas fiscais do ano seguinte; orienta a LOA.",
    "LOA": "Estima a receita e autoriza (fixa) a despesa do ano; é o orçamento propriamente dito.",
}


def _classificar(ementa: str) -> tuple[str, str] | None:
    """(tipo, referência) de um PLN que institui PPA, LDO ou LOA; None para alterações e outros PLN."""
    if _ALTERA.search(ementa):
        return None
    for tipo, rx in (("LOA", _LOA), ("LDO", _LDO), ("PPA", _PPA)):
        m = rx.search(ementa)
        if m:
            return tipo, "-".join(m.groups()) if tipo == "PPA" else m.group(1)
    return None


def _plns(ano: int) -> list[dict]:
    saida: list[dict] = []
    for pagina in range(1, 6):
        d = _http.json(f"{CAMARA}/proposicoes", {"siglaTipo": "PLN", "ano": ano, "itens": 100,
                                                  "pagina": pagina, "ordem": "ASC", "ordenarPor": "id"})
        dados = d.get("dados", [])
        saida += dados
        if len(dados) < 100:
            break
    return saida


def _fmt_lei(m: re.Match) -> str:
    n = m.group(1).replace(".", "")
    return f"Lei {int(n):,}".replace(",", ".") + f"/{m.group(2)}"


def _documento(p: dict, tipo: str, ref: str, n_alteracoes: int) -> dict:
    det = _http.json(f"{CAMARA}/proposicoes/{p['id']}").get("dados", {})
    st = det.get("statusProposicao") or {}
    despacho = st.get("despacho") or ""
    lei = _LEI.search(despacho)
    situacao = st.get("descricaoSituacao") or "Situação não informada"
    return {
        "tipo": tipo, "tipo_nome": TIPOS[tipo], "referencia": ref,
        "proposicao": f"PLN {det.get('numero', p.get('numero'))}/{det.get('ano', p.get('ano'))}",
        "situacao": situacao,
        "virou_lei": situacao.lower().startswith("transformado em norma"),
        "lei": _fmt_lei(lei) if lei and "transformado" in despacho.lower() else None,
        "apresentada_em": (det.get("dataApresentacao") or "")[:10] or None,
        "ultima_movimentacao": (st.get("dataHora") or "")[:10] or None,
        "despacho": despacho[:300] or None,
        "url_texto": det.get("urlInteiroTeor") or st.get("url"),
        "url_tramitacao": f"https://www.camara.leg.br/propostas-legislativas/{p['id']}",
        "alteracoes_no_congresso": n_alteracoes,
    }


def _ciclo_uniao() -> dict:
    hoje = date.today()
    docs: dict[tuple[str, str], dict] = {}
    alteracoes: dict[tuple[str, str], int] = defaultdict(int)
    erros = 0
    for ano in range(hoje.year - 3, hoje.year + 1):
        try:
            plns = _plns(ano)
        except ErroFonte as exc:
            log.warning("Câmara indisponível para PLN %s: %s", ano, exc)
            erros += 1
            continue
        for p in plns:
            ementa = p.get("ementa") or ""
            achado = _classificar(ementa)
            if achado:
                docs.setdefault(achado, p)
                continue
            # contagem de alterações da lei do exercício (ex.: "Altera a Lei ..., que estima a receita ... 2025")
            for tipo, rx in (("LOA", _LOA), ("LDO", _LDO), ("PPA", _PPA)):
                m = rx.search(ementa)
                if m and _ALTERA.search(ementa):
                    alteracoes[(tipo, "-".join(m.groups()) if tipo == "PPA" else m.group(1))] += 1
                    break
    if erros == 4:
        raise ErroFonte("Câmara dos Deputados indisponível")
    itens = [_documento(p, t, r, alteracoes.get((t, r), 0)) for (t, r), p in docs.items()]
    ordem = {"PPA": 0, "LDO": 1, "LOA": 2}
    itens.sort(key=lambda x: (x["referencia"], ordem[x["tipo"]]), reverse=True)
    return {"documentos": itens, "janela": f"PLN apresentados de {hoje.year - 3} a {hoje.year}"}


LINKS_UNIAO = [
    {"nome": "SIOP — Sistema Integrado de Planejamento e Orçamento (valores da LOA por função, órgão e ação)",
     "url": "https://www1.siop.planejamento.gov.br/"},
    {"nome": "Orçamentos anuais — Ministério do Planejamento e Orçamento (LOA, quadros e anexos)",
     "url": "https://www.gov.br/planejamento/pt-br/assuntos/orcamento/orcamentos-anuais"},
    {"nome": "Plano Plurianual — Ministério do Planejamento e Orçamento",
     "url": "https://www.gov.br/planejamento/pt-br/assuntos/plano-plurianual"},
    {"nome": "Portal da Transparência — Orçamento federal e despesas",
     "url": "https://portaldatransparencia.gov.br/despesas"},
    {"nome": "Congresso Nacional — LDO 2026 (tramitação, relatórios e anexos)",
     "url": "https://www.congressonacional.leg.br/materias/materias-bicamerais/-/ver/ldo-2026"},
]

LACUNA_SUBNACIONAL = (
    "Não há fonte aberta e estruturada, de abrangência nacional, com o PPA, a LDO e a LOA de estados e "
    "municípios. O SICONFI (Tesouro Nacional) recebe apenas DCA, MSC, RREO e RGF, e verificamos que esses "
    "documentos não constam de suas entregas. Cada ente publica o seu em formatos próprios (portal de "
    "transparência, site da Assembleia/Câmara ou Diário Oficial). Por isso não listamos os documentos "
    "daqui: o que a LOA fixou para o ano aparece abaixo como “dotação inicial” do RREO, que é declarada "
    "pelo próprio ente ao Tesouro."
)


def ciclo(ente: int) -> dict:
    """Documentos do ciclo orçamentário da esfera do `ente` (1 = União; <100 = UF; 7 dígitos = município)."""
    esfera = economia._esfera(ente)
    base = {"ente": ente, "esfera": esfera, "consultado_em": _agora(), "tipos": [
        {"tipo": t, "nome": TIPOS[t], "sentido": SENTIDO[t]} for t in ("PPA", "LDO", "LOA")]}
    if esfera == "uniao":
        base.update({
            "fonte": {"nome": "Câmara dos Deputados — Dados Abertos (proposições PLN)",
                      "url": "https://dadosabertos.camara.leg.br/api/v2/proposicoes?siglaTipo=PLN"},
            **(lambda c: {"documentos": c["documentos"], "janela": c["janela"]})(_memo(("uniao",), _ciclo_uniao)),
            "links": LINKS_UNIAO, "lacuna": None,
            "observacao": "A LOA é a lei do orçamento; o PLN é o projeto enviado pelo Executivo ao Congresso. "
                          "Os valores da LOA não vêm desta fonte: consulte o SIOP ou o Portal da Transparência.",
        })
        return base
    base.update({
        "fonte": {"nome": "SICONFI — extrato de entregas (apenas DCA, MSC, RREO e RGF)",
                  "url": "https://siconfi.tesouro.gov.br/"},
        "documentos": [], "links": [
            {"nome": "SICONFI — consulta aos relatórios que o ente declarou (RREO, RGF)", "url": "https://siconfi.tesouro.gov.br/"},
        ],
        "lacuna": LACUNA_SUBNACIONAL,
        "observacao": None,
    })
    return base


# --------------------------------------------------------------------------- #
# Calculadora: para onde vai o meu imposto (ilustração proporcional)
# --------------------------------------------------------------------------- #
AVISO_ILUSTRACAO = (
    "Ilustração proporcional, não um extrato. Imposto não é carimbado por área: tudo entra num caixa comum "
    "e o orçamento decide como gastar. Aqui o valor informado é repartido na mesma proporção da despesa "
    "liquidada por função da esfera escolhida (RREO Anexo 02, SICONFI), só para dar ordem de grandeza."
)
AVISO_TRIBUTO = (
    "Cada tributo abastece uma esfera: Imposto de Renda e contribuições são da União (estados e municípios "
    "recebem parte por repasses, como FPE e FPM); ICMS e IPVA são dos estados; IPTU e ISS são dos municípios. "
    "Escolha a esfera que corresponde ao tributo que você paga."
)


def _repartir(total: float, partes: list[tuple[str, float]]) -> list[float]:
    """Divide `total` em centavos, proporcional aos pesos, somando exatamente `total` (maior resto)."""
    centavos = round(total * 100)
    soma = sum(p for _, p in partes)
    brutos = [centavos * p / soma for _, p in partes]
    base = [int(b) for b in brutos]
    falta = centavos - sum(base)
    for i in sorted(range(len(partes)), key=lambda i: brutos[i] - base[i], reverse=True)[:falta]:
        base[i] += 1
    return [b / 100 for b in base]


def distribuir_imposto(ente: int, valor_anual: float) -> dict | None:
    painel = economia.painel_economia(ente)
    if painel is None:
        return None
    funcoes = painel["nivel2"]["despesas"]["funcoes"]
    if not funcoes or valor_anual <= 0:
        return {"ente": ente, "esfera": painel["esfera"], "instituicao": painel.get("instituicao"),
                "periodo": painel["periodo"], "valor_anual": round(valor_anual, 2), "areas": [],
                "lacuna": "O ente não declarou despesa por função neste período; não é possível distribuir."
                if not funcoes else None,
                "aviso": AVISO_ILUSTRACAO, "aviso_tributo": AVISO_TRIBUTO, "fonte": painel["fonte"],
                "consultado_em": _agora()}
    valores = _repartir(valor_anual, [(f["nome"], f["valor"]) for f in funcoes])
    soma = sum(f["valor"] for f in funcoes)
    areas = [{"nome": f["nome"], "icone": f["icone"], "pct": round(100 * f["valor"] / soma, 2),
              "valor": v, "despesa_liquidada": f["valor"]} for f, v in zip(funcoes, valores)]
    return {
        "ente": ente, "esfera": painel["esfera"], "instituicao": painel.get("instituicao"),
        "periodo": painel["periodo"], "valor_anual": round(valor_anual, 2), "areas": areas, "lacuna": None,
        "base_despesa": round(soma, 2),
        "nota_funcoes": "“Encargos especiais” reúne, em geral, serviço da dívida e transferências a outros "
                        "entes; “Previdência” pode incluir o regime próprio dos servidores do ente.",
        "aviso": AVISO_ILUSTRACAO, "aviso_tributo": AVISO_TRIBUTO, "fonte": painel["fonte"],
        "consultado_em": _agora(),
    }
