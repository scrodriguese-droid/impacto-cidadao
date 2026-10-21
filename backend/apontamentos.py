"""Apontamentos do editor sobre os planos de governo, por tópico fixo dentro de cada eixo temático.

Cada apontamento é uma análise AUTORAL (assinada por um editor com conta): candidato + eixo + tópico, uma etiqueta
de status, o texto da análise e a citação do PDF que a sustenta. Existe no máximo um por (candidato, eixo, tópico),
o que permite alinhar os candidatos tópico a tópico na tela.

Nasce como rascunho (só o autor vê) e pode ser publicado, como os post-its. Vive no mesmo banco do editor
(SQLite por padrão; PostgreSQL com EDITOR_BANCO=postgres), com as mesmas regras de sessão e de origem.
"""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

import editor
from schemas import CATEGORIAS

LIMITE_TEXTO = 4000
LIMITE_CITACAO = 3000

# Tópicos fixos, na ordem em que aparecem. Mudar a lista muda a tela, sem mexer no banco.
TOPICOS: dict[str, str] = {
    "metas": "Metas",
    "financiamento": "Financiamento",
    "viabilidade": "Viabilidade",
    "competencia": "Competência",
}
STATUS: dict[str, str] = {"objetiva": "Meta objetiva", "parcial": "Parcial", "generica": "Genérica"}

router = APIRouter(prefix="/api", tags=["apontamentos"])


class ApontamentoIn(BaseModel):
    sq_candidato: int = Field(gt=0)
    eixo: str
    topico: str
    status: str
    texto: str = Field(min_length=1, max_length=LIMITE_TEXTO)
    citacao: str = Field(default="", max_length=LIMITE_CITACAO)
    pagina_pdf: int | None = Field(default=None, ge=1, le=2000)
    publico: bool = False

    @field_validator("eixo")
    @classmethod
    def _eixo(cls, v: str) -> str:
        if v not in CATEGORIAS or v == "outros":
            raise ValueError("Eixo inválido.")
        return v

    @field_validator("topico")
    @classmethod
    def _topico(cls, v: str) -> str:
        if v not in TOPICOS:
            raise ValueError(f"Tópico inválido. Use um de: {', '.join(TOPICOS)}.")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in STATUS:
            raise ValueError(f"Status inválido. Use um de: {', '.join(STATUS)}.")
        return v

    @field_validator("texto", "citacao")
    @classmethod
    def _limpar(cls, v: str) -> str:
        return v.strip()

    @field_validator("texto")
    @classmethod
    def _nao_vazio(cls, v: str) -> str:
        if not v:
            raise ValueError("A análise está vazia.")
        return v


_SELECT = "SELECT a.*, e.nome AS autor FROM apontamentos a JOIN editores e ON e.id = a.editor_id"


def _json(l: sqlite3.Row, meu_id: int | None) -> dict:
    return {"id": l["id"], "sq_candidato": l["sq_candidato"], "eixo": l["eixo"], "topico": l["topico"],
            "status": l["status"], "rotulo_status": STATUS[l["status"]], "texto": l["texto"],
            "citacao": l["citacao"], "pagina_pdf": l["pagina_pdf"], "publico": bool(l["publico"]),
            "autor": l["autor"], "atualizado_em": l["atualizado_em"],
            "meu": meu_id is not None and l["editor_id"] == meu_id}


@router.get("/apontamentos")
def listar(request: Request, eixo: Annotated[str, Query()],
           candidatos: Annotated[str | None, Query(description="sq_candidato separados por vírgula")] = None) -> dict:
    if eixo not in CATEGORIAS or eixo == "outros":
        raise HTTPException(status_code=422, detail="Eixo inválido.")
    ids: list[int] = []
    if candidatos:
        try:
            ids = [int(x) for x in candidatos.split(",") if x.strip()][:10]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Candidatos inválidos.") from exc
    e = editor.editor_atual(request)
    meu = e["id"] if e else None
    sql = f"{_SELECT} WHERE a.eixo = ? AND (a.publico = 1 OR a.editor_id = ?)"
    params: list = [eixo, meu if meu is not None else -1]
    if ids:
        sql += f" AND a.sq_candidato IN ({','.join('?' * len(ids))})"
        params += ids
    with editor._db() as c:
        linhas = c.execute(sql + " ORDER BY a.sq_candidato, a.id", tuple(params)).fetchall()
    return {"eixo": eixo, "topicos": [{"id": k, "nome": v} for k, v in TOPICOS.items()],
            "status": [{"id": k, "nome": v} for k, v in STATUS.items()],
            "itens": [_json(l, meu) for l in linhas]}


@router.post("/apontamentos", dependencies=[Depends(editor.mesma_origem)])
def criar(dados: ApontamentoIn, e: sqlite3.Row = Depends(editor.exigir_editor)) -> dict:
    agora = editor._agora()
    with editor._db() as c:
        if c.execute("SELECT 1 FROM apontamentos WHERE sq_candidato = ? AND eixo = ? AND topico = ?",
                     (dados.sq_candidato, dados.eixo, dados.topico)).fetchone():
            raise HTTPException(status_code=409, detail="Já existe um apontamento para este candidato, eixo e tópico. Edite o existente.")
        aid = c.execute(
            "INSERT INTO apontamentos (sq_candidato, eixo, topico, status, texto, citacao, pagina_pdf, publico, "
            "editor_id, criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (dados.sq_candidato, dados.eixo, dados.topico, dados.status, dados.texto, dados.citacao,
             dados.pagina_pdf, int(dados.publico), e["id"], agora, agora)).lastrowid
        l = c.execute(f"{_SELECT} WHERE a.id = ?", (aid,)).fetchone()
    return _json(l, e["id"])


def _do_autor(c, aid: int, e: sqlite3.Row) -> sqlite3.Row:
    l = c.execute(f"{_SELECT} WHERE a.id = ?", (aid,)).fetchone()
    if not l or (l["editor_id"] != e["id"] and not l["publico"]):
        raise HTTPException(status_code=404, detail="Apontamento não encontrado.")   # rascunho alheio não revela que existe
    if l["editor_id"] != e["id"]:
        raise HTTPException(status_code=403, detail="Só o autor altera este apontamento.")
    return l


@router.put("/apontamentos/{aid}", dependencies=[Depends(editor.mesma_origem)])
def editar(aid: int, dados: ApontamentoIn, e: sqlite3.Row = Depends(editor.exigir_editor)) -> dict:
    with editor._db() as c:
        antigo = _do_autor(c, aid, e)
        if (dados.sq_candidato, dados.eixo, dados.topico) != (antigo["sq_candidato"], antigo["eixo"], antigo["topico"]):
            raise HTTPException(status_code=422, detail="Candidato, eixo e tópico não mudam: apague e crie outro.")
        c.execute("UPDATE apontamentos SET status = ?, texto = ?, citacao = ?, pagina_pdf = ?, publico = ?, "
                  "atualizado_em = ? WHERE id = ?",
                  (dados.status, dados.texto, dados.citacao, dados.pagina_pdf, int(dados.publico), editor._agora(), aid))
        l = c.execute(f"{_SELECT} WHERE a.id = ?", (aid,)).fetchone()
    return _json(l, e["id"])


@router.delete("/apontamentos/{aid}", dependencies=[Depends(editor.mesma_origem)])
def apagar(aid: int, e: sqlite3.Row = Depends(editor.exigir_editor)) -> dict:
    with editor._db() as c:
        _do_autor(c, aid, e)
        c.execute("DELETE FROM apontamentos WHERE id = ?", (aid,))
    return {"apagado": aid}
