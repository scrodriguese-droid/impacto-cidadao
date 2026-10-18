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


# --------------------------------------------------------------------------- #
# Convites: criar a própria conta pelo navegador, sem cadastro aberto
# --------------------------------------------------------------------------- #
def registrar(app, codigo, email="nova@exemplo.org", senha="senha-nova-bem-longa", nome="Nova"):
    c = TestClient(app)
    return c, c.post("/api/editor/registrar", json={"codigo": codigo, "email": email, "nome": nome, "senha": senha})


def test_convite_cria_conta_e_ja_entra(app):
    codigo = editor.criar_convite()
    c, r = registrar(app, codigo.lower())               # aceita minúsculas
    assert r.status_code == 200 and r.json()["nome"] == "Nova"
    assert c.get("/api/editor/eu").json()["nome"] == "Nova"
    assert entrar(app, "nova@exemplo.org", "senha-nova-bem-longa")[1].status_code == 200


def test_convite_vale_uma_vez_so(app):
    codigo = editor.criar_convite()
    assert registrar(app, codigo)[1].status_code == 200
    assert registrar(app, codigo, email="outra@exemplo.org")[1].status_code == 403


def test_convite_vencido_ou_falso_e_recusado(app):
    vencido = editor.criar_convite(minutos=-1)
    assert registrar(app, vencido)[1].status_code == 403
    assert registrar(app, "AAAA-BBBB-CCCC")[1].status_code == 403
    assert registrar(app, "curto")[1].status_code == 403


def test_erro_de_validacao_nao_gasta_o_convite(app):
    codigo = editor.criar_convite()
    assert registrar(app, codigo, senha="curta")[1].status_code == 422
    assert registrar(app, codigo, email="ana@exemplo.org")[1].status_code == 422     # e-mail já existe
    assert registrar(app, codigo)[1].status_code == 200                               # o convite continua valendo


def test_chutes_de_convite_sao_bloqueados(app):
    for _ in range(5):
        assert registrar(app, "AAAA-BBBB-CCCC")[1].status_code == 403
    assert registrar(app, editor.criar_convite())[1].status_code == 429


def test_convite_so_pelo_mesmo_site(app):
    c = TestClient(app)
    r = c.post("/api/editor/registrar", json={"codigo": editor.criar_convite(), "email": "x@exemplo.org", "nome": "X", "senha": "senha-nova-bem-longa"},
               headers={"Origin": "https://site-malicioso.example"})
    assert r.status_code == 403
