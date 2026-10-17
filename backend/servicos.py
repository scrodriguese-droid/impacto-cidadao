"""Guia de serviços públicos: o que cada esfera faz, como pedir e onde está o portal oficial.

Lê dados/servicos.json (catálogo escrito à mão, com base legal e links oficiais conferidos) e dados/competencias_areas.json
(a matriz por ente e a atuação dos três Poderes de cada área). A verba prevista vem da LOA declarada ao Tesouro Nacional,
pela mesma rotina do “Quem resolve o quê?”.

Links por ente: o federal vale para todos. Os de São Paulo (código IBGE do estado 35) e de Limeira (3526902) só aparecem
quando o município escolhido é de lá; nos demais, a tela orienta a procurar a Carta de Serviços ao Cidadão da prefeitura
(Lei 13.460/2017) e o portal do Estado. Assim a página nunca mostra o portal de uma cidade como se fosse de outra.
"""
from __future__ import annotations

import json
from functools import lru_cache

import competencias
from config import RAIZ

ARQUIVO = RAIZ / "dados" / "servicos.json"
ESFERAS = {"municipio": "Municipal", "estado": "Estadual", "uniao": "Federal"}
SP_UF = 35
LIMEIRA = 3526902


@lru_cache(maxsize=1)
def _catalogo() -> dict:
    return json.loads(ARQUIVO.read_text(encoding="utf-8"))


def _links(servico: dict, municipio: int | None) -> dict:
    """Portais oficiais do serviço, por esfera, conforme a cidade escolhida."""
    l = servico["links"]
    estado = (municipio // 100000) if municipio else None
    return {
        "federal": l["federal"],
        "estadual": l["estadual_sp"] if estado == SP_UF else [],
        "municipal": l["municipal_limeira"] if municipio == LIMEIRA else [],
        "estado_com_portal": estado == SP_UF,
        "municipio_com_portal": municipio == LIMEIRA,
    }


def referencias(municipio: int | None) -> dict:
    r = _catalogo()["referencias"]
    estado = (municipio // 100000) if municipio else None
    return {
        "federal": r["federal"],
        "estadual": r["estadual_sp"] if estado == SP_UF else [],
        "municipal": r["municipal_limeira"] if municipio == LIMEIRA else [],
        "carta_servicos_lei": r["lei_carta_servicos"],
        "estado_com_portal": estado == SP_UF,
        "municipio_com_portal": municipio == LIMEIRA,
    }


def _resumo(s: dict) -> dict:
    return {"slug": s["slug"], "area": s["area"], "nome": s["nome"], "resumo": s["resumo"], "esferas": list(s["entes"])}


def indice(municipio: int | None = None) -> dict:
    """As áreas com a lista curta de serviços de cada uma, e os portais oficiais de referência."""
    por_area: dict[str, list[dict]] = {}
    for s in _catalogo()["servicos"]:
        por_area.setdefault(s["area"], []).append(_resumo(s))
    areas = [{"id": a["id"], "titulo": a["titulo"], "resumo": a["resumo"], "servicos": por_area.get(a["id"], [])} for a in competencias._areas()]
    return {"areas": areas, "referencias": referencias(municipio), "total_servicos": len(_catalogo()["servicos"])}


def area(area_id: str, municipio: int | None = None) -> dict | None:
    a = next((x for x in competencias._areas() if x["id"] == area_id), None)
    if not a:
        return None
    servicos = [_resumo(s) for s in _catalogo()["servicos"] if s["area"] == area_id]
    return {**{k: v for k, v in a.items() if k != "itens"}, "servicos": servicos, "referencias": referencias(municipio)}


def servico(area_id: str, slug: str, municipio: int | None = None) -> dict | None:
    s = next((x for x in _catalogo()["servicos"] if x["area"] == area_id and x["slug"] == slug), None)
    if not s:
        return None
    item = next((i for i in competencias._matriz() if i["id"] == s["competencia_id"]), None)
    verba = competencias._verba(item, municipio) if item else []
    a = next((x for x in competencias._areas() if x["id"] == area_id), {})
    return {**{k: v for k, v in s.items() if k not in ("links", "palavras_chave")}, "links": _links(s, municipio), "verba": verba,
            "funcao_orcamento": (item or {}).get("funcao_orcamento"), "quem_cobrar": (item or {}).get("quem_cobrar", []),
            "area_titulo": a.get("titulo", area_id), "mapa": (item or {}).get("mapa")}


def por_esfera(esfera_url: str) -> dict | None:
    esfera = ESFERAS.get(esfera_url)
    if not esfera:
        return None
    titulos = {a["id"]: a["titulo"] for a in competencias._areas()}
    ordem = [a["id"] for a in competencias._areas()]
    grupos: dict[str, list[dict]] = {i: [] for i in ordem}
    for s in _catalogo()["servicos"]:
        if esfera in s["entes"]:
            grupos[s["area"]].append({"slug": s["slug"], "nome": s["nome"], "papel": s["entes"][esfera]})
    return {"esfera": esfera, "rotulo": {"Municipal": "Município (Prefeitura e Câmara Municipal)",
                                         "Estadual": "Estado (Governo Estadual e Assembleia Legislativa)",
                                         "Federal": "União (Governo Federal e Congresso Nacional)"}[esfera],
            "areas": [{"id": i, "titulo": titulos[i], "servicos": grupos[i]} for i in ordem if grupos[i]]}


def buscar(consulta: str, limite: int = 5) -> list[dict]:
    """Serviços do catálogo que combinam com o texto (usa as mesmas normalização e palavras-chave da busca antiga)."""
    q = competencias._norm(consulta)
    if len(q) < 2:
        return []
    ranking = sorted(((competencias._pontuar(q, {"palavras_chave": s["palavras_chave"] + [s["nome"]]}), s) for s in _catalogo()["servicos"]), key=lambda x: -x[0])
    return [_resumo(s) for pts, s in ranking if pts > 0][:limite]
