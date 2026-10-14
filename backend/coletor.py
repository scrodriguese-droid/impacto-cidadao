"""Coletor e tradutor de proposições do Poder Executivo / alto impacto.

Pipeline:  Câmara (e Senado)  ->  filtro  ->  texto oficial  ->  IA  ->  PostgreSQL

Exemplos (a partir da raiz do projeto):
    python backend/coletor.py                         # últimos 60 dias, até 25 análises
    python backend/coletor.py --dias 15 --limite 5    # teste barato
    python backend/coletor.py --sem-ia                # só coleta, sem chamar a IA
    python backend/coletor.py --id 2647155 --forcar   # reanalisa uma proposição da Câmara

Regras de filtro:
  * Medidas Provisórias (MPV) são sempre do Executivo;
  * PL / PLP / PEC / PLN só entram se um dos autores for o Poder Executivo/Presidência;
  * "alto impacto" (opcional): propostas do Congresso cuja ementa menciona vários
    termos de bolso (imposto, aposentadoria, FGTS, salário mínimo...), limitado por
    --max-alto-impacto;
  * Créditos extraordinários ("Abre crédito...") ficam de fora, salvo --incluir-creditos.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

import db
from config import cfg
from fontes import Camara, ErroFonte, Senado, baixar_documento
from llm import ErroConfiguracaoLLM, ErroLLM, RecusaLLM, analisar, modelo_em_uso
from prompts import PROMPT_PROPOSICAO, montar_usuario_proposicao
from schemas import AnaliseProposicao
from temas import TERMOS_POR_TEMA, consulta_tsquery

log = logging.getLogger("impacto.coletor")

TIPOS_PADRAO = ("MPV", "PLP", "PEC", "PLN", "PL")
TIPOS_QUE_PODEM_SER_ALTO_IMPACTO = {"PL", "PLP", "PEC"}

_ALTO_IMPACTO = re.compile(
    r"imposto|tribut|isen[cç][aã]o|imposto de renda|sal[aá]rio[- ]m[ií]nimo|aposentad|previd[eê]nc|"
    r"\binss\b|\bfgts\b|benef[ií]cio|bolsa fam[ií]lia|simples nacional|microempreendedor|\bmei\b|"
    r"reajuste|piso salarial|trabalhist|plano de sa[uú]de|financiamento estudantil|\bfies\b|"
    r"tarifa|combust[ií]ve|cr[eé]dito consignado|pens[aã]o|seguro[- ]desemprego|abono",
    re.IGNORECASE,
)
_CREDITO = re.compile(r"^\s*abre\s+(ao\s+or[cç]amento|cr[eé]dito)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Modelo interno
# --------------------------------------------------------------------------- #
@dataclass
class Item:
    fonte: str                      # camara | senado
    id_externo: str
    sigla_tipo: str
    numero: int | None
    ano: int | None
    ementa: str
    origem: str                     # executivo | alto_impacto
    data_apresentacao: date | None = None
    autoria: str = ""               # ex.: "Poder Executivo", nomes de parlamentares
    nivel: str = "federal"          # federal | estadual | municipal (hoje só há fontes federais)
    url_fonte: str = ""
    url_inteiro_teor: str = ""
    situacao: str = ""
    descricao_tipo: str = ""
    tramitacoes: list[dict] = field(default_factory=list)
    texto: str | None = None
    texto_fonte: str = "ementa"     # inteiro_teor | ementa

    @property
    def identificacao(self) -> str:
        if self.numero and self.ano:
            return f"{self.sigla_tipo} {self.numero}/{self.ano}"
        return f"{self.sigla_tipo} {self.id_externo}"

    @property
    def instancia(self) -> str:
        """Poder de origem: o que nasce no Executivo é do Executivo; propostas do Congresso, do Legislativo.
        (Judiciário — ex.: ADPF/ADI do STF — entrará quando houver fonte conectada.)"""
        return "executivo" if self.origem == "executivo" else "legislativo"


@dataclass
class Placar:
    novos: int = 0
    atualizados: int = 0
    pulados: int = 0
    falhas: int = 0
    pendentes: int = 0


# --------------------------------------------------------------------------- #
# Regras determinísticas (status e linha do tempo NÃO dependem da IA)
# --------------------------------------------------------------------------- #
def inferir_status(sigla_tipo: str, situacao: str, tramitacoes: list[dict]) -> str | None:
    """Mapeia a situação oficial para o vocabulário do app; None se não reconhecer."""
    s = (situacao or "").lower()
    recentes = " ".join(
        f"{t.get('descricaoTramitacao') or ''} {t.get('despacho') or ''}" for t in tramitacoes[-4:]
    ).lower()

    if re.search(r"perda de efic[aá]cia|perdeu a efic[aá]cia|sem efic[aá]cia|caducou", s):
        return "Perdeu a eficácia"
    if "transformad" in s and "norma" in s or "transformado em lei" in s:
        return "Sancionada com vetos" if "veto" in recentes else "Sancionada (em vigor)"
    if re.search(r"arquivad|rejeitad|retirad[ao] pel|prejudicad", s):
        return "Arquivada ou rejeitada"
    if re.search(r"aguardando san[cç][aã]o|remetid[ao] [àa] san[cç][aã]o|enviad[ao] [àa] san[cç][aã]o", s + " " + recentes):
        return "Aprovada pelo Congresso"
    if sigla_tipo == "MPV" and not s:
        return "Em vigor (MP aguardando votação)"
    return None


def montar_linha_do_tempo(tramitacoes: list[dict], limite: int = 8) -> list[dict]:
    """Últimos eventos REAIS de tramitação (nada de etapas futuras inventadas)."""
    eventos: list[dict] = []
    for t in tramitacoes:
        evento = {
            "data": (t.get("dataHora") or "")[:10],
            "orgao": t.get("siglaOrgao") or "",
            "etapa": (t.get("descricaoTramitacao") or "Andamento").strip(),
            "detalhe": re.sub(r"\s+", " ", (t.get("despacho") or "")).strip()[:300],
        }
        if eventos and (eventos[-1]["data"], eventos[-1]["etapa"], eventos[-1]["detalhe"]) == (
            evento["data"], evento["etapa"], evento["detalhe"]
        ):
            continue
        eventos.append(evento)
    ultimos = eventos[-limite:]
    for i, e in enumerate(ultimos):
        e["status"] = "current" if i == len(ultimos) - 1 else "completed"
    return ultimos


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _data(valor: str | None) -> date | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor[:10]).date()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Descoberta de candidatos
# --------------------------------------------------------------------------- #
def descobrir_camara(camara: Camara, tipos: list[str], desde: date, args: argparse.Namespace,
                     conhecidos: set[tuple[str, str]]) -> list[Item]:
    achados: list[Item] = []
    alto_impacto = 0
    anos = range(desde.year, date.today().year + 1)

    for tipo in tipos:
        for ano in anos:
            try:
                resultados = list(camara.listar(tipo, ano, desde=desde, max_itens=args.max_por_tipo))
            except ErroFonte as exc:
                log.warning("Câmara: falha ao listar %s/%s: %s", tipo, ano, exc)
                continue
            log.info("Câmara: %d %s apresentadas em %d desde %s", len(resultados), tipo, ano, desde)

            for r in resultados:
                id_ext = str(r["id"])
                ementa = (r.get("ementa") or "").strip()
                if not args.incluir_creditos and _CREDITO.search(ementa):
                    continue

                origem = "executivo"
                autoria = "Poder Executivo (Presidência da República)"  # MPV é sempre do Executivo
                if tipo != "MPV":
                    try:
                        autores = camara.autores(id_ext)
                    except ErroFonte as exc:
                        log.warning("Câmara: autores de %s indisponíveis: %s", id_ext, exc)
                        continue
                    nomes = [a.get("nome") for a in autores if a.get("nome")]
                    autoria = ", ".join(nomes[:3]) + (f" e outros {len(nomes) - 3}" if len(nomes) > 3 else "")
                    if not Camara.autoria_executivo(autores):
                        score = len(set(m.lower() for m in _ALTO_IMPACTO.findall(ementa)))
                        if ("camara", id_ext) in conhecidos:
                            origem = "alto_impacto"          # já aceita antes: mantém
                        elif (tipo in TIPOS_QUE_PODEM_SER_ALTO_IMPACTO and score >= 2
                                and alto_impacto < args.max_alto_impacto):
                            origem = "alto_impacto"
                            alto_impacto += 1
                        else:
                            continue
                achados.append(Item(
                    fonte="camara", id_externo=id_ext, sigla_tipo=r.get("siglaTipo", tipo),
                    numero=r.get("numero"), ano=r.get("ano"), ementa=ementa, origem=origem,
                    data_apresentacao=_data(r.get("dataApresentacao")), autoria=autoria,
                ))
    return achados


def descobrir_senado(senado: Senado, tipos: list[str], desde: date, args: argparse.Namespace,
                     identificacoes_existentes: set[str]) -> list[Item]:
    achados: list[Item] = []
    for tipo in tipos:
        for ano in range(desde.year, date.today().year + 1):
            try:
                processos = senado.listar_processos(tipo, ano)
            except ErroFonte as exc:
                log.warning("Senado: falha ao listar %s/%s: %s", tipo, ano, exc)
                continue
            for p in processos:
                ident = (p.get("identificacao") or "").strip()
                apresentada = _data(p.get("dataApresentacao"))
                ementa = (p.get("ementa") or "").strip()
                if not ident or ident in identificacoes_existentes or not Senado.autoria_executivo(p):
                    continue
                if apresentada and apresentada < desde:
                    continue
                if not args.incluir_creditos and _CREDITO.search(ementa):
                    continue
                m = re.match(r"([A-Z]+)\s+(\d+)/(\d{4})", ident)
                achados.append(Item(
                    fonte="senado", id_externo=str(p.get("id")),
                    sigla_tipo=m.group(1) if m else tipo,
                    numero=int(m.group(2)) if m else None, ano=int(m.group(3)) if m else ano,
                    ementa=ementa, origem="executivo", data_apresentacao=apresentada,
                    autoria=(p.get("autoria") or "Poder Executivo"),
                    url_fonte=Senado.url_pagina(p), url_inteiro_teor=p.get("urlDocumento") or "",
                    situacao=p.get("situacaoAtual") or "", descricao_tipo=p.get("tipoDocumento") or "",
                ))
    return achados


# --------------------------------------------------------------------------- #
# Enriquecimento (detalhe, tramitação, texto)
# --------------------------------------------------------------------------- #
def enriquecer(item: Item, camara: Camara, *, baixar_texto: bool) -> None:
    if item.fonte == "camara":
        det = camara.detalhe(item.id_externo)
        status = det.get("statusProposicao") or {}
        item.ementa = (det.get("ementa") or item.ementa or "").strip()
        item.descricao_tipo = det.get("descricaoTipo") or item.sigla_tipo
        item.situacao = status.get("descricaoSituacao") or status.get("descricaoTramitacao") or ""
        item.url_inteiro_teor = det.get("urlInteiroTeor") or status.get("url") or ""
        item.url_fonte = f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={item.id_externo}"
        item.data_apresentacao = item.data_apresentacao or _data(det.get("dataApresentacao"))
        item.tramitacoes = camara.tramitacoes(item.id_externo)
        ementa_detalhada = (det.get("ementaDetalhada") or "").strip()
    else:
        ementa_detalhada = ""

    if baixar_texto:
        texto = baixar_documento(camara.http, item.url_inteiro_teor) if item.url_inteiro_teor else None
        if texto and len(texto) >= 300:
            item.texto, item.texto_fonte = texto, "inteiro_teor"
            return
    base = item.ementa + (f"\n\nEmenta detalhada: {ementa_detalhada}" if ementa_detalhada else "")
    item.texto, item.texto_fonte = base, "ementa"


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #
def carregar_existentes() -> tuple[set[tuple[str, str]], set[str]]:
    with db.conexao_avulsa() as conn:
        linhas = conn.execute("SELECT fonte, id_externo, identificacao FROM proposicoes_analisadas").fetchall()
    return ({(r["fonte"], r["id_externo"]) for r in linhas}, {r["identificacao"] for r in linhas})


def gravar(item: Item, *, analise: AnaliseProposicao | None, status: str, erro: str | None,
           texto_hash: str | None, modelo: str | None) -> None:
    status_det = inferir_status(item.sigla_tipo, item.situacao, item.tramitacoes)
    status_final = status_det or (analise.status_tramitacao if analise else None)
    dados: dict[str, Any] = {
        "fonte": item.fonte, "id_externo": item.id_externo, "sigla_tipo": item.sigla_tipo,
        "numero": item.numero, "ano": item.ano, "identificacao": item.identificacao,
        "ementa_oficial": item.ementa, "origem": item.origem, "instancia": item.instancia,
        "nivel": item.nivel, "autoria": item.autoria or None, "url_fonte": item.url_fonte or None,
        "url_inteiro_teor": item.url_inteiro_teor or None, "data_apresentacao": item.data_apresentacao,
        "situacao_atual": item.situacao or None, "linha_do_tempo": Jsonb(montar_linha_do_tempo(item.tramitacoes)),
        "titulo_simples": analise.titulo_simples if analise else None,
        "resumo_cidadao": analise.resumo_cidadao if analise else None,
        "perfis_impactados": list(analise.perfis_impactados) if analise else [],
        "categorias": list(analise.categorias) if analise else [],
        "impacto_pratico": analise.impacto_pratico_bolso_rotina if analise else None,
        "ponto_positivo": analise.ponto_positivo if analise else None,
        "ponto_atencao": analise.ponto_atencao if analise else None,
        "status_tramitacao": status_final,
        "relevancia_bolso": analise.relevancia_bolso if analise else None,
        "analise_bruta": Jsonb(analise.model_dump()) if analise else None,
        "analise_status": status, "analise_erro": erro,
        "texto_fonte": item.texto_fonte, "texto_hash": texto_hash, "modelo_llm": modelo,
        "analisado_em": datetime.now().astimezone() if analise else None,
    }
    colunas = ", ".join(dados)
    marcadores = ", ".join(f"%({c})s" for c in dados)
    # origem/instância/nível são decididos na primeira coleta (reprocessar não reclassifica);
    # a autoria só é sobrescrita quando a nova coleta traz um valor.
    fixos = {"fonte", "id_externo", "origem", "instancia", "nivel"}
    atualizar = ", ".join(
        "autoria = COALESCE(EXCLUDED.autoria, proposicoes_analisadas.autoria)" if c == "autoria"
        else f"{c} = EXCLUDED.{c}"
        for c in dados if c not in fixos
    )
    with db.conexao_avulsa() as conn:
        conn.execute(
            f"INSERT INTO proposicoes_analisadas ({colunas}) VALUES ({marcadores}) "
            f"ON CONFLICT (fonte, id_externo) DO UPDATE SET {atualizar}",
            dados,
        )


def reclassificar_temas() -> int:
    """Recalcula o tema aproximado de TODOS os atos pelas palavras-chave da ementa (ver temas.py).

    Idempotente e barato (centenas de linhas). O primeiro tema da lista é o de maior relevância
    textual. Quem decide o tema exibido é a API: áreas da IA, se houver; senão, estas.
    """
    temas = list(TERMOS_POR_TEMA)
    consultas = [consulta_tsquery(t) for t in temas]
    with db.conexao_avulsa() as conn:
        conn.execute("UPDATE proposicoes_analisadas SET temas_palavras_chave = '{}' "
                     "WHERE temas_palavras_chave <> '{}'")
        cur = conn.execute(
            """
            UPDATE proposicoes_analisadas p SET temas_palavras_chave = s.temas
              FROM (
                SELECT p2.id,
                       array_agg(t.tema ORDER BY
                           ts_rank(to_tsvector('pt_busca', p2.ementa_oficial), websearch_to_tsquery('pt_busca', t.q)) DESC,
                           t.ordem) AS temas
                  FROM proposicoes_analisadas p2
                 CROSS JOIN unnest(%(temas)s::text[], %(consultas)s::text[]) WITH ORDINALITY AS t(tema, q, ordem)
                 WHERE to_tsvector('pt_busca', p2.ementa_oficial) @@ websearch_to_tsquery('pt_busca', t.q)
                 GROUP BY p2.id
              ) s
             WHERE p.id = s.id
            """,
            {"temas": temas, "consultas": consultas},
        )
        return cur.rowcount


def atualizar_andamento(item: Item) -> None:
    """Atualiza só situação/linha do tempo/status de uma proposição já analisada (sem IA)."""
    status_det = inferir_status(item.sigla_tipo, item.situacao, item.tramitacoes)
    with db.conexao_avulsa() as conn:
        conn.execute(
            "UPDATE proposicoes_analisadas SET situacao_atual = %s, linha_do_tempo = %s, "
            "status_tramitacao = COALESCE(%s, status_tramitacao) WHERE fonte = %s AND id_externo = %s",
            (item.situacao or None, Jsonb(montar_linha_do_tempo(item.tramitacoes)), status_det,
             item.fonte, item.id_externo),
        )


def estado_atual(item: Item) -> dict | None:
    with db.conexao_avulsa() as conn:
        return conn.execute(
            "SELECT analise_status, texto_hash FROM proposicoes_analisadas WHERE fonte = %s AND id_externo = %s",
            (item.fonte, item.id_externo),
        ).fetchone()


# --------------------------------------------------------------------------- #
# Processamento de um item
# --------------------------------------------------------------------------- #
def processar(item: Item, camara: Camara, args: argparse.Namespace, placar: Placar) -> None:
    anterior = estado_atual(item)
    ja_ok = bool(anterior and anterior["analise_status"] == "ok")

    try:
        enriquecer(item, camara, baixar_texto=not (ja_ok and not args.forcar))
    except ErroFonte as exc:
        log.warning("%s: não foi possível obter detalhes (%s)", item.identificacao, exc)
        placar.falhas += 1
        return

    if ja_ok and not args.forcar:
        atualizar_andamento(item)
        placar.pulados += 1
        log.info("= %s já analisada; andamento atualizado.", item.identificacao)
        return

    texto = item.texto or item.ementa
    texto_hash = _hash(texto)

    if not args.sem_ia and len(texto) > cfg.max_chars_texto:
        gravar(item, analise=None, status="texto_longo",
               erro=f"Texto com {len(texto):,} caracteres excede MAX_CHARS_TEXTO ({cfg.max_chars_texto:,}).",
               texto_hash=texto_hash, modelo=None)
        placar.pendentes += 1
        log.warning("! %s: texto muito longo (%s caracteres); análise adiada (aumente MAX_CHARS_TEXTO).",
                    item.identificacao, f"{len(texto):,}")
        return

    if args.sem_ia:
        gravar(item, analise=None, status="pendente", erro=None, texto_hash=texto_hash, modelo=None)
        placar.pendentes += 1
        log.info("+ %s coletada (sem IA).", item.identificacao)
        return

    meta = {
        "identificacao": item.identificacao, "descricao_tipo": item.descricao_tipo,
        "data_apresentacao": item.data_apresentacao.isoformat() if item.data_apresentacao else "",
        "ementa": item.ementa, "situacao_atual": item.situacao,
        "origem_descricao": ("Poder Executivo (Presidência da República)" if item.origem == "executivo"
                             else "Congresso Nacional (selecionada por alto impacto no bolso)"),
        "ultimas_tramitacoes": [
            f"{(t.get('dataHora') or '')[:10]} — {t.get('descricaoTramitacao') or ''}".strip(" —")
            for t in item.tramitacoes[-5:]
        ],
    }
    try:
        analise, info = analisar(
            PROMPT_PROPOSICAO,
            montar_usuario_proposicao(meta, texto, texto_eh_ementa=item.texto_fonte == "ementa"),
            AnaliseProposicao,
        )
    except ErroConfiguracaoLLM:
        raise
    except (RecusaLLM, ErroLLM) as exc:
        gravar(item, analise=None, status="falha", erro=str(exc)[:500], texto_hash=texto_hash, modelo=modelo_em_uso())
        placar.falhas += 1
        log.error("x %s: %s", item.identificacao, exc)
        return

    gravar(item, analise=analise, status="ok", erro=None, texto_hash=texto_hash, modelo=info.get("modelo"))
    if anterior:
        placar.atualizados += 1
    else:
        placar.novos += 1
    log.info("+ %s — %s  [%s tokens entrada / %s saída]", item.identificacao, analise.titulo_simples,
             info.get("tokens_entrada"), info.get("tokens_saida"))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def interpretar_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Coleta e traduz proposições do Executivo para linguagem cidadã.")
    p.add_argument("--dias", type=int, default=60, help="janela de apresentação, em dias (padrão: 60)")
    p.add_argument("--tipos", default=",".join(TIPOS_PADRAO), help=f"siglas separadas por vírgula (padrão: {','.join(TIPOS_PADRAO)})")
    p.add_argument("--limite", type=int, default=25, help="máximo de proposições NOVAS a processar por execução (padrão: 25)")
    p.add_argument("--max-por-tipo", type=int, default=300, help="máximo de itens listados por tipo/ano (padrão: 300)")
    p.add_argument("--max-alto-impacto", type=int, default=10, help="máx. de propostas do Congresso incluídas por 'alto impacto' (0 desliga)")
    p.add_argument("--incluir-creditos", action="store_true", help="inclui créditos extraordinários/orçamentários")
    p.add_argument("--sem-senado", action="store_true", help="não consulta o Senado Federal")
    p.add_argument("--sem-ia", action="store_true", help="apenas coleta; deixa a análise como 'pendente'")
    p.add_argument("--forcar", action="store_true", help="reanalisa mesmo as já analisadas")
    p.add_argument("--id", help="processa apenas esta proposição da Câmara (id numérico)")
    p.add_argument("--so-temas", action="store_true",
                   help="não coleta nada: apenas recalcula o tema aproximado (palavras-chave) dos atos no banco")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = interpretar_argumentos(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.so_temas:
        n = reclassificar_temas()
        log.info("Temas por palavras-chave recalculados: %d ato(s) com ao menos um tema.", n)
        return 0

    desde = date.today() - timedelta(days=args.dias)
    tipos = [t.strip().upper() for t in args.tipos.split(",") if t.strip()]
    camara, senado, placar = Camara(), Senado(), Placar()

    try:
        conhecidos, identificacoes = carregar_existentes()
    except Exception as exc:  # noqa: BLE001
        log.error("Não consegui acessar o banco (%s). O PostgreSQL está no ar e o schema.sql foi aplicado?", exc)
        return 1

    if args.id:
        det = camara.detalhe(args.id)
        itens = [Item(fonte="camara", id_externo=str(args.id), sigla_tipo=det.get("siglaTipo", "?"),
                      numero=det.get("numero"), ano=det.get("ano"), ementa=det.get("ementa", ""),
                      origem="executivo", data_apresentacao=_data(det.get("dataApresentacao")))]
    else:
        itens = descobrir_camara(camara, tipos, desde, args, conhecidos)
        if not args.sem_senado:
            itens += descobrir_senado(senado, tipos, desde, args, identificacoes | {i.identificacao for i in itens})

    # Novas primeiro (são as que gastam IA); as já conhecidas só atualizam andamento.
    novas = [i for i in itens if (i.fonte, i.id_externo) not in conhecidos]
    conhecidas = [i for i in itens if (i.fonte, i.id_externo) in conhecidos]
    log.info("Encontradas %d proposições (%d novas, %d já conhecidas).", len(itens), len(novas), len(conhecidas))
    if len(novas) > args.limite:
        log.info("Limitando a %d novas nesta execução (--limite); o restante entra na próxima.", args.limite)
        novas = novas[: args.limite]

    try:
        for item in novas + conhecidas:
            processar(item, camara, args, placar)
    except ErroConfiguracaoLLM as exc:
        log.error("Configuração da IA inválida: %s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrompido pelo usuário.")

    try:
        reclassificar_temas()
    except Exception as exc:  # noqa: BLE001 - a classificação aproximada nunca deve derrubar a coleta
        log.warning("Não foi possível recalcular os temas por palavras-chave: %s", exc)

    log.info("Resumo: %d novas | %d atualizadas | %d só andamento | %d pendentes | %d falhas",
             placar.novos, placar.atualizados, placar.pulados, placar.pendentes, placar.falhas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
