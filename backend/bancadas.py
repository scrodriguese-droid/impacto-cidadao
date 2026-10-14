"""Composição partidária do Congresso Nacional — cadeiras por partido e limiares de votação.

Fontes (abertas, sem chave):
* Câmara dos Deputados — https://dadosabertos.camara.leg.br/api/v2/deputados (deputados em exercício)
* Senado Federal       — https://legis.senado.leg.br/dadosabertos/senador/lista/atual (senadores em exercício)

Só contagem de parlamentares por partido, na sigla informada pela própria fonte. Sem projeções,
sem juízo político; a ordem de apresentação é alfabética. Câmaras municipais: não há fonte aberta
consolidada e confiável da composição atual (o TSE publica o resultado da eleição, não quem está
em exercício), então a lacuna é declarada.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fontes import ClienteHTTP, ErroFonte

log = logging.getLogger("impacto.bancadas")

_http = ClienteHTTP(pausa=0.05, timeout=60.0)

TTL_S = 6 * 3600
_cache: dict[str, tuple[float, Any]] = {}
_trava = threading.Lock()

URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/deputados"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/senador/lista/atual"
PAGINA_CAMARA = "https://www.camara.leg.br/deputados/quem-sao"
PAGINA_SENADO = "https://www25.senado.leg.br/web/senadores/em-exercicio"

# Constituição Federal: Câmara 513 (art. 45 e LC 78/1993), Senado 81 (art. 46).
TOTAL_CONSTITUCIONAL = {"camara": 513, "senado": 81}


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _contar(siglas: list[str | None]) -> list[dict]:
    cont: dict[str, int] = {}
    for s in siglas:
        s = (s or "").strip() or "Sem partido"
        cont[s] = cont.get(s, 0) + 1
    return [{"sigla": s, "cadeiras": n} for s, n in sorted(cont.items(), key=lambda x: x[0].casefold())]


def _limiares(total: int, partidos: list[dict]) -> list[dict]:
    """Limiares sobre o total CONSTITUCIONAL de cadeiras (não sobre as ocupadas)."""
    maiores = sorted((p["cadeiras"] for p in partidos), reverse=True)

    def minimo_partidos(meta: int) -> int | None:
        soma = 0
        for i, n in enumerate(maiores, start=1):
            soma += n
            if soma >= meta:
                return i
        return None

    absoluta = total // 2 + 1
    itens = [
        ("simples", "Maioria simples", absoluta // 2 + 1,
         f"Mais votos “sim” que “não” entre os presentes, com quórum de {absoluta}. O mínimo de {absoluta // 2 + 1} "
         f"votos vale se só o quórum estiver presente; com todos presentes, são {total // 2 + 1}."),
        ("absoluta", "Maioria absoluta", absoluta,
         "Mais da metade de TODOS os membros da Casa, estejam presentes ou não (ex.: lei complementar)."),
        ("tres_quintos", "3/5 — emenda à Constituição (PEC)", math.ceil(total * 3 / 5),
         "Três quintos dos membros da Casa, em dois turnos de votação."),
    ]
    return [{"id": c, "nome": n, "votos": v, "nota": nota, "minimo_partidos": minimo_partidos(v)}
            for c, n, v, nota in itens]


def _casa(chave: str, nome: str, partidos: list[dict], url_api: str, pagina: str) -> dict:
    total = TOTAL_CONSTITUCIONAL[chave]
    ocupadas = sum(p["cadeiras"] for p in partidos)
    return {
        "id": chave, "nome": nome, "cadeiras_total": total, "cadeiras_ocupadas": ocupadas,
        "cadeiras_sem_titular": max(total - ocupadas, 0),
        "partidos": partidos, "n_partidos": len(partidos),
        "limiares": _limiares(total, partidos),
        "fonte": {"nome": f"{nome} — dados abertos", "url_api": url_api, "url_pagina": pagina},
    }


def _camara() -> dict:
    siglas: list[str | None] = []
    pagina = 1
    while True:
        r = _http.json(URL_CAMARA, {"itens": 100, "pagina": pagina, "ordem": "ASC", "ordenarPor": "nome"})
        dados = r.get("dados") or []
        siglas += [d.get("siglaPartido") for d in dados]
        if not dados or not any(l.get("rel") == "next" for l in r.get("links", [])):
            break
        pagina += 1
        if pagina > 20:
            raise ErroFonte("Paginação da Câmara não terminou.")
    if not siglas:
        raise ErroFonte("A Câmara não retornou deputados.")
    return _casa("camara", "Câmara dos Deputados", _contar(siglas), URL_CAMARA, PAGINA_CAMARA)


def _senado() -> dict:
    r = _http.get(URL_SENADO, accept="application/json").json()
    try:
        lista = r["ListaParlamentarEmExercicio"]["Parlamentares"]["Parlamentar"]
    except (KeyError, TypeError) as exc:
        raise ErroFonte("Formato inesperado na resposta do Senado.") from exc
    if isinstance(lista, dict):
        lista = [lista]
    siglas = [(p.get("IdentificacaoParlamentar") or p).get("SiglaPartidoParlamentar") for p in lista]
    if not siglas:
        raise ErroFonte("O Senado não retornou senadores.")
    return _casa("senado", "Senado Federal", _contar(siglas), URL_SENADO, PAGINA_SENADO)


def _memo(chave: str, produzir):  # noqa: ANN001
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < TTL_S:
        return _cache[chave][1]
    with _trava:
        agora = time.time()
        if chave in _cache and agora - _cache[chave][0] < TTL_S:
            return _cache[chave][1]
        valor = produzir()
        _cache[chave] = (agora, valor)
        return valor


def bancadas() -> dict:
    """Cada Casa é independente: se uma falhar, a outra continua e o erro é declarado."""
    casas, erros = [], []
    for chave, fn in (("camara", _camara), ("senado", _senado)):
        try:
            casas.append(_memo(chave, fn))
        except Exception as exc:  # noqa: BLE001
            log.warning("Fonte %s indisponível: %s", chave, exc)
            erros.append({"id": chave, "mensagem": "Fonte oficial indisponível no momento."})
    if not casas:
        raise ErroFonte("Câmara e Senado indisponíveis.")
    return {
        "consultado_em": _agora(),
        "casas": casas,
        "erros": erros,
        "camaras_municipais": {
            "disponivel": False,
            "motivo": ("Não há fonte aberta consolidada e confiável da composição ATUAL das câmaras municipais. "
                       "O TSE divulga o resultado da eleição de vereadores (eleitos por partido), mas não quem está "
                       "em exercício, nem trocas de partido, licenças e suplências. Para não apresentar um número "
                       "possivelmente desatualizado, não mostramos essa composição."),
            "url_fonte": "https://dadosabertos.tse.jus.br/",
        },
        "notas": [
            "Contagem de parlamentares em exercício por partido, conforme informado pela própria Casa; cadeiras sem titular não entram.",
            "Partidos federados ou em bloco continuam contados individualmente, como a fonte informa.",
            "Os limiares usam o total constitucional de cadeiras (513 e 81), não o número de parlamentares em exercício.",
            "É aritmética, não previsão: votar de acordo com o partido não é obrigatório.",
        ],
    }
