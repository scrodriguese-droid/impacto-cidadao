"""De olho no governo: pauta do Congresso e como cada deputado e partido votou em relação ao Governo.

Duas fontes, ambas abertas e oficiais:
* Votações da Câmara (arquivos de dados abertos): processadas por backend/coletor_governismo.py, que grava
  dados/votacoes/governismo_camara.json; aqui só se lê e se soma por período. Regras de cálculo no cabeçalho do coletor.
* Agenda do Plenário (Câmara: /orgaos/180/eventos e /eventos/{id}/pauta; Senado: /plenario/agenda/dia e /votacao),
  consultada ao vivo e guardada por 1 hora.

O Senado não publica a orientação do Governo nos dados abertos de votação, então o governismo é calculado só para a Câmara.
Nada aqui classifica partido ou parlamentar como bom ou ruim: mostramos comportamento em votações, com as regras à vista.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from config import RAIZ
from fontes import ClienteHTTP, ErroFonte

log = logging.getLogger("impacto.governismo")

ARQUIVO = RAIZ / "dados" / "votacoes" / "governismo_camara.json"
LIMIAR_ALTO = 70            # % de votos iguais à orientação do Governo: "apoia com frequência"
LIMIAR_BAIXO = 30           # abaixo disso: "raramente apoia"
MIN_DEPUTADOS_PARTIDO = 3   # partidos menores têm poucos votos para um percentual confiável
DIAS_ATIVO = 120            # deputado sem voto há mais que isso (e sem voto nos últimos dias do banco) = fora do exercício
TTL_AGENDA_S = 3600
TIPOS_RELEVANTES = {"PEC", "PLP", "PL", "PLV", "MPV", "PDL"}
ROTULO_VOTO = {"S": "Sim", "N": "Não", "A": "Abstenção", "O": "Obstrução", "P": "Presidiu a sessão", "-": "Não votou"}

_http = ClienteHTTP(pausa=0.05, timeout=30.0, tentativas=3)
_trava = threading.RLock()
_trava_agenda: dict[str, threading.Lock] = {}
_cache: dict[str, Any] = {"mtime": None, "dados": None}
_cache_agenda: dict[str, tuple[float, Any]] = {}

CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
SENADO = "https://legis.senado.leg.br/dadosabertos"


def _dados() -> dict:
    try:
        mt = ARQUIVO.stat().st_mtime
    except OSError as exc:
        raise ErroFonte("Dados de governismo ainda não coletados (rode backend/coletor_governismo.py).") from exc
    with _trava:
        if _cache["mtime"] != mt:
            _cache.update(mtime=mt, dados=json.loads(ARQUIVO.read_text(encoding="utf-8")))
        return _cache["dados"]


def _pct(parte: float, total: float) -> float | None:
    return round(100 * parte / total, 1) if total else None


def faixa(taxa: float | None) -> str | None:
    if taxa is None:
        return None
    return "alto" if taxa >= LIMIAR_ALTO else "baixo" if taxa < LIMIAR_BAIXO else "medio"


def _periodo(d: dict, desde: int | None, ate: int | None) -> tuple[int, int, list[str]]:
    anos = sorted(int(a) for a in d["anos"])
    desde, ate = max(anos[0], desde or anos[0]), min(anos[-1], ate or anos[-1])
    if desde > ate:
        raise ValueError("O ano inicial não pode ser posterior ao final.")
    return desde, ate, [str(a) for a in range(desde, ate + 1) if str(a) in d["anos"]]


def _soma(dep: dict, anos: list[str]) -> list[int]:
    """[alinhado, contra, abstenção/obstrução, ausente, fiel, votos p/ fidelidade, presente, nominais]"""
    t = [0] * 8
    for a in anos:
        for i, v in enumerate(dep["anos"].get(a, [0] * 8)):
            t[i] += v
    return t


ARQ_APELIDOS = RAIZ / "dados" / "votacoes" / "apelidos.json"


def _apelidos() -> dict:
    """Apelidos editoriais das proposições ("PEC 45/2019" → "Reforma Tributária"); lidos a cada uso para valer sem recoletar."""
    try:
        return json.loads(ARQ_APELIDOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ativo(dep: dict, ate_data: str) -> bool:
    limite = (date.fromisoformat(ate_data) - timedelta(days=DIAS_ATIVO)).isoformat()
    return dep["ultimo_voto"] >= limite


def _linha(dep: dict, t: list[int], ate_data: str) -> dict:
    den = sum(t[:4])
    taxa = _pct(t[0], den)
    return {"id": dep["id"], "nome": dep["nome"], "uf": dep["uf"], "partido": dep["partido"],
            "ativo": _ativo(dep, ate_data), "votacoes": den, "governismo": taxa, "faixa": faixa(taxa),
            "fidelidade": _pct(t[4], t[5]), "presenca": _pct(t[6], t[7])}


def resumo(desde: int | None = None, ate: int | None = None) -> dict:
    """Termômetro: taxa global e por partido, mais a lista de deputados (para a busca)."""
    d = _dados()
    desde, ate, anos = _periodo(d, desde, ate)
    ate_data = d["ate_data"]
    linhas, por_partido = [], {}
    glob = [0] * 8
    for dep in d["deputados"]:
        t = _soma(dep, anos)
        if not sum(t[:4]):
            continue
        linha = _linha(dep, t, ate_data)
        linhas.append(linha)
        p = por_partido.setdefault(dep["partido"], {"t": [0] * 8, "n": 0, "ativos": 0})
        p["n"] += 1
        p["ativos"] += linha["ativo"]
        for i in range(8):
            p["t"][i] += t[i]
            glob[i] += t[i]
    partidos = []
    for sigla, p in por_partido.items():
        if p["n"] < MIN_DEPUTADOS_PARTIDO or sigla.casefold() == "sem partido":
            continue
        soma_c = sum(d["coesao"].get(a, {}).get(sigla, [0, 0])[0] for a in anos)
        n_c = sum(d["coesao"].get(a, {}).get(sigla, [0, 0])[1] for a in anos)
        taxa = _pct(p["t"][0], sum(p["t"][:4]))
        partidos.append({"sigla": sigla, "deputados": p["n"], "ativos": p["ativos"], "governismo": taxa,
                         "faixa": faixa(taxa), "fidelidade": _pct(p["t"][4], p["t"][5]),
                         "presenca": _pct(p["t"][6], p["t"][7]), "coesao": _pct(soma_c, n_c)})
    partidos.sort(key=lambda x: (-(x["governismo"] or 0), x["sigla"].casefold()))
    den = sum(glob[:4])
    return {
        "periodo": {"desde": desde, "ate": ate, "anos_disponiveis": sorted(int(a) for a in d["anos"])},
        "ate_data": ate_data, "gerado_em": d["gerado_em"],
        "votacoes_nominais": sum(d["anos"][a]["nominais"] for a in anos),
        "votacoes_com_orientacao": sum(d["anos"][a]["com_orientacao"] for a in anos),
        "limiares": {"alto": LIMIAR_ALTO, "baixo": LIMIAR_BAIXO},
        "global": {"governismo": _pct(glob[0], den), "faixa": faixa(_pct(glob[0], den)),
                   "contra": _pct(glob[1], den), "abstencao": _pct(glob[2], den), "ausente": _pct(glob[3], den),
                   "fidelidade": _pct(glob[4], glob[5]), "presenca": _pct(glob[6], glob[7])},
        "partidos": partidos,
        "deputados": sorted(linhas, key=lambda x: x["nome"].casefold()),
        "fonte": {"nome": "Câmara dos Deputados — votações nominais, orientações e votos (dados abertos)",
                  "url": "https://dadosabertos.camara.leg.br/swagger/api.html"},
        "notas": [
            "Taxa de governismo: votos iguais à orientação do Governo ÷ votações nominais do Plenário em que o Governo orientou “Sim” ou “Não” e o deputado estava no exercício. Votar o contrário, abster-se, obstruir ou faltar contam contra.",
            "“No exercício” vai do primeiro ao último voto do deputado na legislatura: suplentes não são cobrados pelo que veio antes de assumir. Licenças no meio do caminho não são separáveis nesses dados e entram como falta.",
            "Fidelidade ao partido: % dos votos “Sim” e “Não” do deputado iguais ao da maioria do próprio partido na mesma votação (partido com ao menos 3 votantes e sem empate). Usamos a maioria porque a Câmara publica a orientação por bloco ou federação, não por partido.",
            "Coesão do partido: em cada votação, |sim − não| ÷ (sim + não) entre os votos do partido; 100% = todos votaram igual. É a média das votações.",
            "Presença: votações nominais do Plenário em que o deputado registrou voto (qualquer um) ÷ votações nominais no período dele. Os arquivos não distinguem falta justificada de não justificada.",
            f"Faixas só descrevem o número: {LIMIAR_ALTO}% ou mais = apoia com frequência; de {LIMIAR_BAIXO}% a {LIMIAR_ALTO - 0.1:.1f}% = apoio parcial; abaixo de {LIMIAR_BAIXO}% = raramente apoia. Não dizem se votar com ou contra o Governo é bom ou ruim.",
            f"Partidos com menos de {MIN_DEPUTADOS_PARTIDO} deputados no período não aparecem, por falta de base para o percentual. Deputados que trocaram de partido contam no partido atual.",
            "Só votações nominais entram (as simbólicas não registram o voto de cada um) e o governismo é a medida de um recorte: mostra o passado, não prevê votos futuros.",
            "O Senado não publica a orientação do Governo nos dados abertos de votação, por isso o governismo é calculado apenas para a Câmara.",
        ],
    }


def deputado(dep_id: int, desde: int | None = None, ate: int | None = None) -> dict:
    d = _dados()
    desde, ate, anos = _periodo(d, desde, ate)
    dep = next((x for x in d["deputados"] if x["id"] == dep_id), None)
    if dep is None:
        raise KeyError(dep_id)
    t = _soma(dep, anos)
    den = sum(t[:4])
    colegas = [x for x in d["deputados"] if x["partido"] == dep["partido"]]
    pautas, ap = [], _apelidos()
    for p, c in zip(d["pautas"], dep.get("pautas", "")):
        orient = "S" if p["orientacao_governo"] == "Sim" else "N"
        pautas.append({"proposicao": p["proposicao"], "apelido": ap.get(p["proposicao"]) or p["apelido"], "data": p["data"], "ementa": p["ementa"],
                       "orientacao_governo": p["orientacao_governo"], "voto": ROTULO_VOTO[c],
                       "alinhado": None if c in ("-", "P") else c == orient})
    return {
        **_linha(dep, t, d["ate_data"]),
        "foto": dep["foto"], "primeiro_voto": dep["primeiro_voto"], "ultimo_voto": dep["ultimo_voto"],
        "url": f"https://www.camara.leg.br/deputados/{dep['id']}",
        "periodo": {"desde": desde, "ate": ate},
        "detalhe": {"alinhado": t[0], "contra": t[1], "abstencao": t[2], "ausente": t[3]},
        "colegas_de_partido": len(colegas),
        "por_ano": [{"ano": int(a), "governismo": _pct(v[0], sum(v[:4])), "votacoes": sum(v[:4])}
                    for a, v in sorted(dep["anos"].items()) if sum(v[:4])],
        "pautas": pautas,
    }


def pautas() -> dict:
    """As votações recentes de projetos relevantes (com apelido quando há), e como cada partido votou."""
    d = _dados()
    itens, ap = [], _apelidos()
    for p in d["pautas"]:
        partidos = [{"sigla": s, "sim": v[0], "nao": v[1], "pct_sim": _pct(v[0], v[0] + v[1])}
                    for s, v in p["partidos"].items() if v[0] + v[1] >= 3]
        partidos.sort(key=lambda x: (-(x["pct_sim"] or 0), x["sigla"]))
        itens.append({**{k: p[k] for k in ("id", "data", "proposicao", "ementa", "descricao",
                                          "orientacao_governo", "aprovada", "sim", "nao")}, "apelido": ap.get(p["proposicao"]) or p["apelido"], "partidos": partidos})
    return {"ate_data": d["ate_data"], "pautas": itens,
            "nota": "Votações nominais do Plenário em que o Governo orientou “Sim” ou “Não”, de PEC, PLP, PL, MPV e PDL; "
                    "requerimentos ficam de fora. Para cada projeto aparece a votação mais recente. Apelidos vêm de dados/votacoes/apelidos.json "
                    "(o nome oficial é sempre o da proposição)."}


# --------------------------------------------------------------------------- #
# Agenda ao vivo (Câmara e Senado)
# --------------------------------------------------------------------------- #
def _memo_agenda(chave: str, produzir):  # noqa: ANN001
    agora = time.time()
    if chave in _cache_agenda and agora - _cache_agenda[chave][0] < TTL_AGENDA_S:
        return _cache_agenda[chave][1]
    with _trava_agenda.setdefault(chave, threading.Lock()):  # um cadeado por chave: consulta lenta não trava as outras rotas
        agora = time.time()
        if chave in _cache_agenda and agora - _cache_agenda[chave][0] < TTL_AGENDA_S:
            return _cache_agenda[chave][1]
        try:
            valor = produzir()
        except Exception as exc:  # noqa: BLE001
            if chave in _cache_agenda:  # fonte caiu: melhor o último valor guardado que nada
                log.warning("Agenda %s indisponível, usando o último valor: %s", chave, exc)
                return _cache_agenda[chave][1]
            raise
        _cache_agenda[chave] = (agora, valor)
        return valor


def _pauta_camara(evento_id: int) -> list[dict]:
    itens = []
    for it in _http.json(f"{CAMARA}/eventos/{evento_id}/pauta").get("dados") or []:
        prop = it.get("proposicao_") or it.get("proposicao") or {}
        if prop.get("siglaTipo") not in TIPOS_RELEVANTES:
            continue
        itens.append({"ordem": it.get("ordem"), "proposicao": f"{prop['siglaTipo']} {prop.get('numero')}/{prop.get('ano')}",
                      "ementa": prop.get("ementa"), "regime": it.get("regime"), "situacao": it.get("situacaoItem")})
    return itens


def _deliberativa(e: dict) -> bool:
    t = (e.get("descricaoTipo") or "").casefold()
    return "deliberativa" in t and "não deliberativa" not in t


def _camara_agenda(hoje: date) -> dict:
    def eventos(ini: date, fim: date, ordem: str) -> list[dict]:
        return _http.json(f"{CAMARA}/orgaos/180/eventos", {"dataInicio": ini.isoformat(), "dataFim": fim.isoformat(),
                                                           "itens": 100, "ordem": ordem,
                                                           "ordenarPor": "dataHoraInicio"}).get("dados") or []

    proximos = eventos(hoje, hoje + timedelta(days=14), "ASC")
    sessoes = []
    for e in proximos:
        itens = _pauta_camara(e["id"]) if _deliberativa(e) and e.get("situacao") != "Cancelada" else []
        sessoes.append({"id": e["id"], "inicio": e["dataHoraInicio"], "tipo": e.get("descricaoTipo"),
                        "situacao": e.get("situacao"), "deliberativa": _deliberativa(e),
                        "descricao": (e.get("descricao") or "").strip().split("\n")[0][:200], "itens": itens})
    ultima = None
    if not any(s["deliberativa"] and s["itens"] for s in sessoes):
        # nada relevante marcado: mostra a pauta da última sessão deliberativa, identificada como passada
        for e in eventos(hoje - timedelta(days=30), hoje, "DESC"):
            if _deliberativa(e):
                itens = _pauta_camara(e["id"])
                if itens:
                    ultima = {"id": e["id"], "inicio": e["dataHoraInicio"], "tipo": e.get("descricaoTipo"), "itens": itens}
                    break
    return {"sessoes": sessoes, "ultima_deliberativa": ultima,
            "fonte": {"nome": "Câmara — eventos do Plenário e pauta", "url": "https://www.camara.leg.br/agenda"}}


def _lista(x: Any) -> list:
    return x if isinstance(x, list) else [] if x is None else [x]


def _senado_agenda(hoje: date) -> dict:
    sessoes = []
    for i in range(0, 10):
        dia = hoje + timedelta(days=i)
        if dia.weekday() > 4:
            continue
        r = _http.json(f"{SENADO}/plenario/agenda/dia/{dia:%Y%m%d}")
        ss = ((r.get("AgendaPlenario") or {}).get("Sessoes") or {}).get("Sessao")
        for s in _lista(ss):
            if s.get("Casa") != "SF":
                continue
            materias = []
            for m in _lista((s.get("Materias") or {}).get("Materia")):
                if m.get("SiglaMateria") not in TIPOS_RELEVANTES:
                    continue
                materias.append({"proposicao": m.get("DescricaoIdentificacaoMateria"), "ementa": m.get("Ementa"),
                                 "apelido": (m.get("Cabecalho") or "").strip().strip("()") or None,
                                 "apreciacao": (m.get("Apreciacao") or "").strip() or None})
            sessoes.append({"data": s.get("Data"), "hora": s.get("Hora"), "tipo": (s.get("TipoSessao") or "").strip().title(),
                            "situacao": s.get("SituacaoSessao"), "pauta_confirmada": s.get("PautaConfirmada") == "Sim",
                            "materias": materias})
    # votações nominais abertas recentes (as secretas — autoridades, embaixadores — não mostram voto individual)
    ini = hoje - timedelta(days=45)
    votos = _http.json(f"{SENADO}/votacao", {"dataInicio": ini.isoformat(), "dataFim": hoje.isoformat()})
    recentes, vistos = [], set()
    for v in sorted(votos if isinstance(votos, list) else [], key=lambda x: (x.get("dataSessao") or "", x.get("sequencialVotacao") or 0), reverse=True):
        if v.get("votacaoSecreta") != "N" or v.get("sigla") not in TIPOS_RELEVANTES:
            continue
        if v.get("identificacao") in vistos:  # 1º e 2º turno da mesma matéria: fica o mais recente
            continue
        vistos.add(v.get("identificacao"))
        cont = {"Sim": 0, "Não": 0}
        for x in v.get("votos") or []:
            if x.get("siglaVotoParlamentar") in cont:
                cont[x["siglaVotoParlamentar"]] += 1
        recentes.append({"data": v.get("dataSessao"), "proposicao": v.get("identificacao"), "ementa": v.get("ementa"),
                         "descricao": v.get("descricaoVotacao"), "sim": cont["Sim"], "nao": cont["Não"],
                         "resultado": {"A": "Aprovada", "R": "Rejeitada"}.get(v.get("resultadoVotacao"), v.get("resultadoVotacao"))})
        if len(recentes) >= 6:
            break
    return {"sessoes": sessoes, "ultimas_votacoes": recentes,
            "fonte": {"nome": "Senado — agenda do Plenário e votações", "url": "https://www25.senado.leg.br/web/atividade/plenario"}}


def agenda() -> dict:
    """O que está na mesa: cada Casa é independente; se uma fonte cair, a outra continua e o erro é declarado."""
    hoje = date.today()
    saida: dict[str, Any] = {"consultado_em": datetime.now().isoformat(timespec="seconds"), "hoje": hoje.isoformat(), "erros": []}
    for chave, fn in (("camara", _camara_agenda), ("senado", _senado_agenda)):
        try:
            saida[chave] = _memo_agenda(f"{chave}-{hoje}", lambda fn=fn: fn(hoje))
        except Exception as exc:  # noqa: BLE001
            log.warning("Agenda %s indisponível: %s", chave, exc)
            saida[chave] = None
            saida["erros"].append({"id": chave, "mensagem": "Fonte oficial indisponível no momento."})
    if saida["camara"] is None and saida["senado"] is None:
        raise ErroFonte("Câmara e Senado indisponíveis.")
    saida["nota"] = ("Mostramos só projetos de peso (PEC, PLP, PL, MPV e PDL); requerimentos, homenagens e sessões solenes ficam de fora. "
                     "A pauta pode mudar até a hora da sessão.")
    return saida


# --------------------------------------------------------------------------- #
# Quem dá as cartas: líderes (Governo, oposição/minoria, bancadas) e presidentes das Casas
# --------------------------------------------------------------------------- #
URL_LIDERANCA = f"{SENADO}/composicao/lideranca"   # lideranças das duas Casas e do Congresso (casa CD, SF, CN)
FOTO_CAMARA = "https://www.camara.leg.br/internet/deputado/bandep/{}.jpg"
FOTO_SENADO = "https://www.senado.leg.br/senadores/img/fotos-oficiais/senador{}.jpg"
PERFIL_CAMARA = "https://www.camara.leg.br/deputados/{}"
PERFIL_SENADO = "https://www25.senado.leg.br/web/senadores/senador/-/perfil/{}"


def _pessoa(x: dict) -> dict:
    cam = x.get("codigoDeputado")
    cod = x.get("codigoParlamentar")
    return {"nome": x.get("nomeParlamentar"), "partido": x.get("siglaPartidoFiliacao"), "uf": x.get("uf"),
            "desde": x.get("dataDesignacao"),
            "foto": FOTO_CAMARA.format(cam) if cam else FOTO_SENADO.format(cod) if cod else None,
            "url": PERFIL_CAMARA.format(cam) if cam else PERFIL_SENADO.format(cod) if cod else None}


def _liderancas() -> dict:
    d = _http.json(URL_LIDERANCA)
    if not isinstance(d, list) or not d:
        raise ErroFonte("Formato inesperado na lista de lideranças do Senado.")
    lideres = [x for x in d if x.get("siglaTipoLideranca") == "L"]

    def achar(casa: str, rotulo: str, unidade: str) -> dict | None:
        for x in lideres:
            if x["casa"] == casa and x["descricaoTipoUnidadeLideranca"].startswith(unidade):
                return {"cargo": rotulo, "casa": casa, **_pessoa(x)}
        return None

    governo = [achar("CN", "Líder do Governo no Congresso", "Liderança do Governo"),
               achar("CD", "Líder do Governo na Câmara", "Liderança do Governo"),
               achar("SF", "Líder do Governo no Senado", "Liderança do Governo")]
    oposicao = [achar("CN", "Líder da Oposição no Congresso", "Liderança da Oposição"),
                achar("CD", "Líder da Minoria na Câmara", "Liderança da Minoria na Câmara"),
                achar("SF", "Líder da Oposição no Senado", "Liderança da Oposição")]
    maioria = [achar("CD", "Líder da Maioria na Câmara", "Liderança da Maioria na Câmara"),
               achar("SF", "Líder da Maioria no Senado", "Liderança da Maioria")]

    ativos: dict[str, int] = {}
    try:
        dd = _dados()
        for dep in dd["deputados"]:
            if _ativo(dep, dd["ate_data"]):
                ativos[dep["partido"]] = ativos.get(dep["partido"], 0) + 1
    except ErroFonte:
        pass

    def grupos(casa: str) -> list[dict]:
        saida = []
        for x in lideres:
            u = x["descricaoTipoUnidadeLideranca"]
            if x["casa"] != casa or not (u.startswith("Liderança de Partido") or u.startswith("Liderança de Bloco")):
                continue
            bloco = u.startswith("Liderança de Bloco")
            nome = x.get("nomeBloco") or x.get("siglaBloco") or x.get("siglaPartidoFiliacao") or ""
            cadeiras = None
            if casa == "CD":  # só soma quando os partidos do bloco vêm escritos no nome (ex.: "UNIÃO, PP, PSD…")
                if bloco:
                    partes = [t for t in nome.replace(",", " ").split() if t in ativos]
                    cadeiras = sum(ativos[t] for t in partes) if partes else None
                else:
                    cadeiras = ativos.get(nome)
            saida.append({"tipo": "bloco" if bloco else "partido", "nome": nome, "cadeiras": cadeiras, "lider": _pessoa(x)})
        return sorted(saida, key=lambda g: (-(g["cadeiras"] or 0), g["nome"].casefold()))

    presidentes = {}
    try:
        import bancadas
        for casa in bancadas.bancadas()["casas"]:
            presidentes[casa["id"]] = casa.get("presidente")
    except Exception as exc:  # noqa: BLE001  (complementar: se falhar, a página segue sem eles)
        log.warning("Presidentes das Casas indisponíveis: %s", exc)
    return {
        "presidentes": presidentes,
        "governo": [x for x in governo if x], "oposicao": [x for x in oposicao if x], "maioria": [x for x in maioria if x],
        "camara": grupos("CD"), "senado": grupos("SF"),
        "fonte": {"nome": "Senado Federal — composição das lideranças (dados abertos; inclui a Câmara)", "url": URL_LIDERANCA},
        "notas": [
            "A lista vem direto da base oficial de lideranças; o Governo indica seus líderes e as Casas registram a designação.",
            "Na Câmara a base oficial usa “Minoria” e “Maioria” em vez de “Oposição”: mostramos o nome oficial do cargo.",
            "Líderes de bloco e de federação falam por vários partidos; o número de deputados só aparece quando os partidos do bloco constam no nome.",
            "O presidente da Câmara e o do Senado decidem o que entra na pauta; os líderes negociam com eles na reunião semanal de líderes e orientam o voto das bancadas.",
        ],
    }


def liderancas() -> dict:
    return {"consultado_em": datetime.now().isoformat(timespec="seconds"), **_memo_agenda("liderancas", _liderancas)}


# --------------------------------------------------------------------------- #
# Um projeto em linguagem simples (aba lateral)
# --------------------------------------------------------------------------- #
TIPOS_EXPLICADOS = {
    "PEC": ("Proposta de Emenda à Constituição", "Muda o texto da Constituição. Precisa de 3/5 dos votos (308 deputados e 49 senadores), em dois turnos em cada Casa. Não vai à sanção: o Congresso promulga."),
    "PLP": ("Projeto de Lei Complementar", "Regulamenta pontos que a Constituição manda detalhar em lei complementar. Precisa de maioria absoluta (257 deputados e 41 senadores). Passa pelas duas Casas e vai à sanção do Presidente."),
    "PL": ("Projeto de Lei", "Lei ordinária. Maioria simples dos presentes. Passa pelas duas Casas e vai à sanção do Presidente, que pode sancionar ou vetar."),
    "PLV": ("Projeto de Lei de Conversão", "Transforma uma medida provisória em lei, com as alterações do Congresso. Vai à sanção do Presidente."),
    "MPV": ("Medida Provisória", "O Presidente edita e ela vale como lei na hora. Tem 60 dias, prorrogáveis por mais 60, para a Câmara e o Senado aprovarem; se não votarem, perde a validade."),
    "PDL": ("Projeto de Decreto Legislativo", "Trata de assuntos só do Congresso (aprovar tratados, sustar atos do Executivo, escolher ministros do TCU). Não vai à sanção."),
}
_CAMINHO = [
    "Apresentação: o autor protocola o projeto e a Mesa o distribui às comissões.",
    "Comissões: analisam o mérito e a constitucionalidade (o regime de urgência pode pular essa etapa).",
    "Plenário: todos os deputados (513) ou senadores (81) votam.",
    "Casa revisora: se nasceu na Câmara, o Senado revisa (e vice-versa). Se mudar o texto, ele volta.",
    "Sanção ou promulgação: leis vão ao Presidente, que sanciona ou veta; emendas à Constituição são promulgadas pelo Congresso.",
]
_RE_ROTULO = re.compile(r"\s*([A-Z]{2,4})\s+(\d{1,5})/(\d{4})\s*")


def projeto(rotulo: str) -> dict:
    """Detalhe de um projeto pelo rótulo ('PEC 221/2019'), com dados da Câmara. Falha de rede vira ErroFonte."""
    m = _RE_ROTULO.fullmatch(rotulo or "")
    if not m or m.group(1) not in TIPOS_EXPLICADOS:
        raise ValueError("Informe o projeto como PEC 221/2019, PLP 93/2023, PL 1/2024…")
    sigla, numero, ano = m.group(1), int(m.group(2)), int(m.group(3))
    return _memo_agenda(f"projeto-{sigla}-{numero}-{ano}", lambda: _projeto(sigla, numero, ano))


def _projeto(sigla: str, numero: int, ano: int) -> dict:
    nome_tipo, explicacao = TIPOS_EXPLICADOS[sigla]
    base = {"proposicao": f"{sigla} {numero}/{ano}", "apelido": _apelidos().get(f"{sigla} {numero}/{ano}"), "tipo": nome_tipo, "tipo_explicacao": explicacao, "caminho": _CAMINHO,
            "fonte": {"nome": "Câmara dos Deputados — dados abertos", "url": "https://www.camara.leg.br/busca-portal/proposicoes/pesquisa-simplificada"}}
    achados = _http.json(f"{CAMARA}/proposicoes", {"siglaTipo": sigla, "numero": numero, "ano": ano, "itens": 1}).get("dados") or []
    if not achados:
        return {**base, "encontrado": False,
                "aviso": "Não achamos este projeto na base da Câmara (pode ter nascido no Senado). A página oficial do Senado tem o histórico."}
    pid = achados[0]["id"]
    d = _http.json(f"{CAMARA}/proposicoes/{pid}").get("dados") or {}
    st = d.get("statusProposicao") or {}
    autores = _http.json(f"{CAMARA}/proposicoes/{pid}/autores").get("dados") or []
    tram = _http.json(f"{CAMARA}/proposicoes/{pid}/tramitacoes").get("dados") or []
    tram.sort(key=lambda t: t.get("dataHora") or "")
    principais = [a for a in autores if a.get("proponente")] or autores[:1]
    plenario = None
    try:
        pauta = next((p for p in _dados()["pautas"] if p["proposicao"] == base["proposicao"]), None)
        if pauta:
            plenario = {"data": pauta["data"], "orientacao_governo": pauta["orientacao_governo"], "aprovada": pauta["aprovada"],
                        "sim": pauta["sim"], "nao": pauta["nao"],
                        "partidos": [{"sigla": s, "sim": v[0], "nao": v[1], "pct_sim": _pct(v[0], v[0] + v[1])}
                                     for s, v in sorted(pauta["partidos"].items()) if v[0] + v[1] >= 3]}
    except ErroFonte:
        pass
    return {
        **base, "encontrado": True, "id": pid, "apresentado_em": (d.get("dataApresentacao") or "")[:10],
        "ementa": d.get("ementa"), "ementa_detalhada": d.get("ementaDetalhada") or None,
        "palavras_chave": [k.strip() for k in (d.get("keywords") or "").split(",") if k.strip()][:8],
        "autoria": {"principais": [a.get("nome") for a in principais], "total": len(autores)},
        "situacao": {"descricao": st.get("descricaoSituacao"), "orgao": st.get("siglaOrgao"), "regime": st.get("regime"),
                     "apreciacao": st.get("apreciacao"), "despacho": st.get("despacho"), "data": (st.get("dataHora") or "")[:10]},
        "tramitacoes": [{"data": (t.get("dataHora") or "")[:10], "orgao": t.get("siglaOrgao"), "descricao": t.get("descricaoTramitacao"),
                         "despacho": (t.get("despacho") or "")[:260]} for t in reversed(tram[-8:])],
        "total_tramitacoes": len(tram),
        "plenario": plenario,
        "links": {"inteiro_teor": d.get("urlInteiroTeor"),
                  "pagina": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={pid}"},
    }
