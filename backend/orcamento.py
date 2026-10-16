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

import json
import logging
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

import economia
from config import RAIZ
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
        return painel_ciclo(ente, base)
    base.update({
        "fonte": {"nome": "SICONFI — extrato de entregas (apenas DCA, MSC, RREO e RGF)",
                  "url": "https://siconfi.tesouro.gov.br/"},
        "documentos": [], "links": [
            {"nome": "SICONFI — consulta aos relatórios que o ente declarou (RREO, RGF)", "url": "https://siconfi.tesouro.gov.br/"},
        ],
        "lacuna": LACUNA_SUBNACIONAL,
        "observacao": None,
    })
    return painel_ciclo(ente, base)


# --------------------------------------------------------------------------- #
# Linha do tempo viva, proposta da LOA (lida do texto oficial) e orçamento local
# --------------------------------------------------------------------------- #
PASTA_CACHE = RAIZ / "dados" / "orcamento"
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

PRAZOS = [
    {"tipo": "PPA", "texto": "Enviado ao Congresso até 31 de agosto do 1º ano do mandato e devolvido para sanção até o fim da sessão legislativa (22 de dezembro)."},
    {"tipo": "LDO", "texto": "Enviada até 15 de abril e devolvida para sanção até o fim do 1º período da sessão legislativa (17 de julho)."},
    {"tipo": "LOA", "texto": "Enviada até 31 de agosto e devolvida para sanção até o fim da sessão legislativa (22 de dezembro)."},
]
PRAZOS_FONTE = "Constituição Federal, art. 57, e Ato das Disposições Constitucionais Transitórias, art. 35, § 2º."

ESTADOS = {
    "em_execucao": "Em execução", "aprovada": "Aprovada (virou lei)", "em_tramitacao": "Em tramitação no Congresso",
    "nao_enviada": "Ainda não enviada ao Congresso", "sem_fonte": "Sem fonte aberta",
}


def _num_br(txt: str) -> float:
    return float(txt.replace(".", "").replace(",", "."))


def _paginas_pdf(url: str, limite: int = 200) -> list[str]:
    """Texto (espaços normalizados) das primeiras páginas de um PDF."""
    import io
    import logging as _lg

    from pypdf import PdfReader

    _lg.getLogger("pypdf").setLevel(_lg.ERROR)
    bruto = ClienteHTTP(pausa=0.05, timeout=300.0).get(url, accept="*/*").content
    leitor = PdfReader(io.BytesIO(bruto))
    return [re.sub(r"\s+", " ", (leitor.pages[i].extract_text() or "")) for i in range(min(limite, len(leitor.pages)))]


def _extrair(rx: str, texto: str) -> re.Match | None:
    return re.search(rx, texto, re.I)


def _resumo_ploa(paginas: list[str]) -> dict:
    """Totais do projeto de lei e destaques da Mensagem Presidencial. Cada valor só entra se for encontrado."""
    inicio = " ".join(paginas[:6])
    def valor(rx: str) -> float | None:
        m = _extrair(rx, inicio)
        return _num_br(m.group(m.lastindex)) if m else None
    totais = {
        "receita_total": valor(r"estima a receita da Uni[ãa]o para o exerc[íi]cio financeiro de \d{4} no montante de R\$ ?([\d.]+,\d{2})"),
        "fiscal_e_seguridade": valor(r"receita total estimada nos Or[çc]amentos Fiscal e da Seguridade Social [ée] de R\$ ?([\d.]+,\d{2})"),
        "orcamento_fiscal": valor(r"I - Or[çc]amento Fiscal - R\$ ?([\d.]+,\d{2})"),
        "seguridade": valor(r"II - Or[çc]amento da Seguridade Social - R\$ ?([\d.]+,\d{2})"),
        "refinanciamento_divida": valor(r"III - Refinanciamento da D[ií]vida P[úu]blica Federal - R\$ ?([\d.]+,\d{2})"),
        "credito_condicionado": valor(r"inclui, com fundamento.{0,200}?R\$ ?([\d.]+,\d{2}).{0,160}?opera[çc][õo]es de cr[ée]dito"),
    }
    # destaques da Mensagem Presidencial (texto livre do Executivo): rótulo, expressão, unidade
    candidatos = [
        ("Salário mínimo previsto para 2027", r"sal[áa]rio m[íi]nimo deve atingir o patamar de R\$ ?([\d.]+,\d{2})", "reais"),
        ("Saúde — Atenção Primária (custeio)", r"Programa Aten[çc][ãa]o Prim[áa]ria [àa] Sa[úu]de, o Governo Federal alocar[áa] cerca de R\$ ?([\d.,]+) bilh", "bilhões"),
        ("Saúde — média e alta complexidade (custeio)", r"Para 20\d{2}, est[ãa]o previstos R\$ ?([\d.,]+) bilh[õo]es em despesas de custeio desses servi[çc]os", "bilhões"),
        ("Saúde — investimentos do Novo PAC", r"Minist[ée]rio da Sa[úu]de contar[áa] com R\$ ?([\d.,]+) bilh[õo]es em investimentos", "bilhões"),
        ("Educação básica", r"programa Educa[çc][ãa]o B[áa]sica Democr[áa]tica, com Qualidade e Equidade ter[áa] R\$ ?([\d.,]+) bilh", "bilhões"),
        ("Alimentação escolar (PNAE)", r"PNAE contar[áa] com aproximadamente R\$ ?([\d.,]+) bilh", "bilhões"),
    ]
    destaques = []
    for rotulo, rx, unidade in candidatos:
        for i, t in enumerate(paginas[40:170], start=41):
            m = _extrair(rx, t)
            if m:
                try:
                    v = _num_br(m.group(1)) if unidade == "reais" else float(m.group(1).replace(".", "").replace(",", "."))
                except ValueError:
                    break
                destaques.append({"rotulo": rotulo, "valor": v, "unidade": unidade, "pagina_pdf": i})
                break
    salario_2026 = None
    for t in paginas[140:175]:
        m = re.search(r"Evolu[çc][ãa]o do Sal[áa]rio M[íi]nimo ([\d. ]+?) 0 200", t)
        if m:
            nums = [int(x.replace(".", "")) for x in re.findall(r"\d{1,2}\.\d{3}|\d{3}", m.group(1))]
            if len(nums) >= 2:
                salario_2026 = float(nums[-2])   # penúltimo ponto do gráfico = 2026; o último é o PLOA 2027
            break
    return {"totais": totais, "destaques": destaques, "salario_minimo_ano_anterior": salario_2026}


def _ploa(proposicao: str, url: str) -> dict | None:
    """Resumo do projeto da LOA, lido do PDF oficial na primeira vez e guardado em disco (o PDF tem ~27 MB)."""
    chave = re.sub(r"\W+", "_", proposicao)
    arq = PASTA_CACHE / f"ploa_{chave}.json"
    try:
        if arq.exists():
            return json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    def produzir() -> dict | None:
        try:
            paginas = _paginas_pdf(url)
        except Exception as exc:  # noqa: BLE001 — sem o PDF, a tela mostra só os links
            log.warning("PDF do projeto da LOA indisponível: %s", exc)
            return None
        r = _resumo_ploa(paginas)
        if not r["totais"]["receita_total"]:
            return None
        r.update({"proposicao": proposicao, "url": url, "extraido_em": _agora()})
        try:
            PASTA_CACHE.mkdir(parents=True, exist_ok=True)
            arq.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return r
    return _memo(("ploa", proposicao), produzir)


def _estado_doc(doc: dict | None) -> str:
    if doc is None:
        return "nao_enviada"
    return "aprovada" if doc["virou_lei"] else "em_tramitacao"


def _no(tipo: str, nome: str, periodo: str, estado: str, detalhe: str | None, doc: dict | None = None) -> dict:
    return {"tipo": tipo, "nome": nome, "periodo": periodo, "estado": estado, "estado_rotulo": ESTADOS[estado], "detalhe": detalhe,
            "proposicao": doc["proposicao"] if doc else None, "url_tramitacao": doc["url_tramitacao"] if doc else None}


def _mov(doc: dict) -> str:
    if not doc.get("ultima_movimentacao"):
        return doc["situacao"]
    d = doc["ultima_movimentacao"]
    return f"{doc['situacao']} (última movimentação em {d[8:10]}/{d[5:7]}/{d[:4]})"


def _dotacao_inicial(ente: int) -> dict | None:
    """Despesa autorizada na LOA do exercício (dotação inicial do RREO) e, se houver, por função."""
    try:
        per = economia._periodo_mais_recente(ente)
        if not per:
            return None
        ano, bim = per
        a1 = economia._rreo(ente, ano, bim, "RREO-Anexo 01")
        a2 = economia._rreo(ente, ano, bim, "RREO-Anexo 02")
    except Exception as exc:  # noqa: BLE001
        log.warning("dotação inicial indisponível (%s): %s", ente, exc)
        return None
    despesa = economia._valor(a1, "TotalDespesas", "DOTAÇÃO INICIAL")
    receita = economia._valor(a1, "TotalReceitas", "PREVISÃO INICIAL")
    funcoes: dict[str, float] = {}
    subfuncoes: dict[str, float] = {}
    for r in a2:
        if r.get("cod_conta") != "RREO2TotalDespesas" or not economia.normalizar(r.get("coluna", "")).startswith(economia.normalizar("DOTAÇÃO INICIAL")):
            continue
        nome = economia.FUNCOES.get(economia.normalizar(r.get("conta", "")))
        if nome and nome not in funcoes and r.get("valor"):
            funcoes[nome] = float(r["valor"])
        elif not nome and r.get("valor") and not r.get("conta", "").startswith(("FU", "DESPESAS")):
            subfuncoes.setdefault(economia.normalizar(r["conta"]), float(r["valor"]))
    soma = sum(funcoes.values())
    instituicao = next((r.get("instituicao") for r in a1 if r.get("instituicao")), None)
    return {
        "ano": ano, "instituicao": instituicao, "periodo": f"{bim}º bimestre de {ano}",
        "subfuncoes": subfuncoes,
        "receita_prevista": float(receita) if receita is not None else None,
        "despesa_autorizada": float(despesa) if despesa is not None else None,
        "funcoes": sorted(({"nome": n, "valor": v, "pct": round(100 * v / soma, 1) if soma else None,
                            "icone": economia.ICONES.get(n, "fa-circle")} for n, v in funcoes.items()), key=lambda x: -x["valor"]),
    }


def painel_ciclo(ente: int, base: dict) -> dict:
    """Acrescenta a `base` (resposta de ciclo()) a linha do tempo viva e a peça central da esfera."""
    hoje = date.today()
    ano = hoje.year
    esfera = base["esfera"]
    base["agora"] = {"data": hoje.isoformat(), "rotulo": f"{MESES[hoje.month - 1].capitalize()}/{ano}", "ano": ano}
    base["prazos"] = {"itens": PRAZOS, "fonte": PRAZOS_FONTE}
    docs = {(d["tipo"], d["referencia"]): d for d in base.get("documentos", [])}

    if esfera == "uniao":
        ppa = next((d for d in base["documentos"] if d["tipo"] == "PPA"
                    and "-" in d["referencia"] and int(d["referencia"][:4]) <= ano <= int(d["referencia"][5:])), None)
        ldo, loa = docs.get(("LDO", str(ano + 1))), docs.get(("LOA", str(ano + 1)))
        loa_atual = docs.get(("LOA", str(ano)))
        ppa_estado = "em_execucao" if ppa and ppa["virou_lei"] else _estado_doc(ppa)
        base["linha_do_tempo"] = [
            _no("PPA", "Plano Plurianual", ppa["referencia"].replace("-", "–") if ppa else "—", ppa_estado,
                "Metas de 4 anos; vigora até o fim do 1º ano do mandato seguinte." if ppa else None, ppa),
            _no("LDO", f"LDO {ano + 1}", str(ano + 1), _estado_doc(ldo), _mov(ldo) if ldo else "Prevista para ser enviada até 15 de abril.", ldo),
            _no("LOA", f"LOA {ano + 1}", str(ano + 1), _estado_doc(loa), _mov(loa) if loa else "Prevista para ser enviada até 31 de agosto.", loa),
        ]
        partes = []
        if loa and not loa["virou_lei"]:
            partes.append(f"O Congresso Nacional analisa o projeto do orçamento de {ano + 1} ({loa['proposicao']}).")
        elif loa:
            partes.append(f"O orçamento de {ano + 1} já virou lei.")
        else:
            partes.append(f"O projeto do orçamento de {ano + 1} ainda não chegou ao Congresso.")
        if loa_atual and loa_atual["virou_lei"]:
            lei_txt = f" ({loa_atual['lei']})" if loa_atual.get("lei") else ""
            partes.append(f"O orçamento de {ano} está em execução{lei_txt}.")
        base["momento"] = " ".join(partes)

        proposta = None
        if loa and not loa["virou_lei"] and loa.get("url_texto"):
            r = _ploa(loa["proposicao"], loa["url_texto"])
            comparacao = None
            din = _dotacao_inicial(1)
            if r and din and din["despesa_autorizada"] and r["totais"].get("fiscal_e_seguridade"):
                comparacao = {"ano_base": din["ano"], "valor_base": din["despesa_autorizada"],
                              "variacao_pct": round((r["totais"]["fiscal_e_seguridade"] / din["despesa_autorizada"] - 1) * 100, 1),
                              "nota": f"Orçamentos Fiscal e da Seguridade de {ano + 1} (proposta) contra a dotação inicial de {din['ano']} declarada no RREO."}
            proposta = {
                "proposicao": loa["proposicao"], "ementa_ano": ano + 1, "situacao": _mov(loa), "url_texto": loa["url_texto"],
                "url_tramitacao": loa["url_tramitacao"], "apresentada_em": loa["apresentada_em"],
                "resumo": r, "comparacao": comparacao,
                "leitura_pdf": "extraida" if r else "indisponivel",
                "aviso": "Números lidos do texto do projeto de lei e da Mensagem Presidencial (documento do Executivo). "
                         "São a proposta: o Congresso pode alterá-la.",
            }
        base["proposta"] = proposta
    else:
        din = _dotacao_inicial(ente)
        ref = din["ano"] if din else ano
        base["linha_do_tempo"] = [
            _no("PPA", "Plano Plurianual", "—", "sem_fonte", "Consulte o portal de transparência do ente."),
            _no("LDO", f"LDO {ref + 1}", str(ref + 1), "sem_fonte", "Consulte o portal de transparência do ente."),
            _no("LOA", f"LOA {ref}", str(ref), "em_execucao" if din else "sem_fonte",
                f"A LOA fixou {economia_brl(din['despesa_autorizada'])} de despesa (dotação inicial no RREO)." if din and din["despesa_autorizada"] else None),
        ]
        base["momento"] = (f"O orçamento de {ref} está em execução. O projeto de {ref + 1} é votado pela "
                           f"{'Assembleia Legislativa' if esfera == 'estado' else 'Câmara Municipal'}; não há fonte aberta nacional para acompanhá-lo.")
        base["orcamento_local"] = din
    return base


def economia_brl(v: float) -> str:
    a = abs(v)
    if a >= 1e12:
        return f"R$ {v / 1e12:,.2f} tri".replace(",", "X").replace(".", ",").replace("X", ".")
    if a >= 1e9:
        return f"R$ {v / 1e9:,.1f} bi".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {v / 1e6:,.1f} mi".replace(",", "X").replace(".", ",").replace("X", ".")


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
