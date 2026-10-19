"""“Quem resolve o quê?”: relaciona um problema do dia a dia ao ente responsável e ao orçamento dele.

A matriz de competências fica em dados/competencias.json (feita à mão, com a base constitucional ou legal
de cada item quando há). A verba vem da dotação inicial da LOA declarada no RREO (SICONFI), por função e,
quando existe, por subfunção.
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import time
import unicodedata
from functools import lru_cache
from typing import Any

import orcamento
from config import RAIZ

ARQUIVO = RAIZ / "dados" / "competencias.json"
ARQUIVO_AREAS = RAIZ / "dados" / "competencias_areas.json"
SUGESTOES = ["vaga na creche", "buraco na rua", "demora para cirurgia", "remédio no posto", "assalto e polícia", "aposentadoria no INSS"]
_cache: dict[int, tuple[float, dict | None]] = {}
_trava = threading.Lock()


def _norm(t: str) -> str:
    s = unicodedata.normalize("NFKD", t or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


@lru_cache(maxsize=1)
def _matriz() -> list[dict]:
    return json.loads(ARQUIVO.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _areas() -> list[dict]:
    return json.loads(ARQUIVO_AREAS.read_text(encoding="utf-8"))


def areas() -> list[dict]:
    """As áreas de interesse, sem o detalhe (para os cartões de entrada)."""
    return [{"id": a["id"], "titulo": a["titulo"], "resumo": a["resumo"]} for a in _areas()]


def area(area_id: str, municipio: int | None = None) -> dict | None:
    """Uma área por inteiro: quem faz o quê em cada ente, como os três Poderes atuam e cada serviço em detalhe."""
    a = next((x for x in _areas() if x["id"] == area_id), None)
    if not a:
        return None
    por_id = {it["id"]: it for it in _matriz()}
    servicos = []
    for iid in a["itens"]:
        it = por_id[iid]
        servicos.append({k: v for k, v in it.items() if k not in ("palavras_chave",)} | {"verba": _verba(it, municipio)})
    return {**{k: v for k, v in a.items() if k != "itens"}, "servicos": servicos}


def _singular(q: str) -> str:
    return " ".join(p[:-1] if len(p) > 3 and p.endswith("s") else p for p in q.split())


def _frase(k: str, texto: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", texto) is not None


def _pontuar(consulta: str, item: dict) -> int:
    variantes = {consulta, _singular(consulta)}
    melhor = 0
    for q in variantes:
        pontos = 0
        for kw in item["palavras_chave"]:
            k = _norm(kw)
            if not k:
                continue
            if _frase(k, q):
                pontos += 2 + len(k.split())          # a palavra-chave aparece inteira na consulta
            elif len(q) >= 4 and _frase(q, k):
                pontos += 1                            # a consulta é uma palavra da palavra-chave (ex.: "creche")
        melhor = max(melhor, pontos)
    return melhor


def _dotacao(ente: int) -> dict | None:
    agora = time.time()
    with _trava:
        if ente in _cache and agora - _cache[ente][0] < 6 * 3600:
            return _cache[ente][1]
    d = orcamento._dotacao_inicial(ente)
    with _trava:
        _cache[ente] = (agora, d)
    return d


def _ente(esfera: str, municipio: int) -> int:
    return {"Municipal": municipio, "Estadual": municipio // 100000, "Federal": 1}[esfera]


def _verba(item: dict, municipio: int | None) -> list[dict]:
    if not municipio or not item.get("funcao_orcamento"):
        return []
    saida = []
    for esfera in item["esferas"]:
        din = _dotacao(_ente(esfera, municipio))
        if not din:
            continue
        funcao = next((f for f in din["funcoes"] if f["nome"] == item["funcao_orcamento"]), None)
        sub_nome = item.get("subfuncao_orcamento")
        sub_valor = (din.get("subfuncoes") or {}).get(orcamento.economia.normalizar(sub_nome)) if sub_nome else None
        saida.append({
            "esfera": esfera, "instituicao": din["instituicao"], "ano": din["ano"],
            "funcao": item["funcao_orcamento"], "valor_funcao": funcao["valor"] if funcao else None,
            "subfuncao": sub_nome if sub_valor is not None else None, "valor_subfuncao": sub_valor,
            "despesa_total": din["despesa_autorizada"],
        })
    return saida


ORGAOS = {
    "saude": {"Municipal": "Secretaria Municipal de Saúde", "Estadual": "Secretaria de Estado da Saúde", "Federal": "Ministério da Saúde"},
    "educacao": {"Municipal": "Secretaria Municipal de Educação", "Estadual": "Secretaria de Estado da Educação", "Federal": "Ministério da Educação"},
    "seguranca": {"Municipal": "Secretaria Municipal de Segurança ou equivalente (Guarda Municipal)", "Estadual": "Secretaria de Estado da Segurança Pública",
                  "Federal": "Ministério da Justiça e Segurança Pública"},
    "assistencia": {"Municipal": "Secretaria Municipal de Assistência Social", "Estadual": "Secretaria de Estado de Desenvolvimento Social",
                    "Federal": "Ministério do Desenvolvimento e Assistência Social"},
    "outros": {"Municipal": "Prefeitura Municipal", "Estadual": "Governo do Estado", "Federal": "Governo Federal"},
}
COBRAR = {"Municipal": ["Prefeito", "Vereadores da sua cidade"], "Estadual": ["Governador", "Deputados Estaduais"],
          "Federal": ["Presidente da República", "Deputados Federais e Senadores"]}
FUNCAO_POR_CATEGORIA = {"saude": ("Saúde", "Atenção Básica"), "educacao": ("Educação", None), "seguranca": ("Segurança pública", None),
                        "assistencia": ("Assistência social", None), "outros": (None, None)}


def ficha(municipio: int, categoria: str, esfera: str) -> dict:
    """Quem responde e quanto a esfera autorizou na LOA para a área do equipamento."""
    funcao, sub = FUNCAO_POR_CATEGORIA[categoria]
    verba = _verba({"esferas": [esfera], "funcao_orcamento": funcao, "subfuncao_orcamento": None}, municipio) if funcao else []
    return {"categoria": categoria, "esfera": esfera, "orgao": ORGAOS[categoria][esfera], "quem_cobrar": COBRAR[esfera],
            "verba": verba[0] if verba else None}


def buscar(consulta: str, municipio: int | None = None, limite: int = 3) -> dict:
    q = _norm(consulta)
    if len(q) < 2:
        return {"consulta": consulta, "resultados": [], "sugestoes": SUGESTOES, "areas": areas()}
    ranking = sorted(((_pontuar(q, it), it) for it in _matriz()), key=lambda x: -x[0])
    achados = [it for pts, it in ranking if pts > 0][:limite]
    if not achados:  # erro de digitação: troca cada palavra pela palavra-chave mais parecida
        vocabulario = sorted({w for it in _matriz() for kw in it["palavras_chave"] for w in _norm(kw).split() if len(w) >= 4})
        corrigida = " ".join((difflib.get_close_matches(w, vocabulario, n=1, cutoff=0.82) or [w])[0] if len(w) >= 4 else w for w in q.split())
        if corrigida != q:
            ranking = sorted(((_pontuar(corrigida, it), it) for it in _matriz()), key=lambda x: -x[0])
            achados = [it for pts, it in ranking if pts > 0][:limite]
    resultados = []
    for it in achados:
        resultados.append({k: v for k, v in it.items() if k != "palavras_chave"} | {"verba": _verba(it, municipio)})
    return {"consulta": consulta, "resultados": resultados, "sugestoes": SUGESTOES, "areas": areas()}
