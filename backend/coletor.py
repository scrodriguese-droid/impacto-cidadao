"""Coletor e tradutor de proposições do Poder Executivo / alto impacto.

Pipeline:  Câmara (e Senado)  ->  filtro  ->  texto oficial  ->  IA  ->  PostgreSQL

Exemplos (a partir da raiz do projeto):
    python backend/coletor.py                         # últimos 60 dias; IA só se houver chave
    python backend/coletor.py --dias 180              # janela maior (carga histórica)
    python backend/coletor.py --dias 15 --limite 5    # teste barato (no máx. 5 traduções)
    python backend/coletor.py --ia nao                # só coleta, sem chamar a IA
    python backend/coletor.py --id 2647155 --forcar   # reanalisa uma proposição da Câmara

Regras de filtro:
  * Medidas Provisórias (MPV) são sempre do Executivo;
  * PL / PLP / PEC / PLN só entram se um dos autores for o Poder Executivo/Presidência;
  * "alto impacto" (opcional): propostas do Congresso cuja ementa menciona vários
    termos de bolso (imposto, aposentadoria, FGTS, salário mínimo...), limitado por
    --max-alto-impacto;
  * Créditos extraordinários ("Abre crédito...") ficam de fora, salvo --incluir-creditos.

Comportamento incremental (a coleta pode rodar todo dia):
  * a listagem percorre TODAS as páginas da janela (sem teto por tipo);
  * atos já examinados e descartados não voltam a gastar chamadas (tabela proposicoes_descartadas);
  * atos já conhecidos e AINDA EM TRAMITAÇÃO são atualizados mesmo fora da janela de dias;
  * --limite vale só para TRADUÇÕES por IA; o que passar do limite é coletado sem tradução
    e entra na fila da próxima rodada;
  * cada execução registra uma linha em `coletas` (a tela mostra "dados atualizados em…").
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
from llm import ErroConfiguracaoLLM, ErroLLM, RecusaLLM, analisar, credenciais_configuradas, modelo_em_uso
from prompts import PROMPT_PROPOSICAO, montar_usuario_proposicao
from schemas import AnaliseProposicao
from temas import TERMOS_POR_TEMA, consulta_tsquery

log = logging.getLogger("impacto.coletor")

TIPOS_PADRAO = ("MPV", "PLP", "PEC", "PLN", "PL")
TIPOS_QUE_PODEM_SER_ALTO_IMPACTO = {"PL", "PLP", "PEC"}
# Situações em que o ato não muda mais: só os demais são reexaminados fora da janela de dias.
STATUS_FINAIS = ("Sancionada (em vigor)", "Sancionada com vetos", "Perdeu a eficácia", "Arquivada ou rejeitada")
TETO_SEGURANCA_POR_TIPO = 50_000  # só para nunca entrar em laço infinito se a API paginar mal

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
                     conhecidos: dict[tuple[str, str], dict], descartadas: set[tuple[str, str]],
                     ) -> tuple[list[Item], list[tuple[str, str]]]:
    """Percorre TODA a janela. Devolve (atos aceitos, [(id, motivo)] a lembrar como descartados)."""
    achados: list[Item] = []
    a_descartar: list[tuple[str, str]] = []
    # A cota de "alto impacto" é dividida entre os tipos elegíveis; sem isso, o primeiro tipo listado
    # (PLP) ficaria com todas as vagas e a amostra do Legislativo sairia enviesada.
    cota_por_tipo = -(-args.max_alto_impacto // len(TIPOS_QUE_PODEM_SER_ALTO_IMPACTO))  # teto da divisão
    alto_por_tipo: dict[str, int] = {}
    anos = range(desde.year, date.today().year + 1)
    teto = args.max_por_tipo or TETO_SEGURANCA_POR_TIPO

    for tipo in tipos:
        for ano in anos:
            try:
                resultados = list(camara.listar(tipo, ano, desde=desde, max_itens=teto))
            except ErroFonte as exc:
                log.warning("Câmara: falha ao listar %s/%s: %s", tipo, ano, exc)
                continue
            log.info("Câmara: %d %s apresentadas em %d desde %s", len(resultados), tipo, ano, desde)

            for r in resultados:
                id_ext = str(r["id"])
                chave = ("camara", id_ext)
                if chave in descartadas:
                    continue
                ementa = (r.get("ementa") or "").strip()
                if not args.incluir_creditos and _CREDITO.search(ementa):
                    continue

                origem = "executivo"
                autoria = "Poder Executivo (Presidência da República)"  # MPV é sempre do Executivo
                if tipo != "MPV":
                    conhecido = conhecidos.get(chave)
                    if conhecido and conhecido.get("autoria"):
                        origem, autoria = conhecido["origem"], conhecido["autoria"]  # sem nova chamada à API
                    else:
                        try:
                            autores = camara.autores(id_ext)
                        except ErroFonte as exc:
                            log.warning("Câmara: autores de %s indisponíveis: %s", id_ext, exc)
                            continue
                        nomes = [a.get("nome") for a in autores if a.get("nome")]
                        autoria = ", ".join(nomes[:3]) + (f" e outros {len(nomes) - 3}" if len(nomes) > 3 else "")
                        if not Camara.autoria_executivo(autores):
                            score = len(set(m.lower() for m in _ALTO_IMPACTO.findall(ementa)))
                            elegivel = tipo in TIPOS_QUE_PODEM_SER_ALTO_IMPACTO and score >= 2
                            if conhecido:
                                origem = "alto_impacto"          # já aceita antes: mantém
                            elif elegivel and alto_por_tipo.get(tipo, 0) < cota_por_tipo:
                                origem = "alto_impacto"
                                alto_por_tipo[tipo] = alto_por_tipo.get(tipo, 0) + 1
                            elif elegivel:
                                continue                          # só não coube na cota desta rodada
                            else:
                                a_descartar.append((id_ext, "autoria_congresso_sem_alto_impacto"))
                                continue
                achados.append(Item(
                    fonte="camara", id_externo=id_ext, sigla_tipo=r.get("siglaTipo", tipo),
                    numero=r.get("numero"), ano=r.get("ano"), ementa=ementa, origem=origem,
                    data_apresentacao=_data(r.get("dataApresentacao")), autoria=autoria,
                ))
    return achados, a_descartar


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
def carregar_existentes() -> tuple[dict[tuple[str, str], dict], set[str], set[tuple[str, str]]]:
    """(atos conhecidos com seu estado, identificações já vistas, atos examinados e descartados)."""
    with db.conexao_avulsa() as conn:
        linhas = conn.execute(
            "SELECT fonte, id_externo, identificacao, origem, autoria, analise_status "
            "FROM proposicoes_analisadas").fetchall()
        descartadas = conn.execute("SELECT fonte, id_externo FROM proposicoes_descartadas").fetchall()
    conhecidos = {(r["fonte"], r["id_externo"]): r for r in linhas}
    return (conhecidos, {r["identificacao"] for r in linhas},
            {(r["fonte"], r["id_externo"]) for r in descartadas})


def registrar_descartadas(descartes: list[tuple[str, str]]) -> None:
    if not descartes:
        return
    with db.conexao_avulsa() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO proposicoes_descartadas (fonte, id_externo, motivo) VALUES ('camara', %s, %s) "
                "ON CONFLICT (fonte, id_externo) DO NOTHING",
                descartes,
            )


def itens_em_tramitacao_fora_da_lista(ja_listados: set[tuple[str, str]]) -> list[Item]:
    """Atos da Câmara já conhecidos e ainda em tramitação que a janela de dias não devolveu.

    Sem isto, uma MP apresentada há 61 dias que fosse votada hoje ficaria "aguardando votação" para sempre.
    """
    with db.conexao_avulsa() as conn:
        linhas = conn.execute(
            """
            SELECT fonte, id_externo, sigla_tipo, numero, ano, ementa_oficial, origem, data_apresentacao,
                   autoria, nivel
              FROM proposicoes_analisadas
             WHERE fonte = 'camara'
               AND COALESCE(status_tramitacao, '') <> ALL (%s)
            """,
            (list(STATUS_FINAIS),),
        ).fetchall()
    return [
        Item(fonte=r["fonte"], id_externo=r["id_externo"], sigla_tipo=r["sigla_tipo"], numero=r["numero"],
             ano=r["ano"], ementa=r["ementa_oficial"], origem=r["origem"],
             data_apresentacao=r["data_apresentacao"], autoria=r["autoria"] or "", nivel=r["nivel"])
        for r in linhas if (r["fonte"], r["id_externo"]) not in ja_listados
    ]


def iniciar_coleta(desde: date | None, dias: int | None, com_ia: bool) -> int | None:
    try:
        with db.conexao_avulsa() as conn:
            return conn.execute(
                "INSERT INTO coletas (tipo, desde, janela_dias, com_ia) VALUES ('proposicoes', %s, %s, %s) RETURNING id",
                (desde, dias, com_ia),
            ).fetchone()["id"]
    except Exception as exc:  # noqa: BLE001 - o registro nunca deve derrubar a coleta
        log.warning("Não foi possível registrar a coleta: %s", exc)
        return None


def concluir_coleta(coleta_id: int | None, placar: "Placar") -> None:
    if coleta_id is None:
        return
    try:
        with db.conexao_avulsa() as conn:
            conn.execute(
                "UPDATE coletas SET concluida_em = now(), novos = %s, atualizados = %s, so_andamento = %s, "
                "pendentes = %s, falhas = %s WHERE id = %s",
                (placar.novos, placar.atualizados, placar.pulados, placar.pendentes, placar.falhas, coleta_id),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("Não foi possível concluir o registro da coleta: %s", exc)


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
def processar(item: Item, camara: Camara, args: argparse.Namespace, placar: Placar, *, com_ia: bool) -> None:
    anterior = estado_atual(item)
    ja_ok = bool(anterior and anterior["analise_status"] == "ok")
    so_andamento = (ja_ok and not args.forcar) or (not com_ia and anterior is not None)

    try:
        # O texto integral só é necessário para TRADUZIR; sem IA, ementa + andamento bastam.
        enriquecer(item, camara, baixar_texto=com_ia and not (ja_ok and not args.forcar))
    except ErroFonte as exc:
        log.warning("%s: não foi possível obter detalhes (%s)", item.identificacao, exc)
        placar.falhas += 1
        return

    if so_andamento:
        atualizar_andamento(item)
        placar.pulados += 1
        log.info("= %s: andamento atualizado%s.", item.identificacao, "" if ja_ok else " (ainda sem tradução)")
        return

    texto = item.texto or item.ementa
    texto_hash = _hash(texto)

    if com_ia and len(texto) > cfg.max_chars_texto:
        gravar(item, analise=None, status="texto_longo",
               erro=f"Texto com {len(texto):,} caracteres excede MAX_CHARS_TEXTO ({cfg.max_chars_texto:,}).",
               texto_hash=texto_hash, modelo=None)
        placar.pendentes += 1
        log.warning("! %s: texto muito longo (%s caracteres); análise adiada (aumente MAX_CHARS_TEXTO).",
                    item.identificacao, f"{len(texto):,}")
        return

    if not com_ia:
        gravar(item, analise=None, status="pendente", erro=None, texto_hash=texto_hash, modelo=None)
        placar.pendentes += 1
        log.info("+ %s coletada (sem tradução por IA).", item.identificacao)
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
    p.add_argument("--dias", type=int, default=60,
                   help="janela de apresentação, em dias (padrão: 60). Use o que quiser, ex.: --dias 365")
    p.add_argument("--tipos", default=",".join(TIPOS_PADRAO), help=f"siglas separadas por vírgula (padrão: {','.join(TIPOS_PADRAO)})")
    p.add_argument("--limite", type=int, default=25,
                   help="máximo de TRADUÇÕES por IA por execução (padrão: 25; 0 = sem limite). O excedente é "
                        "coletado sem tradução e entra na fila da próxima rodada")
    p.add_argument("--max-por-tipo", type=int, default=0,
                   help="teto de itens examinados por tipo/ano (padrão: 0 = percorre toda a janela)")
    p.add_argument("--max-alto-impacto", type=int, default=10,
                   help="máx. de propostas NOVAS do Congresso por 'alto impacto' por execução, dividido entre "
                        "PL, PLP e PEC (0 desliga)")
    p.add_argument("--incluir-creditos", action="store_true", help="inclui créditos extraordinários/orçamentários")
    p.add_argument("--sem-senado", action="store_true", help="não consulta o Senado Federal")
    p.add_argument("--ia", choices=["auto", "sim", "nao"], default="auto",
                   help="tradução por IA: auto = só se houver chave configurada (padrão), sim = exige, nao = só coleta")
    p.add_argument("--sem-ia", action="store_true", help="atalho para --ia nao")
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

    modo_ia = "nao" if args.sem_ia else args.ia
    usar_ia = {"sim": True, "nao": False, "auto": credenciais_configuradas()}[modo_ia]
    if modo_ia == "auto" and not usar_ia:
        log.info("Sem chave de IA configurada: coletando sem tradução (os atos ficam 'pendentes'). "
                 "Preencha o .env, ou use --ia sim se a credencial vem do login do CLI.")
    args.sem_ia = not usar_ia

    try:
        conhecidos, identificacoes, descartadas = carregar_existentes()
    except Exception as exc:  # noqa: BLE001
        log.error("Não consegui acessar o banco (%s). O PostgreSQL está no ar e o schema.sql foi aplicado?", exc)
        return 1

    pontual = bool(args.id)
    coleta_id = None if pontual else iniciar_coleta(desde, args.dias, usar_ia)

    if pontual:
        det = camara.detalhe(args.id)
        itens = [Item(fonte="camara", id_externo=str(args.id), sigla_tipo=det.get("siglaTipo", "?"),
                      numero=det.get("numero"), ano=det.get("ano"), ementa=det.get("ementa", ""),
                      origem="executivo", data_apresentacao=_data(det.get("dataApresentacao")))]
    else:
        itens, a_descartar = descobrir_camara(camara, tipos, desde, args, conhecidos, descartadas)
        registrar_descartadas(a_descartar)
        log.info("Câmara: %d aceitos na janela; %d novos descartes memorizados.", len(itens), len(a_descartar))
        if not args.sem_senado:
            itens += descobrir_senado(senado, tipos, desde, args, identificacoes | {i.identificacao for i in itens})
        # Atos ainda em tramitação que a janela de dias não devolveu continuam sendo atualizados.
        listados = {(i.fonte, i.id_externo) for i in itens}
        fora_da_janela = itens_em_tramitacao_fora_da_lista(listados)
        if fora_da_janela:
            log.info("Reexaminando %d ato(s) ainda em tramitação apresentados antes da janela.", len(fora_da_janela))
            itens += fora_da_janela

    # Quem precisa de tradução: atos novos e os ainda não traduzidos (ou todos, com --forcar).
    def traduzido(i: Item) -> bool:
        c = conhecidos.get((i.fonte, i.id_externo))
        return bool(c and c["analise_status"] == "ok")

    precisam = [i for i in itens if args.forcar or not traduzido(i)]
    so_andamento = [i for i in itens if not (args.forcar or not traduzido(i))]
    novas = sum(1 for i in precisam if (i.fonte, i.id_externo) not in conhecidos)
    fila_ia, sem_ia = ([], precisam)
    if usar_ia:
        limite = args.limite or len(precisam)
        fila_ia, sem_ia = precisam[:limite], precisam[limite:]
    log.info("Em exame: %d ato(s) — %d novos, %d já traduzidos (só andamento), %d para traduzir agora, "
             "%d coletados sem tradução.", len(itens), novas, len(so_andamento), len(fila_ia), len(sem_ia))
    if usar_ia and sem_ia:
        log.info("O que passou de --limite=%d fica pendente e entra na fila da próxima rodada.", args.limite)

    try:
        for item in fila_ia:
            processar(item, camara, args, placar, com_ia=True)
        for item in sem_ia + so_andamento:
            processar(item, camara, args, placar, com_ia=False)
    except ErroConfiguracaoLLM as exc:
        log.error("Configuração da IA inválida: %s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrompido pelo usuário.")

    try:
        reclassificar_temas()
    except Exception as exc:  # noqa: BLE001 - a classificação aproximada nunca deve derrubar a coleta
        log.warning("Não foi possível recalcular os temas por palavras-chave: %s", exc)

    concluir_coleta(coleta_id, placar)
    log.info("Resumo: %d traduzidas (novas) | %d retraduzidas | %d só andamento | %d coletadas sem tradução | %d falhas",
             placar.novos, placar.atualizados, placar.pulados, placar.pendentes, placar.falhas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
