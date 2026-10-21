"""Apontamentos do editor: validação, rascunho x publicado, autoria e unicidade por (candidato, eixo, tópico)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import apontamentos
import editor

BASE = {"sq_candidato": 280002551544, "eixo": "saude", "topico": "metas", "status": "parcial",
        "texto": "Fixa meta de leitos, mas sem prazo.", "citacao": "ampliar leitos", "pagina_pdf": 12}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, "ARQUIVO_DB", tmp_path / "editor.db")
    monkeypatch.setattr(editor, "_falhas", editor._Falhas(maximo=5, janela_s=600))
    if editor._usa_postgres():
        with editor._db() as c:
            c.execute("TRUNCATE apontamentos, postits, sessoes, convites, editores RESTART IDENTITY CASCADE")
    a = FastAPI()
    a.include_router(editor.router)
    a.include_router(apontamentos.router)
    editor.criar_editor("ana@exemplo.org", "Ana", "senha-bem-longa-1")
    editor.criar_editor("bia@exemplo.org", "Bia", "outra-senha-longa-2")
    return a


def entrar(app, email="ana@exemplo.org", senha="senha-bem-longa-1"):
    c = TestClient(app)
    assert c.post("/api/editor/entrar", json={"email": email, "senha": senha}).status_code == 200
    return c


def test_exige_conta_de_editor_para_escrever(app):
    assert TestClient(app).post("/api/apontamentos", json=BASE).status_code == 401


def test_valida_eixo_topico_e_status(app):
    c = entrar(app)
    for campo, valor in (("eixo", "outros"), ("eixo", "nada"), ("topico", "x"), ("status", "vermelho")):
        assert c.post("/api/apontamentos", json={**BASE, campo: valor}).status_code == 422
    assert c.post("/api/apontamentos", json={**BASE, "texto": "   "}).status_code == 422


def test_rascunho_so_o_autor_ve_e_publicar_abre_para_todos(app):
    ana = entrar(app)
    r = ana.post("/api/apontamentos", json=BASE)
    assert r.status_code == 200 and r.json()["publico"] is False and r.json()["rotulo_status"] == "Parcial"
    url = "/api/apontamentos?eixo=saude"
    assert len(ana.get(url).json()["itens"]) == 1
    assert TestClient(app).get(url).json()["itens"] == []
    aid = r.json()["id"]
    assert ana.put(f"/api/apontamentos/{aid}", json={**BASE, "publico": True}).json()["publico"] is True
    visto = TestClient(app).get(url).json()
    assert [i["autor"] for i in visto["itens"]] == ["Ana"] and visto["itens"][0]["meu"] is False
    assert [t["id"] for t in visto["topicos"]] == ["metas", "financiamento", "viabilidade", "competencia"]


def test_um_por_candidato_eixo_e_topico(app):
    c = entrar(app)
    assert c.post("/api/apontamentos", json=BASE).status_code == 200
    assert c.post("/api/apontamentos", json=BASE).status_code == 409
    assert c.post("/api/apontamentos", json={**BASE, "topico": "financiamento"}).status_code == 200


def test_so_o_autor_edita_ou_apaga_e_rascunho_alheio_nao_existe(app):
    ana, bia = entrar(app), entrar(app, "bia@exemplo.org", "outra-senha-longa-2")
    aid = ana.post("/api/apontamentos", json=BASE).json()["id"]
    assert bia.put(f"/api/apontamentos/{aid}", json=BASE).status_code == 404
    assert bia.delete(f"/api/apontamentos/{aid}").status_code == 404
    ana.put(f"/api/apontamentos/{aid}", json={**BASE, "publico": True})
    assert bia.put(f"/api/apontamentos/{aid}", json=BASE).status_code == 403
    assert ana.delete(f"/api/apontamentos/{aid}").json() == {"apagado": aid}


def test_chave_nao_muda_na_edicao_e_filtra_por_candidato(app):
    c = entrar(app)
    aid = c.post("/api/apontamentos", json={**BASE, "publico": True}).json()["id"]
    assert c.put(f"/api/apontamentos/{aid}", json={**BASE, "topico": "viabilidade"}).status_code == 422
    c.post("/api/apontamentos", json={**BASE, "sq_candidato": 5, "publico": True})
    itens = TestClient(app).get("/api/apontamentos?eixo=saude&candidatos=5").json()["itens"]
    assert [i["sq_candidato"] for i in itens] == [5]
