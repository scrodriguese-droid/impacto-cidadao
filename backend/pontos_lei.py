"""Principais pontos do texto de cada lei do ciclo (PPA e LDO), lidos do documento oficial na Câmara dos Deputados.

O texto vem do PDF do projeto de lei (campo `urlInteiroTeor` da API de Dados Abertos da Câmara). Cada ponto só entra se
for encontrado no texto: nada é presumido. O PDF é grande, então o resumo é lido uma vez e guardado em disco.

Atenção: o PDF é o projeto como o Executivo o enviou. O Congresso pode ter alterado o texto antes da sanção; o aviso vai junto.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

import orcamento
from config import RAIZ

log = logging.getLogger("impacto.pontos_lei")
PASTA = RAIZ / "dados" / "orcamento"


def _num_extenso_br(txt: str) -> float:
    return float(txt.replace(".", "").replace(",", "."))


def _bi(v: float) -> str:
    return f"R$ {v / 1e9:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + " bilhões"


def _lista_incisos(trecho: str) -> list[str]:
    """'I - a; II - b; e III - c.' -> ['a', 'b', 'c']"""
    partes = re.split(r"\b[IVX]{1,5} - ", trecho)[1:]
    return [re.sub(r"(;\s*e|;|\.)$", "", re.sub(r"^e\s+", "", p.strip())).strip() for p in partes if p.strip()]


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def extrair_ppa(paginas: list[str]) -> dict[str, Any] | None:
    """Prioridades (art. 3º), agendas transversais (art. 4º) e anexos (art. 6º) do projeto do PPA."""
    texto = " ".join(paginas[:8])
    itens: list[dict[str, Any]] = []
    m = re.search(r"Art\. 3[º°o] S[ãa]o prioridades .*?:(.*?)Par[áa]grafo [úu]nico", texto)
    if m and (lista := _lista_incisos(m.group(1))):
        itens.append({"rotulo": "Prioridades do governo federal", "lista": [_cap(x) for x in lista], "artigo": "art. 3º"})
    m = re.search(r"Art\. 4[º°o] S[ãa]o agendas transversais .*?:(.*?)Par[áa]grafo [úu]nico", texto)
    if m and (lista := _lista_incisos(m.group(1))):
        itens.append({"rotulo": "Agendas transversais", "lista": [_cap(x) for x in lista], "artigo": "art. 4º",
                      "nota": "Temas que atravessam vários ministérios e são acompanhados em conjunto."})
    m = re.search(r"Art\. 6[º°o] Integram o PPA.*?:(.*?)§ 1", texto)
    if m:
        corpo = re.sub(r"\s\d{1,2}\s(?=[IVX]+ - Anexo)", " ", m.group(1))
        anexos = [re.sub(r"(;\s*e|;|\.)\s*(?:[IVX]+ - )?$", "", x.strip()) for x in re.split(r"(?:[IVX]+ - )?Anexo [IVX]+ - ", corpo)[1:]]
        if len(anexos) >= 4:
            itens.append({"rotulo": f"O que compõe o plano ({len(anexos)} anexos)", "lista": [_cap(a.strip()) for a in anexos], "artigo": "art. 6º"})
    if not itens:
        return None
    return {"itens": itens}


def extrair_ldo(paginas: list[str]) -> dict[str, Any] | None:
    """Meta de resultado primário e faixa de tolerância (art. 2º) do projeto da LDO."""
    texto = " ".join(paginas[:6])
    itens: list[dict[str, Any]] = []
    meta = re.search(r"meta de (super[áa]vit|d[ée]ficit) prim[áa]rio de R\$ ?([\d.]+,\d{2}).{0,300}?para o Governo Central", texto)
    sup = re.search(r"limite superior equivalente a (super[áa]vit|d[ée]ficit) prim[áa]rio de R\$ ?([\d.]+,\d{2})", texto)
    inf = re.search(r"limite inferior equivalente a (super[áa]vit|d[ée]ficit) prim[áa]rio de R\$ ?([\d.]+,\d{2})", texto)
    if meta:
        v = _num_extenso_br(meta.group(2))
        itens.append({"rotulo": "Meta de resultado primário", "valor": _bi(v),
                      "texto": f"Meta de {meta.group(1).lower().replace('superavit', 'superávit').replace('deficit', 'déficit')} para o Governo Central: "
                               "receitas menos despesas, sem contar os juros da dívida.", "artigo": "art. 2º", "bi": round(v / 1e9, 2)})
    if inf and sup:
        a, b = _num_extenso_br(inf.group(2)), _num_extenso_br(sup.group(2))
        itens.append({"rotulo": "Faixa de tolerância na execução", "valor": f"{_bi(a)} a {_bi(b)}",
                      "texto": "Resultado dentro dessa faixa conta como meta cumprida; passar do limite superior não é descumprimento.", "artigo": "art. 2º, § 1º e § 2º"})
    prec = re.search(r"equivalente a ([\d,]+)% .{0,160}?das despesas da Uni[ãa]o com precat[óo]rios", texto)
    if prec:
        itens.append({"rotulo": "Precatórios dentro da meta", "valor": f"{prec.group(1)}% do valor",
                      "texto": "Parte das despesas com precatórios e requisições de pequeno valor entra no cálculo da meta.", "artigo": "art. 2º, § 3º"})
    if not itens:
        return None
    return {"itens": itens}


EXTRATORES = {"PPA": extrair_ppa, "LDO": extrair_ldo}
AVISO = ("Pontos lidos do texto do projeto de lei como o Poder Executivo o enviou ao Congresso. O texto final pode ter sido alterado "
         "antes da sanção; confira no documento oficial.")


def _documento_do_ciclo(tipo: str) -> dict | None:
    """PPA em vigor ou LDO do próximo ano, entre os documentos da União."""
    ano = date.today().year
    docs = orcamento.ciclo(1).get("documentos", [])
    if tipo == "PPA":
        return next((d for d in docs if d["tipo"] == "PPA" and "-" in d["referencia"] and int(d["referencia"][:4]) <= ano <= int(d["referencia"][5:])), None)
    return next((d for d in docs if d["tipo"] == tipo and d["referencia"] == str(ano + 1)), None)


def pontos(tipo: str) -> dict[str, Any] | None:
    """Principais pontos do documento da lei (PPA ou LDO). None se o PDF não pôde ser lido."""
    tipo = tipo.upper()
    if tipo not in EXTRATORES:
        return None
    doc = _documento_do_ciclo(tipo)
    if not doc or not doc.get("url_texto"):
        return None
    arq = PASTA / f"pontos_{tipo}_{re.sub(r'\W+', '_', doc['proposicao'])}.json"
    try:
        if arq.exists():
            return json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass

    def produzir() -> dict | None:
        try:
            paginas = orcamento._paginas_pdf(doc["url_texto"], limite=8)
        except Exception as exc:  # noqa: BLE001 — sem o PDF, a tela mostra só o resumo geral
            log.warning("PDF de %s indisponível: %s", doc["proposicao"], exc)
            return None
        r = EXTRATORES[tipo](paginas)
        if not r:
            return None
        r.update({"tipo": tipo, "proposicao": doc["proposicao"], "url_texto": doc["url_texto"], "url_tramitacao": doc["url_tramitacao"],
                  "virou_lei": doc["virou_lei"], "aviso": AVISO})
        try:
            PASTA.mkdir(parents=True, exist_ok=True)
            arq.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return r

    return orcamento._memo(("pontos", tipo, doc["proposicao"]), produzir)
