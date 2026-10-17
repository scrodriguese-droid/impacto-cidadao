"""Contas de editor e post-its: senhas, sessão, permissões, bloqueio de tentativas e origem das requisições."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import editor


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, "ARQUIVO_DB", tmp_path / "editor.db")
    monkeypatch.setattr(editor, "_falhas", editor._Falhas(maximo=5, janela_s=600))
    a = FastAPI()
    a.include_router(editor.router)
    editor.criar_editor("ana@exemplo.org", "Ana", "senha-bem-longa-1")
    editor.criar_editor("bia@exemplo.org", "Bia", "outra-senha-longa-2")
    return a


def entrar(app, email="ana@exemplo.org", senha="senha-bem-longa-1"):
    c = TestClient(app)
    r = c.post("/api/editor/entrar", json={"email": email, "senha": senha})
    return c, r


def test_hash_de_senha_nao_guarda_a_senha_e_confere():
    h = editor.hash_senha("minha-senha-forte")
    assert "minha-senha-forte" not in h and h.startswith("scrypt$")
    assert editor.verificar_senha("minha-senha-forte", h)
    assert not editor.verificar_senha("outra", h)
    assert editor.hash_senha("minha-senha-forte") != h        # sal diferente a cada vez
    assert not editor.verificar_senha("x", "lixo")


def test_criar_editor_valida_email_senha_e_duplicidade(app):
    with pytest.raises(ValueError):
        editor.criar_editor("sem-arroba", "X", "senha-bem-longa-1")
    with pytest.raises(ValueError):
        editor.criar_editor("c@exemplo.org", "X", "curta")
    with pytest.raises(ValueError):
        editor.criar_editor("ANA@exemplo.org", "Ana 2", "senha-bem-longa-1")   # e-mail repetido, sem diferenciar caixa


def test_login_certo_define_cookie_httponly_e_sessao_funciona(app):
    c, r = entrar(app)
    assert r.status_code == 200 and r.json() == {"logado": True, "nome": "Ana", "papel": "editor"}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert c.get("/api/editor/eu").json()["nome"] == "Ana"
    assert TestClient(app).get("/api/editor/eu").json()["logado"] is False


def test_login_errado_nao_revela_se_o_email_existe(app):
    _, r1 = entrar(app, senha="errada-errada-1")
    _, r2 = entrar(app, email="ninguem@exemplo.org", senha="errada-errada-1")
    assert r1.status_code == r2.status_code == 401 and r1.json() == r2.json()


def test_bloqueia_apos_cinco_tentativas_erradas(app):
    for _ in range(5):
        assert entrar(app, senha="errada-errada-1")[1].status_code == 401
    assert entrar(app)[1].status_code == 429                     # até a senha certa fica bloqueada por um tempo


def test_sair_encerra_a_sessao(app):
    c, _ = entrar(app)
    assert c.post("/api/editor/sair").json() == {"logado": False}
    assert c.get("/api/editor/eu").json()["logado"] is False


def test_postit_exige_login_e_nasce_como_rascunho(app):
    corpo = {"pagina": "tesouro", "ancora": "#tRegras", "rotulo": "Regras do jogo", "texto": "Minha nota."}
    assert TestClient(app).post("/api/postits", json=corpo).status_code == 401
    c, _ = entrar(app)
    r = c.post("/api/postits", json=corpo)
    assert r.status_code == 200 and r.json()["publico"] is False and r.json()["minha"] is True
    # o visitante não vê rascunho; o autor vê
    assert TestClient(app).get("/api/postits", params={"pagina": "tesouro"}).json()["itens"] == []
    assert len(c.get("/api/postits", params={"pagina": "tesouro"}).json()["itens"]) == 1


def test_publicar_deixa_o_visitante_ver_sem_expor_e_mail(app):
    c, _ = entrar(app)
    pid = c.post("/api/postits", json={"pagina": "inicio", "texto": "Olá", "publico": True}).json()["id"]
    itens = TestClient(app).get("/api/postits", params={"pagina": "inicio"}).json()["itens"]
    assert [i["id"] for i in itens] == [pid] and itens[0]["autor"] == "Ana" and itens[0]["minha"] is False
    assert "email" not in itens[0]


def test_so_o_autor_edita_ou_apaga(app):
    ana, _ = entrar(app)
    pid = ana.post("/api/postits", json={"pagina": "inicio", "texto": "Da Ana", "publico": True}).json()["id"]
    bia, _ = entrar(app, "bia@exemplo.org", "outra-senha-longa-2")
    assert bia.put(f"/api/postits/{pid}", json={"pagina": "inicio", "texto": "Trocado"}).status_code == 403
    assert bia.delete(f"/api/postits/{pid}").status_code == 403
    assert ana.put(f"/api/postits/{pid}", json={"pagina": "inicio", "texto": "Editado", "publico": False}).json()["texto"] == "Editado"
    assert ana.delete(f"/api/postits/{pid}").status_code == 200


def test_rascunho_alheio_nao_revela_que_existe(app):
    ana, _ = entrar(app)
    pid = ana.post("/api/postits", json={"pagina": "inicio", "texto": "Segredo"}).json()["id"]
    bia, _ = entrar(app, "bia@exemplo.org", "outra-senha-longa-2")
    assert bia.delete(f"/api/postits/{pid}").status_code == 404


def test_validacao_de_pagina_e_texto(app):
    c, _ = entrar(app)
    assert c.post("/api/postits", json={"pagina": "../x", "texto": "a"}).status_code == 422
    assert c.post("/api/postits", json={"pagina": "inicio", "texto": "   "}).status_code == 422
    assert c.post("/api/postits", json={"pagina": "inicio", "texto": "a" * 2001}).status_code == 422
    assert c.get("/api/postits", params={"pagina": "A B"}).status_code == 422


def test_escrita_de_outro_site_e_recusada(app):
    c, _ = entrar(app)
    r = c.post("/api/postits", json={"pagina": "inicio", "texto": "x"}, headers={"Origin": "https://site-malicioso.example"})
    assert r.status_code == 403
    r = c.post("/api/editor/entrar", json={"email": "ana@exemplo.org", "senha": "senha-bem-longa-1"}, headers={"Origin": "https://site-malicioso.example"})
    assert r.status_code == 403
