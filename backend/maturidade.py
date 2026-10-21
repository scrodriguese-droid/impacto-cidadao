"""Maturidade das promessas dos planos de governo: régua objetiva, igual para todos os candidatos.

Não julga se a promessa é boa, justa ou viável: só confere se o texto traz os elementos que permitem
ao cidadão cobrar depois. Três critérios centrais — meta numérica, prazo e fonte de recursos — mais
dois de apoio (quem executa e indicador de acompanhamento).

Níveis (neutros, sem cor de aprovação/reprovação):
  estruturada: meta, prazo e fonte presentes;
  parcial:     um ou dois dos três;
  generica:    nenhum dos três (carta de intenções).

Sobre uma proposta já analisada, os critérios vêm dos campos verificados contra o PDF; sobre um
capítulo, de padrões de texto. Neste caso os sinais podem vir de frases diferentes do capítulo.
"""
from __future__ import annotations

import re

NIVEIS = {
    "estruturada": "Promessa estruturada",
    "parcial": "Promessa parcial",
    "generica": "Carta de intenções",
}
EXPLICACAO = ("Régua objetiva, igual para todos: confere se o texto traz meta numérica, prazo e fonte de recursos. "
              "Não avalia se a proposta é boa ou viável nem prevê se será cumprida.")

_META = re.compile(
    r"R\$\s?\d|\b\d[\d.,]*\s*(%|por cento|mil\b|milh|bilh|km\b|creches?|escolas?|vagas?|unidades?|casas?|"
    r"moradias?|leitos?|hospitais|empregos?|postos?|equipes?|municípios|policiais)", re.I)
_PRAZO = re.compile(
    r"\bat[eé]\s+(o\s+)?(ano\s+de\s+)?20\d\d|\bem\s+\d+\s+(anos?|meses|dias)|\bnos?\s+primeiros?\s+\d*\s*"
    r"(dias|meses|anos?)|\b(primeiro|1[ºo°])\s+ano\b|\bat[eé]\s+o\s+fim\s+do\s+(mandato|governo)|\b100\s+dias\b", re.I)
_FONTE = re.compile(
    r"financiad|financiamento|custeio|custead|fonte[s]?\s+de\s+recursos?|dota[cç][aã]o|"
    r"\bPPPs?\b|parcerias?\s+p[uú]blico|concess[oõ]es|concess[aã]o|compensa[dç]|"
    r"recursos?\s+(do|da|de|dos|das)\s+(fundo|or[cç]amento|tesouro|uni[aã]o|petr[oó]leo|royalties)|"
    r"receitas?\s+(de|do|da|dos|das)\s", re.I)
_EXECUTOR = re.compile(
    r"\bdecreto\b|medida provisória|projeto de lei|\bPEC\b|emenda constitucional|lei complementar|"
    r"minist[eé]rio|prefeituras?|governos? estaduais|estados e munic[ií]pios|consórcios?|repasse", re.I)
_INDICADOR = re.compile(r"indicadores?\b|monitoramento|painel (p[uú]blico|de)|presta[cç][aã]o de contas|"
                        r"metas? (anuais|de resultado)|avalia[cç][aã]o (de|dos) resultados", re.I)


def nivel(meta: bool, prazo: bool, fonte: bool) -> str:
    n = int(meta) + int(prazo) + int(fonte)
    return "estruturada" if n == 3 else "parcial" if n else "generica"


def _montar(meta: bool, prazo: bool, fonte: bool, executor: bool, indicador: bool) -> dict:
    n = nivel(meta, prazo, fonte)
    return {"nivel": n, "rotulo": NIVEIS[n],
            "criterios": {"meta_numerica": meta, "prazo": prazo, "fonte_de_recursos": fonte,
                          "quem_executa": executor, "indicador": indicador}}


def avaliar_proposta(p: dict) -> dict:
    """A partir dos campos já verificados da proposta (propostas_candidato)."""
    return _montar(bool(p["tem_meta_numerica"]), bool(p["tem_prazo"]), bool(p["tem_fonte_recurso"]),
                   p["canal_execucao"] != "nao_especificado", False)


def avaliar_texto(texto: str) -> dict:
    """A partir de padrões no texto (capítulo inteiro): sinais presentes, não necessariamente numa só promessa."""
    return _montar(bool(_META.search(texto)), bool(_PRAZO.search(texto)), bool(_FONTE.search(texto)),
                   bool(_EXECUTOR.search(texto)), bool(_INDICADOR.search(texto)))
