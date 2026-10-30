"""Agenda pública do Presidente da República (Palácio do Planalto) e o que ela mostra sobre a relação com o Congresso.

Fonte: página oficial https://www.gov.br/planalto/pt-br/acompanhe-o-planalto/agenda-do-presidente-da-republica-lula/agenda-do-presidente-da-republica
(um endereço por dia, AAAA-MM-DD). A página não tem API; lemos o HTML público, com identificação honesta, e guardamos por 30 minutos.
Cada compromisso traz horário, título (com quem ou o quê), participantes e local. NÃO há campo de "pauta": não inventamos assunto.

A Lei 12.813/2013 manda autoridades do Executivo publicarem seus compromissos; os encontros com interessados também ficam no
sistema e-Agendas da CGU (https://eagendas.cgu.gov.br), que exige consulta no próprio site e por isso só é linkado.

A classificação dos compromissos (Congresso, ministros, cerimônias, fora de Brasília) é automática, por palavras no título e nos
participantes, e é declarada como tal na tela. Só leitura: nada é gravado.
"""
from __future__ import annotations

import html as _html
import logging
import re
import threading
import time
from datetime import date, datetime

from fontes import ClienteHTTP, ErroFonte

log = logging.getLogger("impacto.agenda_presidente")

BASE = "https://www.gov.br/planalto/pt-br/acompanhe-o-planalto/agenda-do-presidente-da-republica-lula/agenda-do-presidente-da-republica"
TTL_S = 1800
DIAS = 5                      # dias mais recentes publicados que mostramos
_http = ClienteHTTP(pausa=0.1, timeout=20.0, tentativas=2)
_cache: dict[str, tuple[float, dict]] = {}
_trava = threading.Lock()

_RE_DATAS = re.compile(r"agenda-do-presidente-da-republica/(\d{4}-\d{2}-\d{2})")
_RE_BLOCO = re.compile(r'<li class="item-compromisso-wrapper">')
_RE_TAG = re.compile(r"<[^>]+>")

# Classificação automática. Palavras no título ou nos participantes (sem acento e em minúsculas).
CATEGORIAS = {
    "congresso": ("presidente da camara", "presidente do senado", "senador", "senadora", "senadores", "deputado", "deputada",
                  "deputados", "lider", "lideres", "congresso", "camara dos deputados", "senado federal", "bancada", "frente parlamentar"),
    "ministros": ("ministro", "ministra", "ministros", "ministras"),
    "cerimonias": ("cerimonia", "sancao", "assinatura", "lancamento", "inauguracao", "solenidade", "entrega"),
}
LOCAIS_SEDE = ("palacio", "brasilia", "granja do torto", "torto", "itamaraty", "residencia", "alvorada", "planalto", "esplanada",
               "ministerio", "congresso", "supremo", "bsb")


def _sem_acento(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def _limpo(t: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_RE_TAG.sub(" ", t))).strip().strip(",").strip()


def _casa(padrao: str, bloco: str) -> str:
    m = re.search(padrao, bloco, re.S)
    return _limpo(m.group(1)) if m else ""


def parse_dia(html: str) -> list[dict]:
    """Compromissos de uma página diária (vazia se a página não tiver nenhum)."""
    saida = []
    for b in _RE_BLOCO.split(html)[1:]:
        titulo = _casa(r'<h2 class="compromisso-titulo[^"]*">(.*?)</h2>', b)
        if not titulo:
            continue
        part_div = re.search(r'<div class="compromisso-participantes">(.*?)</ul>', b, re.S)
        participantes = [_limpo(x) for x in re.findall(r"<li>(.*?)</li>", part_div.group(1), re.S)] if part_div else []
        saida.append({
            "inicio": _casa(r'class="compromisso-inicio">(.*?)</time>', b) or None,
            "fim": _casa(r'class="compromisso-fim">(.*?)</time>', b) or None,
            "titulo": titulo,
            "participantes": [p for p in participantes if p],
            "local": _casa(r'<div class="compromisso-local">(.*?)</div>', b) or None,
        })
    return saida


def classificar(c: dict, nomes: tuple[str, ...] = ()) -> list[str]:
    """Categorias do compromisso (podem ser várias). `nomes` = nomes de líderes e presidentes das Casas, para casar no texto."""
    texto = _sem_acento(" ".join([c["titulo"], *c["participantes"]]))
    # palavra inteira: "Cerimonial" (chefe do cerimonial) não é "cerimônia"
    cats = [cat for cat, palavras in CATEGORIAS.items() if any(re.search(r"\b" + re.escape(p) + r"\b", texto) for p in palavras)]
    if any(_sem_acento(n) in texto for n in nomes if n) and "congresso" not in cats:
        cats.append("congresso")
    local = _sem_acento(c.get("local") or "")
    if local and not any(s in local for s in LOCAIS_SEDE):
        cats.append("fora")
    return cats


def _nomes_do_congresso() -> tuple[str, ...]:
    """Nomes de presidentes das Casas e líderes (da base de lideranças); vazio se indisponível."""
    try:
        import governismo
        l = governismo.liderancas()
        pessoas = [*(p for p in (l.get("presidentes") or {}).values() if p), *l["governo"], *l["oposicao"], *l["maioria"],
                   *(g["lider"] for g in l["camara"] + l["senado"])]
        return tuple(sorted({p["nome"] for p in pessoas if p and p.get("nome")}))
    except Exception as exc:  # noqa: BLE001
        log.warning("Nomes de lideranças indisponíveis para a agenda: %s", exc)
        return ()


def _coletar() -> dict:
    indice = _http.get(BASE, accept="text/html").text
    datas = sorted(set(_RE_DATAS.findall(indice)), reverse=True)[:DIAS]
    if not datas:
        raise ErroFonte("A página da agenda não listou dias publicados.")
    nomes = _nomes_do_congresso()
    dias = []
    for d in datas:
        try:
            comps = parse_dia(_http.get(f"{BASE}/{d}", accept="text/html").text)
        except ErroFonte as exc:
            log.warning("Agenda de %s indisponível: %s", d, exc)
            continue
        for c in comps:
            c["categorias"] = classificar(c, nomes)
        dias.append({"data": d, "compromissos": comps})
    if not dias:
        raise ErroFonte("Nenhum dia da agenda pôde ser lido.")
    todos = [c for d in dias for c in d["compromissos"]]
    resumo = {cat: sum(1 for c in todos if cat in c["categorias"]) for cat in ("congresso", "ministros", "cerimonias", "fora")}
    resumo["total"] = len(todos)
    return {
        "dias": dias, "resumo": resumo, "periodo": {"de": dias[-1]["data"], "ate": dias[0]["data"]},
        "fonte": {"nome": "Palácio do Planalto — agenda do Presidente da República", "url": BASE},
        "e_agendas": "https://eagendas.cgu.gov.br/",
        "notas": [
            "A agenda mostra com quem o Presidente se reúne, não o que foi tratado: a fonte oficial não informa a pauta de cada encontro.",
            "Mostramos os últimos dias publicados; a agenda do dia costuma sair ao longo do dia e pode mudar.",
            "As etiquetas (Congresso, ministros, cerimônias, fora de Brasília) são uma classificação automática por palavras no título e nos participantes e podem errar. Congresso inclui presidentes das Casas, líderes e parlamentares citados pelo nome.",
            "Encontros com representantes de setores e empresas, e as agendas de ministros, ficam no e-Agendas da CGU, que exige consulta no próprio site.",
        ],
    }


def agenda() -> dict:
    chave = date.today().isoformat()
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < TTL_S:
        return _cache[chave][1]
    with _trava:
        agora = time.time()
        if chave in _cache and agora - _cache[chave][0] < TTL_S:
            return _cache[chave][1]
        try:
            valor = _coletar()
        except Exception:
            if _cache:  # fonte caiu: melhor o último valor lido que nada
                return max(_cache.values(), key=lambda v: v[0])[1]
            raise
        valor["consultado_em"] = datetime.now().isoformat(timespec="seconds")
        _cache[chave] = (agora, valor)
        return valor
