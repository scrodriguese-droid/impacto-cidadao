"""Capítulos de um plano de governo, sem IA.

Como funciona: lê o sumário do próprio PDF (já guardado página a página em `paginas_plano`),
localiza cada título no corpo do texto e, com isso, descobre as páginas de cada capítulo. Para cada
capítulo mostra (a) a abertura literal do texto e (b) os temas do site que ele toca, pelas mesmas
palavras-chave da busca (backend/temas.py), iguais para todos os candidatos. Se houver análise por IA
do plano, soma também quantas propostas dela caem naquelas páginas.

Nada aqui interpreta o conteúdo: é estrutura do documento + busca literal.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

import db
import maturidade
from temas import TERMOS_POR_TEMA, consulta_tsquery

MIN_CAPITULOS = 3          # abaixo disso o sumário não foi entendido: melhor não mostrar nada
PAGINAS_PROCURA_SUMARIO = 10
TAM_ABERTURA = 320
_PONTILHADO = re.compile(r"\.{4,}")
_FIM_DE_ENTRADA = re.compile(r"\.{3,}\s*\d{1,3}\s*$")
_NUM_FINAL = re.compile(r"[\s.…]+\d{1,3}\s*$")


def _norm(texto: str) -> str:
    """MAIÚSCULAS, sem acento e sem pontuação: para comparar títulos do sumário com o corpo."""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", " ", t.upper()).strip()


def _linhas(texto: str) -> list[str]:
    return [re.sub(r"[\s ]+", " ", l).strip() for l in texto.replace("\t", " ").splitlines() if l.strip()]


def _paginas_do_sumario(paginas: list[dict]) -> list[int]:
    """Índices (posição na lista) das páginas de sumário: a que diz 'Sumário' + as seguintes que seguem o padrão."""
    ini = None
    for i, p in enumerate(paginas[:PAGINAS_PROCURA_SUMARIO]):
        if p["indice"] or re.search(r"\b(SUMARIO|INDICE)\b", _norm(p["texto"][:300])):
            ini = i
            break
    if ini is None:
        return []
    achadas = [ini]
    for i in range(ini + 1, min(ini + 4, len(paginas))):
        t = paginas[i]["texto"]
        if paginas[i]["indice"] or len(_PONTILHADO.findall(t)) >= 5:
            achadas.append(i)
        else:
            break
    return achadas


def _entradas(paginas: list[dict], idx_sumario: list[int]) -> list[str]:
    """Títulos do sumário, já sem pontilhado nem número de página (junta títulos quebrados em duas linhas)."""
    linhas = [l for i in idx_sumario for l in _linhas(paginas[i]["texto"])
              if _norm(l) not in ("SUMARIO", "INDICE")]  # o rótulo do sumário não é uma entrada
    com_pontos = sum(1 for l in linhas if _PONTILHADO.search(l))
    titulos: list[str] = []
    if com_pontos >= 4:  # sumário com pontilhado: a entrada termina na linha "... 12"
        acum: list[str] = []
        for l in linhas:
            acum.append(l)
            if _FIM_DE_ENTRADA.search(l) or (_PONTILHADO.search(l) and _NUM_FINAL.search(l)):
                titulos.append(" ".join(acum))
                acum = []
        # sobra sem número de página é cabeçalho/rodapé: descarta
    else:  # sumário só com títulos: uma entrada por linha
        titulos = linhas
    limpos = []
    for t in titulos:
        t = _PONTILHADO.split(t)[0] if _PONTILHADO.search(t) else _NUM_FINAL.sub("", t)
        t = re.sub(r"\s+", " ", t).strip(" .-–—")
        n = _norm(t)
        if 4 <= len(n) <= 140 and n not in ("SUMARIO", "INDICE") and not re.fullmatch(r"\d+", n):
            limpos.append(t)
    return limpos


def _localizar(paginas: list[dict], titulos: list[str], apos: int) -> list[tuple[str, int]]:
    """Posição (índice de página) de cada título no corpo, em ordem; o que não for achado é ignorado."""
    normas = [_norm(p["texto"]) for p in paginas]
    achados: list[tuple[str, int]] = []
    atual = apos
    for t in titulos:
        alvo = _norm(t)
        if not alvo:
            continue
        for i in range(atual, len(paginas)):
            if alvo in normas[i]:
                achados.append((t, i))
                atual = i
                break
    return achados


def _abertura(texto: str, titulo: str) -> str:
    """Primeiros caracteres do texto logo depois do título, literais (sem reescrever)."""
    flat = re.sub(r"\s+", " ", texto)
    m = re.search(r"\s*".join(re.escape(w) for w in titulo.split()), flat, re.I)
    resto = flat[m.end():] if m else flat
    resto = resto.strip(" .:-–—0123456789")
    if len(resto) <= TAM_ABERTURA:
        return resto
    corte = resto[:TAM_ABERTURA]
    return corte[: corte.rfind(" ")] + "…" if " " in corte else corte + "…"


def capitulos_do_plano(plano_id: int) -> dict[str, Any]:
    paginas = db.consultar(
        "SELECT pagina, texto, indice FROM paginas_plano WHERE plano_id = %s ORDER BY pagina", (plano_id,))
    if not paginas:
        return {"capitulos": [], "motivo": "texto_indisponivel"}
    idx_sumario = _paginas_do_sumario(paginas)
    if not idx_sumario:
        return {"capitulos": [], "motivo": "sem_sumario"}
    achados = _localizar(paginas, _entradas(paginas, idx_sumario), idx_sumario[-1] + 1)
    if len(achados) < MIN_CAPITULOS:
        return {"capitulos": [], "motivo": "sumario_nao_entendido"}

    # Páginas (número) em que cada tema aparece, pelas palavras-chave da busca.
    por_tema: dict[str, set[int]] = {}
    for tema in TERMOS_POR_TEMA:
        linhas = db.consultar(
            "SELECT g.pagina FROM paginas_plano g WHERE g.plano_id = %(p)s AND NOT g.indice "
            "AND g.busca @@ websearch_to_tsquery('pt_busca', %(q)s)",
            {"p": plano_id, "q": consulta_tsquery(tema)})
        por_tema[tema] = {r["pagina"] for r in linhas}
    nomes = {r["id"]: r["nome"] for r in db.consultar("SELECT id, nome FROM categorias")}
    ia = db.consultar(
        "SELECT categoria_id, pagina FROM propostas_candidato WHERE plano_id = %s AND pagina IS NOT NULL",
        (plano_id,))

    capitulos = []
    for k, (titulo, i) in enumerate(achados):
        prox = achados[k + 1][1] if k + 1 < len(achados) else len(paginas)
        fim = max(i, prox - 1) if prox > i else i
        ini_pg, fim_pg = paginas[i]["pagina"], paginas[min(fim, len(paginas) - 1)]["pagina"]
        faixa = range(ini_pg, fim_pg + 1)
        temas = []
        compartilha = prox == i  # o próximo título começa na mesma página: não dá para separar os temas
        for tema, pgs in ({} if compartilha else por_tema).items():
            n = sum(1 for p in faixa if p in pgs)
            if n and n >= 0.2 * len(faixa):
                temas.append({"categoria": tema, "nome": nomes.get(tema, tema), "paginas_com_o_tema": n,
                              "propostas_analisadas": sum(1 for r in ia if r["categoria_id"] == tema
                                                          and ini_pg <= r["pagina"] <= fim_pg)})
        temas.sort(key=lambda t: (-t["paginas_com_o_tema"], t["nome"]))
        capitulos.append({"ordem": k + 1, "titulo": titulo, "pagina_inicial": ini_pg, "pagina_final": fim_pg,
                          "abertura": _abertura(paginas[i]["texto"], titulo),
                          "pagina_compartilhada": compartilha, "temas": temas[:5],
                          # páginas que dividem título com outro capítulo não permitem separar os sinais
                          "maturidade": None if compartilha else maturidade.avaliar_texto(
                              "\n".join(p["texto"] for p in paginas[i:fim + 1]))})
    return {"capitulos": capitulos, "motivo": None}
