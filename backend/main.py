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
import logging
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import db
import simuladores
from config import cfg
from fontes import buscar_municipios, contexto_municipio
from schemas import CATEGORIAS, PERFIS, STATUS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("impacto.api")

PerfilQuery = Annotated[str | None, Query(description="id do perfil (ex.: clt, mei, aposentado)")]


# --------------------------------------------------------------------------- #
# Aplicação
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        db.abrir_pool()
    except Exception as exc:  # noqa: BLE001
        log.error("Banco indisponível na inicialização: %s", exc)
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
        "titulo": r["titulo_simples"],
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


_COLUNAS_PROPOSICAO = """
    p.id, p.identificacao, p.sigla_tipo, p.titulo_simples, p.resumo_cidadao, p.perfis_impactados,
    p.categorias, p.impacto_pratico, p.ponto_positivo, p.ponto_atencao, p.status_tramitacao,
    p.relevancia_bolso, p.data_apresentacao, p.origem, p.ementa_oficial, p.url_fonte,
    p.linha_do_tempo, p.texto_fonte,
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
    pagina: Annotated[int, Query(ge=1, le=1000)] = 1,
    por_pagina: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
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
    sql = f"""
        SELECT {_COLUNAS_PROPOSICAO}, count(*) OVER() AS total_geral
          FROM proposicoes_analisadas p
          LEFT JOIN vw_placar_votos v ON v.proposicao_id = p.id
         WHERE p.analise_status = 'ok'
           AND (%(perfil)s::text IS NULL OR %(perfil)s::text = ANY (p.perfis_impactados) OR 'todos' = ANY (p.perfis_impactados))
           AND (%(categoria)s::text IS NULL OR %(categoria)s::text = ANY (p.categorias))
           AND (%(status)s::text IS NULL OR p.status_tramitacao = %(status)s::text)
           AND (%(q)s::text IS NULL
                OR p.busca @@ websearch_to_tsquery('portuguese', %(q)s::text)
                OR p.identificacao ILIKE '%%' || %(q_like)s::text || '%%')
         ORDER BY {ordenacao}
         LIMIT %(limite)s OFFSET %(deslocamento)s
    """
    linhas = db.consultar(sql, {
        "perfil": perfil, "categoria": categoria, "status": status, "q": termo,
        "q_like": _escapar_like(termo) if termo else None,
        "limite": por_pagina, "deslocamento": (pagina - 1) * por_pagina,
    })
    total = linhas[0]["total_geral"] if linhas else 0
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina,
            "itens": [_proposicao_json(r) for r in linhas]}


@router.get("/proposicoes/{proposicao_id}", tags=["proposicoes"])
def obter_proposicao(proposicao_id: int) -> dict:
    linha = db.consultar_um(
        f"SELECT {_COLUNAS_PROPOSICAO} FROM proposicoes_analisadas p "
        "LEFT JOIN vw_placar_votos v ON v.proposicao_id = p.id "
        "WHERE p.id = %s AND p.analise_status = 'ok'",
        (proposicao_id,),
    )
    if not linha:
        raise HTTPException(status_code=404, detail="Proposição não encontrada.")
    return _proposicao_json(linha)


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
    existe = db.consultar_um(
        "SELECT 1 AS ok FROM proposicoes_analisadas WHERE id = %s AND analise_status = 'ok'", (proposicao_id,)
    )
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
    return {"eleicao": cfg.tse_ano, "ordenacao": "alfabética por nome de urna (sem ranking)",
            "aviso": AVISO_IA, "itens": itens}


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
                "como_chega_na_cidade": _como_chega(p["canal_execucao"], nome_cidade) if nome_cidade else None,
            } for p in exibidas],
        })

    return {
        "categoria": categoria_ok, "perfil": perfil, "municipio": cidade,
        "ordenacao": "alfabética por nome de urna (sem ranking)",
        "avisos": [AVISO_IA, AVISO_LOCAL],
        "candidatos": itens,
    }


# Termos usados na busca por tema (sem IA). Sintaxe do websearch_to_tsquery: "a or b".
# São palavras-chave deliberadamente simples e iguais para todos os candidatos.
TERMOS_TEMA: dict[str, str] = {
    "impostos": "imposto or tributo or tributária or isenção or alíquota or \"imposto de renda\"",
    "economia": "inflação or juros or crédito or fiscal or \"contas públicas\" or dívida or câmbio",
    "emprego": "emprego or trabalho or salário or desemprego or trabalhador or empreendedor",
    "previdencia": "previdência or aposentadoria or aposentado or INSS or pensão or idoso",
    "assistencia": "\"bolsa família\" or assistência or pobreza or fome or \"transferência de renda\" or BPC",
    "saude": "saúde or SUS or hospital or médico or medicamento or vacina",
    "educacao": "educação or escola or creche or professor or universidade or ensino",
    "seguranca": "segurança or polícia or crime or violência or facção or presídio",
    "habitacao": "moradia or habitação or saneamento or \"minha casa\" or esgoto",
    "transporte": "transporte or ferrovia or rodovia or porto or aeroporto or mobilidade or infraestrutura",
    "meio_ambiente": "ambiente or clima or desmatamento or amazônia or sustentável or carbono",
    "agro": "agropecuária or agricultura or agricultor or rural or safra or pecuária",
}
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

    return {"consulta": termo or consulta, "modo": modo, "categoria": categoria,
            "ordenacao": "alfabética por nome de urna (sem ranking)", "aviso": AVISO_BUSCA,
            "candidatos": list(por_candidato.values())}


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

# Aplicativo web (HTML estático) servido na raiz — registrado por último para não
# sombrear as rotas /api.
if cfg.pasta_frontend.exists():
    app.mount("/", StaticFiles(directory=cfg.pasta_frontend, html=True), name="frontend")
