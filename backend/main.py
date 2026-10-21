"""API REST do ImpactoCidadão (FastAPI).

Executar (a partir da raiz do projeto):
    uvicorn main:app --reload --app-dir backend
Documentação interativa:  http://localhost:8000/docs
Aplicativo web:           http://localhost:8000/

Convenções:
  * endpoints síncronos (`def`): o FastAPI os executa em threads, ideal p/ psycopg/httpx bloqueantes;
  * todo SQL usa parâmetros nomeados — nenhuma entrada do usuário é concatenada;
  * nada aqui chama a IA: a tradução acontece nos coletores; a API só lê o que já foi
    analisado (custo previsível e resposta rápida).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import autoria
import bancadas
import capitulos
import db
import ciclo
import competencias
import editor
import servicos
import economia
import equipamentos
import macro
import maturidade
import normas
import orcamento
import tributos
import simuladores
from config import RAIZ, cfg
from fontes import buscar_municipios, contexto_municipio
from schemas import CATEGORIAS, PERFIS, STATUS
from temas import TERMOS_POR_TEMA, consulta_tsquery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("impacto.api")

PerfilQuery = Annotated[str | None, Query(description="id do perfil (ex.: clt, mei, aposentado)")]


# --------------------------------------------------------------------------- #
# Aplicação
# --------------------------------------------------------------------------- #
def _aquecer_cache() -> None:
    try:
        macro.resumo()
    except Exception as exc:  # noqa: BLE001 — é só otimização; a primeira consulta refaz o trabalho
        log.info("Aquecimento do cache macro não concluído: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        db.abrir_pool()
    except Exception as exc:  # noqa: BLE001
        log.error("Banco indisponível na inicialização: %s", exc)
    # Aquece o cache dos indicadores em segundo plano: a página inicial não espera 30–60 s na primeira visita.
    threading.Thread(target=_aquecer_cache, name="aquecer-cache", daemon=True).start()
    yield
    db.fechar_pool()


app = FastAPI(
    title="ImpactoCidadão API",
    version="1.0.0",
    description="Leis, medidas provisórias e planos de governo traduzidos em impacto prático. "
                "Conteúdo gerado por IA a partir de fontes oficiais; sempre confira o documento original.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(cfg.cors_origins),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, call_next):
    resposta = await call_next(request)
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if not request.url.path.startswith("/api"):
        # páginas e scripts estáticos: o navegador revalida (ETag) em vez de reaproveitar uma cópia antiga
        resposta.headers.setdefault("Cache-Control", "no-cache")
    return resposta


@app.exception_handler(psycopg.OperationalError)
async def banco_indisponivel(_: Request, exc: psycopg.OperationalError):
    log.error("Erro de conexão com o banco: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "Banco de dados indisponível no momento."})


# --------------------------------------------------------------------------- #
# Limite simples de requisições de escrita/cálculo (por IP, em memória)
# --------------------------------------------------------------------------- #
class _Limitador:
    def __init__(self, maximo: int, janela_s: int) -> None:
        self.maximo, self.janela = maximo, janela_s
        self._hist: dict[str, deque[float]] = defaultdict(deque)

    def permitir(self, chave: str) -> bool:
        agora = time.monotonic()
        fila = self._hist[chave]
        while fila and fila[0] <= agora - self.janela:
            fila.popleft()
        if len(fila) >= self.maximo:
            return False
        fila.append(agora)
        if len(self._hist) > 20_000:  # evita crescimento ilimitado
            self._hist.clear()
        return True


_limite_escrita = _Limitador(maximo=60, janela_s=60)


def limitar(request: Request) -> None:
    ip = request.client.host if request.client else "desconhecido"
    if not _limite_escrita.permitir(ip):
        raise HTTPException(status_code=429, detail="Muitas requisições. Aguarde um instante e tente de novo.")


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _validar_perfil(perfil: str | None) -> str | None:
    if perfil in (None, ""):
        return None
    if perfil not in PERFIS:
        raise HTTPException(status_code=422, detail=f"Perfil inválido. Use um de: {', '.join(PERFIS)}.")
    return perfil


def _validar_categoria(categoria: str | None) -> str | None:
    if categoria in (None, "", "all"):
        return None
    if categoria not in CATEGORIAS:
        raise HTTPException(status_code=422, detail=f"Categoria inválida. Use uma de: {', '.join(CATEGORIAS)}.")
    return categoria


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _proposicao_json(r: dict) -> dict:
    return {
        "id": r["id"],
        "identificacao": r["identificacao"],
        "sigla_tipo": r["sigla_tipo"],
        "norma": normas.classificar(r["sigla_tipo"]),   # degrau da pirâmide de Kelsen (constitucional, complementar…)
        "instancia": r["instancia"],
        "nivel": r["nivel"],
        "uf": r["uf"],
        "municipio_ibge": r["municipio_ibge"],
        "autoria": r["autoria"],
        "tema": r["tema_principal"],
        "tema_origem": r["tema_origem"],   # "ia" (áreas definidas pela tradução) | "palavras_chave" (aproximado)
        "traduzida": r["analise_status"] == "ok",
        "situacao_oficial": r["situacao_atual"],
        # Sem tradução (IA indisponível): o título cai para a identificação oficial.
        "titulo": r["titulo_simples"] or r["identificacao"],
        "resumo": r["resumo_cidadao"],
        "perfis": r["perfis_impactados"],
        "categorias": r["categorias"],
        "impacto_pratico": r["impacto_pratico"],
        "ponto_positivo": r["ponto_positivo"],
        "ponto_atencao": r["ponto_atencao"],
        "status": r["status_tramitacao"],
        "relevancia": r["relevancia_bolso"],
        "data_apresentacao": r["data_apresentacao"].isoformat() if r["data_apresentacao"] else None,
        "origem": r["origem"],
        "ementa_oficial": r["ementa_oficial"],
        "url_fonte": r["url_fonte"],
        "linha_do_tempo": r["linha_do_tempo"],
        "texto_analisado": r["texto_fonte"],
        "votos": {
            "concordo": r["v_concordo"], "concordo_com_ajustes": r["v_ajustes"],
            "discordo": r["v_discordo"], "total": r["v_total"],
        },
    }


# Temas efetivos de um ato: as áreas definidas pela IA, se houver; senão, as aproximadas por
# palavras-chave (backend/temas.py). O tema principal é o primeiro da lista ("outros" se vazia).
_TEMAS_EFETIVOS = "(CASE WHEN cardinality(p.categorias) > 0 THEN p.categorias ELSE p.temas_palavras_chave END)"
_TEMA_PRINCIPAL = f"COALESCE({_TEMAS_EFETIVOS}[1], 'outros')"

_COLUNAS_PROPOSICAO = f"""
    p.id, p.identificacao, p.sigla_tipo, p.titulo_simples, p.resumo_cidadao, p.perfis_impactados,
    p.categorias, p.impacto_pratico, p.ponto_positivo, p.ponto_atencao, p.status_tramitacao,
    p.relevancia_bolso, p.data_apresentacao, p.origem, p.ementa_oficial, p.url_fonte,
    p.linha_do_tempo, p.texto_fonte, p.instancia, p.nivel, p.uf, p.municipio_ibge, p.autoria,
    p.analise_status, p.situacao_atual,
    {_TEMA_PRINCIPAL} AS tema_principal,
    CASE WHEN cardinality(p.categorias) > 0 THEN 'ia' ELSE 'palavras_chave' END AS tema_origem,
    COALESCE(v.concordo, 0)             AS v_concordo,
    COALESCE(v.concordo_com_ajustes, 0) AS v_ajustes,
    COALESCE(v.discordo, 0)             AS v_discordo,
    COALESCE(v.total, 0)                AS v_total
"""


# --------------------------------------------------------------------------- #
# Saúde e referências
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get("/saude", tags=["sistema"])
def saude() -> dict:
    return {"status": "ok", "banco": db.verificar(), "ia": cfg.llm_provider}


@router.get("/perfis", tags=["referencia"])
def listar_perfis() -> list[dict]:
    return db.consultar("SELECT id, nome, descricao, icone FROM perfis ORDER BY ordem")


@router.get("/categorias", tags=["referencia"])
def listar_categorias() -> list[dict]:
    return db.consultar("SELECT id, nome, icone FROM categorias ORDER BY ordem")


@router.get("/normas", tags=["referencia"])
def hierarquia_das_normas() -> dict:
    """Pirâmide de Kelsen aplicada aos tipos de proposição: o que é cada um, o rigor para aprovar e um exemplo."""
    return normas.catalogo()


@router.get("/status", tags=["referencia"])
def listar_status() -> list[str]:
    return list(STATUS)


# --------------------------------------------------------------------------- #
# Proposições
# --------------------------------------------------------------------------- #
@router.get("/proposicoes", tags=["proposicoes"])
def listar_proposicoes(
    perfil: PerfilQuery = None,
    categoria: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=120, description="busca por texto ou número (ex.: 'FGTS', 'MPV 1394')")] = None,
    ordem: Literal["recentes", "impacto"] = "recentes",
    instancia: Annotated[Literal["executivo", "legislativo"] | None, Query(
        description="poder de origem do ato")] = None,
    norma: Annotated[Literal["constitucional", "complementar", "ordinaria", "medida_provisoria", "ato_congresso"] | None, Query(
        description="tipo de norma na hierarquia (ver /api/normas)")] = None,
    nivel: Annotated[Literal["federal", "estadual", "municipal"] | None, Query(
        description="nível administrativo")] = None,
    incluir_pendentes: Annotated[bool, Query(
        description="inclui itens ainda sem tradução (só com dados oficiais); com um perfil escolhido eles "
                    "seguem na lista, pois não há como saber a quem afetam"
    )] = False,
    agrupar_por: Annotated[Literal["tema", "nivel_poder"] | None, Query(
        description="ordena por grupos antes da ordenação escolhida")] = None,
    desde: Annotated[date | None, Query(description="atos apresentados a partir desta data (AAAA-MM-DD)")] = None,
    ate: Annotated[date | None, Query(description="atos apresentados até esta data (AAAA-MM-DD)")] = None,
    pagina: Annotated[int, Query(ge=1, le=1000)] = 1,
    por_pagina: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    if desde and ate and desde > ate:
        raise HTTPException(status_code=422, detail="A data inicial não pode ser posterior à final.")
    perfil = _validar_perfil(perfil)
    categoria = _validar_categoria(categoria)
    if status in ("", "all"):
        status = None
    if status is not None and status not in STATUS:
        raise HTTPException(status_code=422, detail="Status inválido.")
    termo = (q or "").strip() or None

    ordenacao = (
        "CASE p.relevancia_bolso WHEN 'alta' THEN 0 WHEN 'media' THEN 1 WHEN 'baixa' THEN 2 ELSE 3 END, "
        "p.data_apresentacao DESC NULLS LAST, p.id DESC"
        if ordem == "impacto" else "p.data_apresentacao DESC NULLS LAST, p.id DESC"
    )
    if agrupar_por == "tema":
        # Ordem dos grupos = ordem das categorias; "outros" por último. Dentro do grupo, os traduzidos primeiro.
        ordenacao = (f"array_position(%(ordem_temas)s::text[], {_TEMA_PRINCIPAL}), "
                     "(p.analise_status <> 'ok'), " + ordenacao)
    elif agrupar_por == "nivel_poder":
        ordenacao = (
            "CASE p.nivel WHEN 'federal' THEN 0 WHEN 'estadual' THEN 1 ELSE 2 END, "
            "CASE p.instancia WHEN 'executivo' THEN 0 ELSE 1 END, " + ordenacao
        )
    # Atos ainda sem tradução não têm perfis classificados: com `incluir_pendentes` eles continuam na
    # lista (marcados como "não traduzidos" pela tela) em vez de a página ficar vazia ao escolher um perfil.
    filtros = f"""
         WHERE (p.analise_status = 'ok' OR %(pendentes)s)
           AND (%(instancia)s::text IS NULL OR p.instancia = %(instancia)s::text)
           AND (%(nivel)s::text IS NULL OR p.nivel = %(nivel)s::text)
           AND (%(siglas)s::text[] IS NULL OR p.sigla_tipo = ANY (%(siglas)s::text[]))
           AND (%(desde)s::date IS NULL OR p.data_apresentacao >= %(desde)s::date)
           AND (%(ate)s::date IS NULL OR p.data_apresentacao <= %(ate)s::date)
           AND (%(perfil)s::text IS NULL OR %(perfil)s::text = ANY (p.perfis_impactados) OR 'todos' = ANY (p.perfis_impactados)
                OR (%(pendentes)s AND p.analise_status <> 'ok'))
           AND (%(categoria)s::text IS NULL OR {_TEMA_PRINCIPAL} = %(categoria)s::text)
           AND (%(status)s::text IS NULL OR p.status_tramitacao = %(status)s::text)
           AND (%(q)s::text IS NULL
                OR p.busca @@ websearch_to_tsquery('portuguese', %(q)s::text)
                OR p.identificacao ILIKE '%%' || %(q_like)s::text || '%%')
    """
    parametros = {
        "perfil": perfil, "categoria": categoria, "status": status, "q": termo,
        "q_like": _escapar_like(termo) if termo else None,
        "instancia": instancia, "nivel": nivel, "pendentes": incluir_pendentes,
        "siglas": normas.siglas_da_norma(norma) if norma else None,
        "desde": desde, "ate": ate, "ordem_temas": list(CATEGORIAS),
    }
    linhas = db.consultar(
        f"""
        SELECT {_COLUNAS_PROPOSICAO}, count(*) OVER() AS total_geral
          FROM proposicoes_analisadas p
          LEFT JOIN vw_placar_votos v ON v.proposicao_id = p.id
          {filtros}
         ORDER BY {ordenacao}
         LIMIT %(limite)s OFFSET %(deslocamento)s
        """,
        {**parametros, "limite": por_pagina, "deslocamento": (pagina - 1) * por_pagina},
    )
    # Quantos atos há em cada tema, sob os mesmos filtros (para os cabeçalhos dos grupos).
    por_tema = db.consultar(
        f"SELECT {_TEMA_PRINCIPAL} AS tema, count(*) AS n FROM proposicoes_analisadas p {filtros} GROUP BY 1",
        parametros,
    )
    # Quantos atos há em cada tipo de norma, sob os mesmos filtros MENOS o próprio tipo (para a pirâmide mostrar as opções).
    por_sigla = db.consultar(
        f"SELECT p.sigla_tipo AS sigla, count(*) AS n FROM proposicoes_analisadas p {filtros} GROUP BY 1",
        {**parametros, "siglas": None},
    )
    por_norma: dict[str, int] = {i: 0 for i in normas.IDS}
    for r in por_sigla:
        nid = normas.classificar(r["sigla"])
        if nid:
            por_norma[nid] += r["n"]
    total = linhas[0]["total_geral"] if linhas else 0
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina,
            "temas": {r["tema"]: r["n"] for r in por_tema}, "normas": por_norma,
            "itens": [_proposicao_json(r) for r in linhas]}


@router.get("/proposicoes/{proposicao_id}", tags=["proposicoes"])
def obter_proposicao(proposicao_id: int) -> dict:
    linha = db.consultar_um(
        f"SELECT {_COLUNAS_PROPOSICAO} FROM proposicoes_analisadas p "
        "LEFT JOIN vw_placar_votos v ON v.proposicao_id = p.id "
        "WHERE p.id = %s",
        (proposicao_id,),
    )
    if not linha:
        raise HTTPException(status_code=404, detail="Proposição não encontrada.")
    return _proposicao_json(linha)


# --- Cobertura por poder (instância) e nível administrativo ----------------- #
NIVEIS = [("federal", "Federal"), ("estadual", "Estadual"), ("municipal", "Municipal")]
INSTANCIAS = [("executivo", "Executivo"), ("legislativo", "Legislativo")]
_FORA = "Fonte a conectar."
# (aplicável?, fonte conectada?, fonte, observação) — declarado aqui para a tela nunca fingir cobertura.
COBERTURA: dict[tuple[str, str], tuple[bool, bool, str, str]] = {
    ("federal", "executivo"): (True, True, "Câmara dos Deputados e Senado Federal (dados abertos)",
                               "Medidas provisórias e projetos de lei de autoria do Executivo. "
                               "Decretos (Diário Oficial da União) ainda não são coletados."),
    ("federal", "legislativo"): (True, True, "Câmara dos Deputados (dados abertos)",
                                 "Amostra: projetos do Congresso selecionados por mencionarem vários temas de "
                                 "impacto no bolso — não é o universo de proposições."),
    ("estadual", "executivo"): (True, False, "Diários Oficiais estaduais",
                                "Não há API única: cada estado publica de um jeito. " + _FORA),
    ("estadual", "legislativo"): (True, False, "Assembleias Legislativas", "Cada assembleia tem seu próprio portal. " + _FORA),
    ("municipal", "executivo"): (True, False, "Diários Oficiais municipais (Querido Diário, Open Knowledge Brasil)",
                                 "A API pública não completou a conexão no último teste. " + _FORA),
    ("municipal", "legislativo"): (True, False, "Câmaras Municipais e Diários Oficiais municipais", _FORA),
}


@router.get("/politicas/resumo", tags=["proposicoes"])
def resumo_politicas() -> dict:
    """Matriz poder × nível: quantos atos há e se a fonte de dados está conectada."""
    contagens = {
        (r["nivel"], r["instancia"]): r
        for r in db.consultar(
            "SELECT nivel, instancia, count(*) AS total, "
            "count(*) FILTER (WHERE analise_status = 'ok') AS traduzidas "
            "FROM proposicoes_analisadas GROUP BY nivel, instancia"
        )
    }
    celulas = []
    for nivel, nome_nivel in NIVEIS:
        for instancia, nome_instancia in INSTANCIAS:
            aplicavel, conectada, fonte, obs = COBERTURA[(nivel, instancia)]
            c = contagens.get((nivel, instancia))
            celulas.append({
                "nivel": nivel, "instancia": instancia,
                "nome_nivel": nome_nivel, "nome_instancia": nome_instancia,
                "aplicavel": aplicavel, "fonte_conectada": conectada, "fonte": fonte, "observacao": obs,
                "total": c["total"] if c else 0, "traduzidas": c["traduzidas"] if c else 0,
            })
    # Frescor dos dados: quando a coleta rodou pela última vez e até onde no passado ela olhou.
    coleta = db.consultar_um(
        "SELECT max(concluida_em) AS ultima, min(desde) AS cobertura_desde "
        "FROM coletas WHERE tipo = 'proposicoes' AND concluida_em IS NOT NULL"
    ) or {}
    datas = db.consultar_um(
        "SELECT min(data_apresentacao) AS mais_antigo, max(data_apresentacao) AS mais_recente "
        "FROM proposicoes_analisadas"
    ) or {}
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"niveis": [{"id": i, "nome": n} for i, n in NIVEIS],
            "instancias": [{"id": i, "nome": n} for i, n in INSTANCIAS], "celulas": celulas,
            "coleta": {"ultima_coleta": iso(coleta.get("ultima")),
                       "cobertura_desde": iso(coleta.get("cobertura_desde")),
                       "ato_mais_antigo": iso(datas.get("mais_antigo")),
                       "ato_mais_recente": iso(datas.get("mais_recente"))}}


# --- Votação -------------------------------------------------------------- #
class VotoEntrada(BaseModel):
    voto: Literal["concordo", "concordo_com_ajustes", "discordo"]
    perfil: str | None = None
    token: str = Field(min_length=16, max_length=64, description="identificador anônimo gerado no navegador")

    @field_validator("token")
    @classmethod
    def _token_seguro(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", v):
            raise ValueError("token inválido")
        return v

    @field_validator("perfil")
    @classmethod
    def _perfil_valido(cls, v: str | None) -> str | None:
        if v is not None and v not in PERFIS:
            raise ValueError("perfil inválido")
        return v


def _placar(proposicao_id: int) -> dict:
    linha = db.consultar_um(
        "SELECT COALESCE(concordo,0) AS concordo, COALESCE(concordo_com_ajustes,0) AS concordo_com_ajustes, "
        "COALESCE(discordo,0) AS discordo, COALESCE(total,0) AS total "
        "FROM (SELECT %s::bigint AS id) x LEFT JOIN vw_placar_votos v ON v.proposicao_id = x.id",
        (proposicao_id,),
    )
    return linha or {"concordo": 0, "concordo_com_ajustes": 0, "discordo": 0, "total": 0}


@router.get("/proposicoes/{proposicao_id}/votos", tags=["votacao"])
def ver_votos(proposicao_id: int) -> dict:
    return _placar(proposicao_id)


@router.post("/proposicoes/{proposicao_id}/voto", tags=["votacao"], dependencies=[Depends(limitar)])
def votar(proposicao_id: int, entrada: VotoEntrada) -> dict:
    """Registra (ou troca) o voto de um participante anônimo.

    Um voto por token; o token é guardado apenas como hash com sal. É uma consulta
    de opinião informal — NÃO é pesquisa eleitoral nem representa a população.
    """
    existe = db.consultar_um("SELECT 1 AS ok FROM proposicoes_analisadas WHERE id = %s", (proposicao_id,))
    if not existe:
        raise HTTPException(status_code=404, detail="Proposição não encontrada.")
    anon_hash = hashlib.sha256(f"{cfg.voto_sal}:{entrada.token}".encode()).hexdigest()
    with db.conexao() as conn:
        conn.execute(
            """
            INSERT INTO votos_comunitarios (proposicao_id, anon_hash, voto, perfil_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (proposicao_id, anon_hash)
            DO UPDATE SET voto = EXCLUDED.voto, perfil_id = EXCLUDED.perfil_id
            """,
            (proposicao_id, anon_hash, entrada.voto, entrada.perfil),
        )
    return {"registrado": entrada.voto, "placar": _placar(proposicao_id)}


# --------------------------------------------------------------------------- #
# Simuladores
# --------------------------------------------------------------------------- #
class SimulacaoIR(BaseModel):
    salario_bruto: float = Field(ge=0, le=1_000_000, description="rendimento tributável mensal, em R$")
    dependentes: int = Field(default=0, ge=0, le=20)
    outras_deducoes: float = Field(default=0, ge=0, le=500_000, description="pensão alimentícia, previdência privada etc.")


class SimulacaoMEI(BaseModel):
    faturamento_anual: float = Field(ge=0, le=10_000_000)
    atividade: Literal["comercio_industria", "servicos", "comercio_servicos"] = "servicos"
    meses_de_atividade: int = Field(default=12, ge=1, le=12)
    teto_hipotetico: float | None = Field(default=None, gt=0, le=10_000_000,
                                          description="simula 'e se o teto fosse X?' — não é lei aprovada")


@router.post("/simulador/ir", tags=["simuladores"], dependencies=[Depends(limitar)])
def simulador_ir(entrada: SimulacaoIR) -> dict:
    return simuladores.simular_ir(entrada.salario_bruto, entrada.dependentes, entrada.outras_deducoes)


@router.post("/simulador/mei", tags=["simuladores"], dependencies=[Depends(limitar)])
def simulador_mei(entrada: SimulacaoMEI) -> dict:
    return simuladores.simular_mei(entrada.faturamento_anual, entrada.atividade,
                                   entrada.meses_de_atividade, entrada.teto_hipotetico)


# --------------------------------------------------------------------------- #
# Municípios
# --------------------------------------------------------------------------- #
@router.get("/municipios", tags=["municipios"])
def pesquisar_municipios(q: Annotated[str, Query(min_length=2, max_length=60)]) -> list[dict]:
    try:
        return buscar_municipios(q)
    except Exception as exc:  # noqa: BLE001
        log.warning("IBGE indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="Lista de municípios (IBGE) indisponível no momento.") from exc


# --------------------------------------------------------------------------- #
# Composição partidária (Câmara e Senado)
# --------------------------------------------------------------------------- #
@router.get("/bancadas", tags=["bancadas"])
def bancadas_partidarias() -> dict:
    try:
        return bancadas.bancadas()
    except Exception as exc:  # noqa: BLE001
        log.warning("Composição partidária indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="As fontes oficiais da Câmara e do Senado estão indisponíveis no momento.") from exc


@router.get("/bancadas/alinhamento", tags=["bancadas"])
def bancadas_alinhamento(desde: Annotated[int | None, Query(ge=2000, le=2100)] = None,
                         ate: Annotated[int | None, Query(ge=2000, le=2100)] = None) -> dict:
    try:
        return bancadas.alinhamento(desde, ate)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.warning("Alinhamento indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="Os dados de votação ainda não estão disponíveis.") from exc


@router.get("/bancadas/tematicas", tags=["bancadas"])
def bancadas_tematicas() -> dict:
    try:
        return bancadas.frentes_tematicas()
    except Exception as exc:  # noqa: BLE001
        log.warning("Frentes parlamentares indisponíveis: %s", exc)
        raise HTTPException(status_code=503, detail="A fonte oficial das frentes parlamentares (Câmara) está indisponível no momento.") from exc


# --------------------------------------------------------------------------- #
# Autoria dos projetos da Câmara (líderes por tema, projetos por político, divisão por tema)
# --------------------------------------------------------------------------- #
def _autoria(fn, *args):  # noqa: ANN001
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Deputado(a) não encontrado(a) nos dados de autoria.") from exc
    except Exception as exc:  # noqa: BLE001
        log.warning("Autoria indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="Os dados de autoria dos projetos ainda não estão disponíveis.") from exc


@router.get("/autoria/resumo", tags=["autoria"])
def autoria_resumo(desde: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
                   ate: Annotated[date | None, Query(description="AAAA-MM-DD")] = None) -> dict:
    return _autoria(autoria.resumo, desde, ate)


@router.get("/autoria/painel", tags=["autoria"])
def autoria_painel(visao: Annotated[str, Query(pattern="^(tema|deputado|partido|tipo|efeito|parada)$")] = "tema",
                   desde: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
                   ate: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
                   tema: Annotated[int | None, Query(ge=1, le=999)] = None,
                   partido: Annotated[str | None, Query(max_length=30)] = None,
                   efeito: Annotated[str | None, Query(pattern="^(restringe|amplia|procedimental|indefinido)$")] = None) -> dict:
    return _autoria(autoria.painel, visao, desde, ate, tema, partido, efeito)


@router.get("/autoria/catalogo", tags=["autoria"])
def autoria_catalogo() -> dict:
    return _autoria(autoria.catalogo)


@router.get("/autoria/analise", tags=["autoria"])
def autoria_analise(termo: Annotated[list[str], Query(min_length=1, max_length=6, description="t:<código>, a:<assunto> ou k:<palavra>")],
                    de: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="AAAA-MM")],
                    ate: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="AAAA-MM")],
                    lente: Annotated[str, Query(pattern="^(partido|intencao|parlamentares|gargalo)$")] = "partido") -> dict:
    return _autoria(autoria.analise, termo, lente, de, ate)


@router.get("/autoria/analise/projetos", tags=["autoria"])
def autoria_analise_projetos(termo: Annotated[list[str], Query(min_length=1, max_length=6)],
                             de: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
                             ate: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
                             partido: Annotated[str | None, Query(max_length=30)] = None,
                             efeito: Annotated[str | None, Query(pattern="^(restringe|amplia|procedimental|indefinido)$")] = None,
                             etapa: Annotated[str | None, Query(pattern="^(arquivada|senado|plenario|comissao|mesa|outro)$")] = None,
                             sigla: Annotated[str | None, Query(max_length=30)] = None,
                             onde: Annotated[str | None, Query(max_length=200)] = None,
                             so_lei: bool = False) -> dict:
    return _autoria(autoria.projetos_da_analise, termo, de, ate, partido, efeito, etapa, sigla, onde, so_lei)


@router.get("/autoria/projetos", tags=["autoria"])
def autoria_projetos(q: Annotated[str | None, Query(max_length=80)] = None,
                     tipo: Annotated[str | None, Query(pattern="^(PEC|PLP|PL)$")] = None,
                     efeito: Annotated[str | None, Query(pattern="^(restringe|amplia|procedimental|indefinido)$")] = None,
                     efetividade: Annotated[str | None, Query(pattern="^(em_tramitacao|aprovado|arquivado)$")] = None,
                     desde: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
                     ate: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
                     deslocamento: Annotated[int, Query(ge=0, le=100000)] = 0,
                     ano_de: Annotated[int | None, Query(ge=1990, le=2100)] = None,
                     ano_ate: Annotated[int | None, Query(ge=1990, le=2100)] = None) -> dict:
    return _autoria(autoria.buscar_projetos, q, tipo, efeito, efetividade, desde, ate, deslocamento, ano_de, ano_ate)


@router.get("/autoria/politicos", tags=["autoria"])
def autoria_politicos(q: Annotated[str, Query(min_length=2, max_length=60)],
                      desde: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
                      ate: Annotated[date | None, Query(description="AAAA-MM-DD")] = None) -> dict:
    return _autoria(autoria.buscar_politicos, q, desde, ate)


@router.get("/autoria/politicos/{politico_id}", tags=["autoria"])
def autoria_politico(politico_id: int, desde: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
                     ate: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
                     tema: Annotated[int | None, Query(ge=1, le=999)] = None) -> dict:
    return _autoria(autoria.projetos_do_politico, politico_id, desde, ate, tema)


# --------------------------------------------------------------------------- #
# Indicadores macroeconômicos (BCB, IBGE, Tesouro Nacional)
# --------------------------------------------------------------------------- #
def _fonte_macro_indisponivel(exc: Exception) -> HTTPException:
    log.warning("Fonte macroeconômica indisponível: %s", exc)
    return HTTPException(status_code=503, detail="Uma fonte oficial (Banco Central, IBGE ou Tesouro) está indisponível no momento.")


@router.get("/macro/catalogo", tags=["macro"])
def macro_catalogo() -> list[dict]:
    return macro.catalogo()


@router.get("/macro/resumo", tags=["macro"])
def macro_resumo() -> dict:
    try:
        return macro.resumo()
    except Exception as exc:  # noqa: BLE001
        raise _fonte_macro_indisponivel(exc) from exc


@router.get("/macro/serie/{chave}", tags=["macro"])
def macro_serie(
    chave: Annotated[str, Path(pattern=r"^[a-z0-9_]{2,20}$")],
    inicio: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
    fim: Annotated[date | None, Query(description="AAAA-MM-DD")] = None,
) -> dict:
    try:
        return macro.serie(chave, inicio.isoformat() if inicio else None, fim.isoformat() if fim else None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Indicador desconhecido.") from exc
    except Exception as exc:  # noqa: BLE001
        raise _fonte_macro_indisponivel(exc) from exc


@router.get("/tributos/arrecadacao", tags=["tributos"])
def tributos_arrecadacao(municipio: Annotated[int | None, Query(ge=1000000, le=5999999)] = None) -> dict:
    """Arrecadação dos últimos 12 meses por tributo: federal (Tesouro) e, com município, estadual e municipal (SICONFI)."""
    try:
        return tributos.arrecadacao(municipio)
    except Exception as exc:  # noqa: BLE001
        log.warning("Arrecadação por tributo indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="Os dados de arrecadação estão indisponíveis no momento.") from exc


@router.get("/tributos/repasses", tags=["tributos"])
def tributos_repasses(municipio: Annotated[int | None, Query(ge=1000000, le=5999999)] = None) -> dict:
    """Repasses entre União, estado e município (SICONFI) e a repartição de receitas da União (Tesouro)."""
    try:
        return tributos.repasses(municipio)
    except Exception as exc:  # noqa: BLE001
        log.warning("Repasses indisponíveis: %s", exc)
        raise HTTPException(status_code=503, detail="Os dados de repasses estão indisponíveis no momento.") from exc


@router.get("/tributos/fluxo-federal", tags=["tributos"])
def tributos_fluxo_federal() -> dict:
    try:
        return tributos.fluxo_federal()
    except Exception as exc:  # noqa: BLE001
        log.warning("Fluxo federal indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="O Resultado do Tesouro Nacional está indisponível no momento.") from exc


def _ou_404(valor, msg="Não encontrado."):  # noqa: ANN001
    if valor is None:
        raise HTTPException(status_code=404, detail=msg)
    return valor


def _servicos(fn, *args):  # noqa: ANN001
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        log.warning("Guia de serviços indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="Não foi possível montar o guia de serviços agora.") from exc


MunicipioQuery = Annotated[int | None, Query(ge=1000000, le=9999999, description="código IBGE do município")]


@router.get("/servicos", tags=["servicos"])
def servicos_indice(municipio: MunicipioQuery = None) -> dict:
    return _servicos(servicos.indice, municipio)


@router.get("/servicos/esfera/{esfera}", tags=["servicos"])
def servicos_esfera(esfera: Literal["municipio", "estado", "uniao"]) -> dict:
    return _ou_404(_servicos(servicos.por_esfera, esfera))


@router.get("/servicos/{area_id}", tags=["servicos"])
def servicos_area(area_id: str, municipio: MunicipioQuery = None) -> dict:
    return _ou_404(_servicos(servicos.area, area_id, municipio), "Área não encontrada.")


@router.get("/servicos/{area_id}/{slug}", tags=["servicos"])
def servicos_servico(area_id: str, slug: str, municipio: MunicipioQuery = None) -> dict:
    return _ou_404(_servicos(servicos.servico, area_id, slug, municipio), "Serviço não encontrado.")


@router.get("/executivo/orgaos", tags=["executivo"])
def executivo_orgaos() -> dict:
    """Orçamento federal por ministério e órgão vinculado (arquivo gerado por backend/coletor_orgaos.py)."""
    arq = RAIZ / "dados" / "orcamento" / "orgaos.json"
    if not arq.exists():
        raise HTTPException(status_code=503, detail="Dados dos ministérios ainda não coletados. Rode: python backend/coletor_orgaos.py")
    dados = json.loads(arq.read_text(encoding="utf-8"))
    titulares = RAIZ / "dados" / "ministros.json"
    mapa = json.loads(titulares.read_text(encoding="utf-8")) if titulares.exists() else {}
    for o in dados["orgaos"]:
        o["ministro"] = mapa.get(o["codigo"])
    return dados


@router.get("/competencias/areas", tags=["competencias"])
def competencias_areas() -> list[dict]:
    return competencias.areas()


@router.get("/competencias/areas/{area_id}", tags=["competencias"])
def competencias_area(area_id: str, municipio: Annotated[int | None, Query(ge=1000000, le=9999999)] = None) -> dict:
    try:
        r = competencias.area(area_id, municipio)
    except Exception as exc:  # noqa: BLE001
        log.warning("Área de interesse indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="Não foi possível montar esta área agora.") from exc
    if not r:
        raise HTTPException(status_code=404, detail="Área não encontrada.")
    return r


@router.get("/competencias/buscar", tags=["competencias"])
def competencias_buscar(
    q: Annotated[str, Query(min_length=2, max_length=120, description="problema do dia a dia, ex.: vaga na creche")],
    municipio: Annotated[int | None, Query(ge=1000000, le=5999999, description="código IBGE do município")] = None,
) -> dict:
    try:
        return competencias.buscar(q, municipio)
    except Exception as exc:  # noqa: BLE001
        log.warning("Busca de competências falhou: %s", exc)
        raise HTTPException(status_code=503, detail="Não foi possível consultar a matriz de competências agora.") from exc


@router.get("/equipamentos/{codigo_ibge}", tags=["equipamentos"])
def equipamentos_municipio(codigo_ibge: Annotated[int, Path(ge=1000000, le=5999999)]) -> dict:
    """Unidades de saúde (CNES) e outros serviços (OpenStreetMap) de um município, com coordenadas."""
    try:
        return equipamentos.listar(codigo_ibge)
    except Exception as exc:  # noqa: BLE001
        log.warning("Equipamentos indisponíveis (%s): %s", codigo_ibge, exc)
        raise HTTPException(status_code=503, detail="O cadastro de equipamentos públicos está indisponível no momento.") from exc


@router.get("/equipamentos/{codigo_ibge}/verba", tags=["equipamentos"])
def equipamentos_verba(
    codigo_ibge: Annotated[int, Path(ge=1000000, le=5999999)],
    categoria: Annotated[Literal["saude", "educacao", "seguranca", "assistencia", "outros"], Query()],
    esfera: Annotated[Literal["Municipal", "Estadual", "Federal"], Query()],
) -> dict:
    """Responsável e verba autorizada na LOA (dotação inicial do RREO) para o tipo de equipamento e a esfera dele."""
    try:
        return competencias.ficha(codigo_ibge, categoria, esfera)
    except Exception as exc:  # noqa: BLE001
        log.warning("Verba do equipamento indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="Os dados de orçamento estão indisponíveis no momento.") from exc


@router.get("/macro/ciclo", tags=["macro"])
def macro_ciclo() -> dict:
    try:
        return ciclo.ciclo()
    except Exception as exc:  # noqa: BLE001
        raise _fonte_macro_indisponivel(exc) from exc


@router.get("/macro/financiamento", tags=["macro"])
def macro_financiamento() -> dict:
    try:
        return macro.financiamento()
    except Exception as exc:  # noqa: BLE001
        raise _fonte_macro_indisponivel(exc) from exc


@router.get("/macro/reservas", tags=["macro"])
def macro_reservas() -> dict:
    try:
        return macro.reservas()
    except Exception as exc:  # noqa: BLE001
        raise _fonte_macro_indisponivel(exc) from exc


@router.get("/macro/divida", tags=["macro"])
def macro_divida() -> dict:
    try:
        return macro.divida()
    except Exception as exc:  # noqa: BLE001
        raise _fonte_macro_indisponivel(exc) from exc


# --------------------------------------------------------------------------- #
# Economia do setor público (SICONFI)
# --------------------------------------------------------------------------- #
@router.get("/economia/{codigo_ibge}", tags=["economia"])
def economia_municipio(codigo_ibge: Annotated[int, Path(ge=1, le=5999999)]) -> dict:
    """Saúde fiscal, receitas, despesas e execução orçamentária do município (código IBGE de 7 dígitos)."""
    try:
        painel = economia.painel_economia(codigo_ibge)
    except Exception as exc:  # noqa: BLE001
        log.warning("SICONFI indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="O SICONFI (Tesouro Nacional) está indisponível no momento.") from exc
    if painel is None:
        raise HTTPException(status_code=404, detail="Este município ainda não publicou relatórios no SICONFI.")
    contexto = None
    try:
        contexto = contexto_municipio(codigo_ibge)
    except Exception:  # noqa: BLE001
        pass
    return {**painel, "municipio": contexto}


# --------------------------------------------------------------------------- #
# Ciclo orçamentário (PPA, LDO, LOA) e "para onde vai o meu imposto"
# --------------------------------------------------------------------------- #
@router.get("/orcamento/ciclo/{codigo}", tags=["orcamento"])
def orcamento_ciclo(codigo: Annotated[int, Path(ge=1, le=5999999)]) -> dict:
    """PPA, LDO e LOA da esfera do ente (1 = União, 2 dígitos = UF, 7 dígitos = município)."""
    try:
        return orcamento.ciclo(codigo)
    except Exception as exc:  # noqa: BLE001
        log.warning("Fonte do ciclo orçamentário indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="A fonte dos documentos orçamentários está indisponível no momento.") from exc


@router.get("/orcamento/imposto/{codigo}", tags=["orcamento"])
def orcamento_imposto(
    codigo: Annotated[int, Path(ge=1, le=5999999)],
    valor: Annotated[float, Query(gt=0, le=100_000_000, description="imposto pago no ano, em R$")],
) -> dict:
    """Distribui `valor` pelas áreas de governo, proporcional à despesa liquidada por função (ilustração)."""
    try:
        res = orcamento.distribuir_imposto(codigo, valor)
    except Exception as exc:  # noqa: BLE001
        log.warning("SICONFI indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="O SICONFI (Tesouro Nacional) está indisponível no momento.") from exc
    if res is None:
        raise HTTPException(status_code=404, detail="Este ente ainda não publicou relatórios no SICONFI.")
    return res


# --------------------------------------------------------------------------- #
# Candidatos e planos de governo
# --------------------------------------------------------------------------- #
AVISO_IA = ("Resumo gerado por inteligência artificial a partir do plano de governo registrado no TSE. "
            "Cada proposta traz o trecho literal e a página do documento original; confira sempre a fonte.")
AVISO_LOCAL = ("O que um plano promete não é garantia de execução: depende de aprovação do Congresso, "
               "de orçamento e, em muitos casos, da adesão de estados e prefeituras.")

_FILTRO_CANDIDATOS = """
    c.ano_eleicao = %(ano)s AND c.cargo = 'Presidente'
    AND (%(todos)s OR lower(c.situacao_candidatura) LIKE 'deferido%%' OR lower(coalesce(c.totalizacao, '')) LIKE '2%%')
    AND (NOT %(turno2)s OR lower(coalesce(c.totalizacao, '')) LIKE '2%%')
"""


def _candidato_json(r: dict) -> dict:
    return {
        "sq_candidato": r["sq_candidato"], "nome": r["nome_urna"], "numero": r["numero"],
        "partido": r["sigla_partido"], "situacao": r["situacao_candidatura"], "totalizacao": r["totalizacao"],
        "vice": r["vice"],
    }


@router.get("/candidatos", tags=["candidatos"])
def listar_candidatos(
    turno2: Annotated[bool, Query(description="apenas candidatos no 2º turno (segundo o TSE)")] = False,
    todos: Annotated[bool, Query(description="inclui candidaturas indeferidas/renunciadas")] = False,
) -> dict:
    linhas = db.consultar(
        f"""
        SELECT c.*, p.id AS plano_id, p.status AS plano_status, p.url_pdf, p.paginas, p.resumo_geral,
               p.propostas_descartadas, p.analisado_em, p.modelo_llm,
               (SELECT count(*) FROM propostas_candidato x WHERE x.plano_id = p.id)                         AS n_propostas,
               (SELECT count(*) FROM propostas_candidato x WHERE x.plano_id = p.id AND x.tem_meta_numerica)  AS n_metas,
               (SELECT count(*) FROM propostas_candidato x WHERE x.plano_id = p.id AND x.tem_prazo)         AS n_prazos,
               (SELECT count(*) FROM propostas_candidato x WHERE x.plano_id = p.id AND x.tem_fonte_recurso) AS n_recursos
          FROM candidatos c
          LEFT JOIN planos_governo p ON p.sq_candidato = c.sq_candidato
         WHERE {_FILTRO_CANDIDATOS}
         ORDER BY c.nome_urna
        """,
        {"ano": cfg.tse_ano, "todos": todos, "turno2": turno2},
    )
    itens = []
    for r in linhas:
        itens.append({
            **_candidato_json(r),
            "plano": {
                "status": r["plano_status"] or "sem_plano",
                "url_pdf": r["url_pdf"], "paginas": r["paginas"], "resumo_geral": r["resumo_geral"],
                "analisado_em": r["analisado_em"].isoformat() if r["analisado_em"] else None,
                "modelo": r["modelo_llm"],
                "propostas": r["n_propostas"], "com_meta_numerica": r["n_metas"],
                "com_prazo": r["n_prazos"], "com_fonte_de_recurso": r["n_recursos"],
                "citacoes_descartadas": r["propostas_descartadas"],
            },
        })
    ultima = db.consultar_um(
        "SELECT max(concluida_em) AS ultima FROM coletas WHERE tipo = 'candidatos' AND concluida_em IS NOT NULL"
    ) or {}
    return {"eleicao": cfg.tse_ano, "ordenacao": "alfabética por nome de urna (sem ranking)",
            "aviso": AVISO_IA, "atualizado_em": ultima["ultima"].isoformat() if ultima.get("ultima") else None,
            "itens": itens}


def _como_chega(canal: str, cidade: str) -> str:
    return {
        "beneficio_direto": f"Seria pago ou entregue diretamente às pessoas elegíveis: moradores de {cidade} que "
                            "cumprissem os critérios receberiam sem depender da prefeitura.",
        "programa_via_municipio": f"Depende de execução ou adesão da prefeitura: em {cidade}, a medida só "
                                  "chegaria se o município aderisse e estruturasse o serviço.",
        "repasse_a_municipios": f"Prevê repasses do governo federal aos municípios; o valor que caberia a {cidade} "
                                "depende de critérios que o plano não detalha.",
        "regulacao_nacional": f"É uma regra nacional: valeria da mesma forma em {cidade} e no resto do país.",
        "investimento_federal_direto": f"Seria obra ou serviço do governo federal; o plano não indica se "
                                       f"alcançaria {cidade}.",
        "nao_especificado": f"O plano não detalha como esta medida chegaria aos municípios; não é possível "
                            f"estimar o efeito em {cidade}.",
    }.get(canal, "")


@router.get("/candidatos/comparar", tags=["candidatos"])
def comparar_candidatos(
    categoria: Annotated[str, Query(description="tema (ex.: saude, educacao, impostos)")],
    perfil: PerfilQuery = None,
    municipio: Annotated[int | None, Query(ge=1000000, le=9999999, description="código IBGE do município")] = None,
    turno2: bool = False,
    todos: bool = False,
) -> dict:
    """Mesma pergunta, para todos os candidatos, em ordem alfabética e com o mesmo formato."""
    categoria_ok = _validar_categoria(categoria)
    if categoria_ok is None:
        raise HTTPException(status_code=422, detail="Informe uma categoria.")
    perfil = _validar_perfil(perfil)

    cidade = contexto_municipio(municipio) if municipio else None
    if municipio and not cidade:
        raise HTTPException(status_code=404, detail="Município não encontrado no IBGE.")
    nome_cidade = cidade["nome"] if cidade else None

    candidatos = db.consultar(
        f"""
        SELECT c.sq_candidato, c.nome_urna, c.numero, c.sigla_partido, c.situacao_candidatura, c.totalizacao, c.vice,
               p.id AS plano_id, p.status AS plano_status, p.url_pdf, t.mencionado, t.resumo_neutro
          FROM candidatos c
          LEFT JOIN planos_governo p ON p.sq_candidato = c.sq_candidato
          LEFT JOIN temas_candidato t ON t.plano_id = p.id AND t.categoria_id = %(categoria)s
         WHERE {_FILTRO_CANDIDATOS}
         ORDER BY c.nome_urna
        """,
        {"ano": cfg.tse_ano, "todos": todos, "turno2": turno2, "categoria": categoria_ok},
    )
    plano_ids = [c["plano_id"] for c in candidatos if c["plano_id"]]
    propostas = db.consultar(
        """
        SELECT id, plano_id, descricao, trecho_literal, pagina, tem_meta_numerica, tem_prazo,
               tem_fonte_recurso, canal_execucao, perfis_beneficiados,
               (%(perfil)s::text IS NULL OR %(perfil)s::text = ANY (perfis_beneficiados)
                OR 'todos' = ANY (perfis_beneficiados)) AS relevante_para_perfil
          FROM propostas_candidato
         WHERE plano_id = ANY (%(planos)s) AND categoria_id = %(categoria)s
         ORDER BY id
        """,
        {"planos": plano_ids, "categoria": categoria_ok, "perfil": perfil},
    ) if plano_ids else []

    por_plano: dict[int, list[dict]] = defaultdict(list)
    for p in propostas:
        por_plano[p["plano_id"]].append(p)

    itens = []
    for c in candidatos:
        todas = por_plano.get(c["plano_id"], [])
        exibidas = [p for p in todas if p["relevante_para_perfil"]]
        itens.append({
            **_candidato_json(c),
            "plano_status": c["plano_status"] or "sem_plano",
            "url_pdf": c["url_pdf"],
            "mencionado": c["mencionado"],
            "resumo": c["resumo_neutro"],
            "propostas_no_tema": len(todas),
            "propostas_ocultas_pelo_perfil": len(todas) - len(exibidas),
            "propostas": [{
                "descricao": p["descricao"], "trecho_literal": p["trecho_literal"], "pagina": p["pagina"],
                "especifica": {"meta_numerica": p["tem_meta_numerica"], "prazo": p["tem_prazo"],
                               "fonte_de_recurso": p["tem_fonte_recurso"]},
                "canal": p["canal_execucao"], "perfis": p["perfis_beneficiados"],
                "maturidade": maturidade.avaliar_proposta(p),
                "como_chega_na_cidade": _como_chega(p["canal_execucao"], nome_cidade) if nome_cidade else None,
            } for p in exibidas],
        })

    return {
        "categoria": categoria_ok, "perfil": perfil, "municipio": cidade,
        "ordenacao": "alfabética por nome de urna (sem ranking)",
        "avisos": [AVISO_IA, AVISO_LOCAL],
        "candidatos": itens,
    }


# Termos da busca por tema (sem IA): mesma lista usada para agrupar os atos (backend/temas.py),
# na sintaxe do websearch_to_tsquery ("a or b"), iguais para todos os candidatos.
TERMOS_TEMA: dict[str, str] = {tema: consulta_tsquery(tema) for tema in TERMOS_POR_TEMA}
AVISO_BUSCA = ("Busca por palavra-chave no texto integral dos planos, sem inteligência artificial e sem "
               "interpretação: são mostrados os trechos literais com maior relevância, na mesma regra para "
               "todos os candidatos. A quantidade de trechos não indica prioridade nem qualidade do plano.")
_OPCOES_TRECHO = 'StartSel="⟦", StopSel="⟧", MaxWords=45, MinWords=20, MaxFragments=1, ShortWord=2'


@router.get("/candidatos/buscar", tags=["candidatos"])
def buscar_nos_planos(
    q: Annotated[str | None, Query(max_length=100, description="palavra(s) a procurar nos planos")] = None,
    categoria: Annotated[str | None, Query(description="tema: busca pelos termos padrão do tema")] = None,
    trechos: Annotated[int, Query(ge=1, le=10, description="trechos por candidato")] = 5,
    turno2: bool = False,
    todos: bool = False,
) -> dict:
    """Trechos literais dos planos que mencionam a palavra ou o tema (sem IA).

    Igual para todos: mesma consulta, mesmo número de trechos, ordem alfabética.
    Os `trecho` vêm com ⟦marcadores⟧ nas palavras encontradas (o cliente deve escapar o HTML).
    """
    categoria = _validar_categoria(categoria)
    termo = (q or "").strip()
    if termo:
        consulta, modo = termo, "palavra"
    elif categoria in TERMOS_TEMA:
        consulta, modo = TERMOS_TEMA[categoria], "tema"
    else:
        raise HTTPException(status_code=422,
                            detail="Informe uma palavra (q) ou um tema que tenha termos de busca padrão.")

    linhas = db.consultar(
        f"""
        WITH q AS (SELECT websearch_to_tsquery('pt_busca', %(consulta)s) AS tsq)
        SELECT c.sq_candidato, c.nome_urna, c.numero, c.sigla_partido, c.situacao_candidatura,
               c.totalizacao, c.vice,
               p.id AS plano_id, p.status AS plano_status, p.url_pdf, p.paginas,
               (SELECT count(*) FROM paginas_plano x WHERE x.plano_id = p.id) AS n_texto,
               r.pagina, r.trecho
          FROM candidatos c
          LEFT JOIN planos_governo p ON p.sq_candidato = c.sq_candidato
          LEFT JOIN LATERAL (
                SELECT g.pagina, ts_headline('pt_busca', g.texto, q.tsq, %(opcoes)s) AS trecho
                  FROM paginas_plano g, q
                 WHERE g.plano_id = p.id AND NOT g.indice AND g.busca @@ q.tsq
                 ORDER BY ts_rank_cd(g.busca, q.tsq) DESC, g.pagina
                 LIMIT %(n)s
          ) r ON true
         WHERE {_FILTRO_CANDIDATOS}
         ORDER BY c.nome_urna, r.pagina
        """,
        {"consulta": consulta, "opcoes": _OPCOES_TRECHO, "n": trechos,
         "ano": cfg.tse_ano, "todos": todos, "turno2": turno2},
    )

    por_candidato: dict[int, dict] = {}
    for r in linhas:
        item = por_candidato.setdefault(r["sq_candidato"], {
            **_candidato_json(r),
            "plano_status": r["plano_status"] or "sem_plano",
            "url_pdf": r["url_pdf"],
            "paginas_do_plano": r["paginas"],
            "texto_disponivel": bool(r["n_texto"]),
            "trechos": [],
        })
        if r["pagina"] is not None:
            item["trechos"].append({"pagina": r["pagina"], "trecho": r["trecho"]})

    # Diagnóstico objetivo do tema: em quantas páginas que tratam dele há meta, prazo, fonte e executor.
    plano_ids = [i["plano_id"] for i in linhas if i["plano_id"]]
    paginas_tema = db.consultar(
        "SELECT g.plano_id, g.texto FROM paginas_plano g "
        "WHERE g.plano_id = ANY (%(planos)s) AND NOT g.indice "
        "AND g.busca @@ websearch_to_tsquery('pt_busca', %(consulta)s)",
        {"planos": plano_ids, "consulta": consulta}) if plano_ids else []
    textos: dict[int, list[str]] = defaultdict(list)
    for r in paginas_tema:
        textos[r["plano_id"]].append(r["texto"])
    plano_de = {i["sq_candidato"]: i["plano_id"] for i in linhas}
    for sq, item in por_candidato.items():
        item["diagnostico"] = maturidade.contar_paginas(textos.get(plano_de[sq], []))

    return {"consulta": termo or consulta, "modo": modo, "categoria": categoria,
            "ordenacao": "alfabética por nome de urna (sem ranking)", "aviso": AVISO_BUSCA,
            "regua": maturidade.EXPLICACAO,
            "candidatos": list(por_candidato.values())}


AVISO_CAPITULOS = ("Capítulos tirados do sumário do próprio plano, sem inteligência artificial: cada um mostra a "
                   "abertura literal do texto e os temas que aparecem nas suas páginas (mesmas palavras-chave "
                   "para todos os candidatos). Não é um resumo interpretado; confira o documento original.")


@router.get("/candidatos/{sq_candidato}/capitulos", tags=["candidatos"])
def capitulos_do_candidato(sq_candidato: int) -> dict:
    """Capítulos do plano (pelo sumário do PDF), com abertura literal e temas tocados — sem IA."""
    plano = db.consultar_um(
        "SELECT id, url_pdf FROM planos_governo WHERE sq_candidato = %s ORDER BY id DESC LIMIT 1", (sq_candidato,))
    if not plano:
        raise HTTPException(status_code=404, detail="Este candidato não tem plano registrado.")
    return {**capitulos.capitulos_do_plano(plano["id"]), "url_pdf": plano["url_pdf"], "aviso": AVISO_CAPITULOS,
            "regua": maturidade.EXPLICACAO}


@router.get("/candidatos/{sq_candidato}", tags=["candidatos"])
def obter_candidato(sq_candidato: int) -> dict:
    cand = db.consultar_um(
        """
        SELECT c.*, p.id AS plano_id, p.status AS plano_status, p.url_pdf, p.paginas, p.resumo_geral,
               p.analisado_em, p.modelo_llm, p.propostas_descartadas
          FROM candidatos c LEFT JOIN planos_governo p ON p.sq_candidato = c.sq_candidato
         WHERE c.sq_candidato = %s
        """,
        (sq_candidato,),
    )
    if not cand:
        raise HTTPException(status_code=404, detail="Candidato não encontrado.")
    temas, propostas = [], []
    if cand["plano_id"]:
        temas = db.consultar(
            "SELECT t.categoria_id, c.nome, t.mencionado, t.resumo_neutro FROM temas_candidato t "
            "JOIN categorias c ON c.id = t.categoria_id WHERE t.plano_id = %s ORDER BY c.ordem",
            (cand["plano_id"],),
        )
        propostas = db.consultar(
            "SELECT categoria_id, descricao, trecho_literal, pagina, tem_meta_numerica, tem_prazo, "
            "tem_fonte_recurso, canal_execucao, perfis_beneficiados FROM propostas_candidato "
            "WHERE plano_id = %s ORDER BY id",
            (cand["plano_id"],),
        )
    return {
        **_candidato_json(cand),
        "plano": {"status": cand["plano_status"] or "sem_plano", "url_pdf": cand["url_pdf"],
                  "paginas": cand["paginas"], "resumo_geral": cand["resumo_geral"],
                  "analisado_em": cand["analisado_em"].isoformat() if cand["analisado_em"] else None,
                  "modelo": cand["modelo_llm"], "citacoes_descartadas": cand["propostas_descartadas"]},
        "temas": temas, "propostas": propostas, "aviso": AVISO_IA,
    }


app.include_router(router)
app.include_router(editor.router)

# Aplicativo web (HTML estático) servido na raiz — registrado por último para não
# sombrear as rotas /api.
if cfg.pasta_frontend.exists():
    app.mount("/", StaticFiles(directory=cfg.pasta_frontend, html=True), name="frontend")
