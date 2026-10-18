"""Contas de editores e post-its.

Quem entra com uma conta é classificado como **editor** e pode colar post-its (notas) nas páginas. Cada post-it
nasce como rascunho, visível só para o autor, e pode ser publicado para todos os visitantes.

Desenho, em poucas linhas:
  * Não há cadastro aberto: as contas são criadas por quem administra o servidor, pelo terminal
    (`python backend/editor.py criar --email ... --nome ...`), onde a senha é digitada sem eco. Assim ninguém
    vira editor sozinho e nenhuma senha passa por arquivo, chat ou URL.
  * Senhas ficam como hash scrypt com sal próprio. A sessão é um token aleatório num cookie HttpOnly e SameSite=Lax;
    no banco guardamos só o SHA-256 do token, então um vazamento do arquivo não entrega sessões válidas.
  * Tentativas de entrar erradas bloqueiam por um tempo (por IP e por e-mail). Escritas conferem a origem da
    requisição, além do SameSite, contra pedidos vindos de outros sites.
  * Os dados vão para um SQLite próprio (dados/editor/editor.db, fora do git), independente do PostgreSQL do restante
    do app, para a edição funcionar mesmo com o banco principal desligado.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import RAIZ  # noqa: E402

log = logging.getLogger("impacto.editor")

ARQUIVO_DB = RAIZ / "dados" / "editor" / "editor.db"
COOKIE = "ic_sessao"
VALIDADE_DIAS = 14
MIN_SENHA = 10
PAPEIS = ("editor",)
LIMITE_TEXTO = 8000          # caracteres por post-it
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
_inicializado: set[str] = set()
_trava = threading.Lock()


# --------------------------------------------------------------------------- #
# Banco (SQLite)
# --------------------------------------------------------------------------- #
def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    ARQUIVO_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ARQUIVO_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        chave = str(ARQUIVO_DB)
        if chave not in _inicializado:
            with _trava:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS editores (
                        id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, nome TEXT NOT NULL,
                        papel TEXT NOT NULL DEFAULT 'editor', senha_hash TEXT NOT NULL, criado_em TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS sessoes (
                        token_hash TEXT PRIMARY KEY, editor_id INTEGER NOT NULL REFERENCES editores(id) ON DELETE CASCADE,
                        criado_em TEXT NOT NULL, expira_em TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS postits (
                        id INTEGER PRIMARY KEY, pagina TEXT NOT NULL, ancora TEXT NOT NULL DEFAULT '', rotulo TEXT NOT NULL DEFAULT '',
                        texto TEXT NOT NULL, publico INTEGER NOT NULL DEFAULT 0,
                        editor_id INTEGER NOT NULL REFERENCES editores(id) ON DELETE CASCADE,
                        criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS ix_postits_pagina ON postits(pagina);
                    CREATE TABLE IF NOT EXISTS convites (
                        codigo_hash TEXT PRIMARY KEY, papel TEXT NOT NULL DEFAULT 'editor',
                        criado_em TEXT NOT NULL, expira_em TEXT NOT NULL, usado_em TEXT);
                """)
                _inicializado.add(chave)
        yield conn
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Senhas e sessões
# --------------------------------------------------------------------------- #
def hash_senha(senha: str) -> str:
    sal = os.urandom(16)
    h = hashlib.scrypt(senha.encode("utf-8"), salt=sal, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${sal.hex()}${h.hex()}"


def verificar_senha(senha: str, guardado: str) -> bool:
    try:
        _, n, r, p, sal, esperado = guardado.split("$")
        h = hashlib.scrypt(senha.encode("utf-8"), salt=bytes.fromhex(sal), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(h.hex(), esperado)
    except (ValueError, TypeError):
        return False


_HASH_FALSO = hash_senha("senha-que-ninguem-tem-" + secrets.token_hex(8))   # igual custo quando o e-mail não existe


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def criar_editor(email: str, nome: str, senha: str, papel: str = "editor") -> int:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise ValueError("E-mail inválido.")
    if not nome.strip():
        raise ValueError("Informe o nome.")
    if len(senha) < MIN_SENHA:
        raise ValueError(f"A senha precisa ter pelo menos {MIN_SENHA} caracteres.")
    if papel not in PAPEIS:
        raise ValueError("Papel inválido.")
    with _db() as c:
        if c.execute("SELECT 1 FROM editores WHERE email = ?", (email,)).fetchone():
            raise ValueError("Já existe uma conta com este e-mail.")
        return c.execute("INSERT INTO editores (email, nome, papel, senha_hash, criado_em) VALUES (?,?,?,?,?)",
                         (email, nome.strip(), papel, hash_senha(senha), _agora())).lastrowid


# --------------------------------------------------------------------------- #
# Convites: o administrador gera um código de uso único; quem o recebe cria a própria conta (e a própria senha) pelo
# navegador. Assim a senha nunca passa por terminal alheio, chat ou arquivo, e continua sem haver cadastro aberto.
# --------------------------------------------------------------------------- #
VALIDADE_CONVITE_MIN = 60
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # sem I, O, 0, 1: não se confunde ao digitar


def criar_convite(papel: str = "editor", minutos: int = VALIDADE_CONVITE_MIN) -> str:
    if papel not in PAPEIS:
        raise ValueError("Papel inválido.")
    codigo = "-".join("".join(secrets.choice(_ALFABETO) for _ in range(4)) for _ in range(3))
    agora = datetime.now(timezone.utc)
    with _db() as c:
        c.execute("INSERT INTO convites (codigo_hash, papel, criado_em, expira_em) VALUES (?,?,?,?)",
                  (_hash_token(codigo), papel, agora.isoformat(timespec="seconds"),
                   (agora + timedelta(minutes=minutos)).isoformat(timespec="seconds")))
    return codigo


def _normalizar_codigo(codigo: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", codigo.upper())


def usar_convite(codigo: str, email: str, nome: str, senha: str) -> int:
    """Valida o convite e cria a conta, na mesma transação do uso do código (não dá para usar duas vezes)."""
    limpo = _normalizar_codigo(codigo)
    formatado = "-".join(limpo[i:i + 4] for i in range(0, 12, 4)) if len(limpo) == 12 else ""
    agora = _agora()
    with _db() as c:
        c.execute("BEGIN IMMEDIATE")
        conv = c.execute("SELECT papel FROM convites WHERE codigo_hash = ? AND usado_em IS NULL AND expira_em > ?",
                         (_hash_token(formatado), agora)).fetchone() if formatado else None
        if not conv:
            raise PermissionError("Convite inválido, vencido ou já usado.")
        # valida e cria; qualquer erro desfaz tudo (o convite continua válido para tentar de novo)
        email = email.strip().lower()
        if not _EMAIL.match(email):
            raise ValueError("E-mail inválido.")
        if not nome.strip():
            raise ValueError("Informe o nome.")
        if len(senha) < MIN_SENHA:
            raise ValueError(f"A senha precisa ter pelo menos {MIN_SENHA} caracteres.")
        if c.execute("SELECT 1 FROM editores WHERE email = ?", (email,)).fetchone():
            raise ValueError("Já existe uma conta com este e-mail.")
        eid = c.execute("INSERT INTO editores (email, nome, papel, senha_hash, criado_em) VALUES (?,?,?,?,?)",
                        (email, nome.strip(), conv["papel"], hash_senha(senha), agora)).lastrowid
        c.execute("UPDATE convites SET usado_em = ? WHERE codigo_hash = ?", (agora, _hash_token(formatado)))
        return eid


# --------------------------------------------------------------------------- #
# Proteções: tentativas erradas e origem das requisições
# --------------------------------------------------------------------------- #
class _Falhas:
    def __init__(self, maximo: int, janela_s: int) -> None:
        self.maximo, self.janela = maximo, janela_s
        self._h: dict[str, deque[float]] = defaultdict(deque)

    def _limpa(self, chave: str) -> deque[float]:
        agora, fila = time.monotonic(), self._h[chave]
        while fila and fila[0] <= agora - self.janela:
            fila.popleft()
        return fila

    def bloqueado(self, *chaves: str) -> bool:
        return any(len(self._limpa(k)) >= self.maximo for k in chaves)

    def registrar(self, *chaves: str) -> None:
        for k in chaves:
            self._limpa(k).append(time.monotonic())
        if len(self._h) > 20_000:
            self._h.clear()

    def zerar(self, *chaves: str) -> None:
        for k in chaves:
            self._h.pop(k, None)


_falhas = _Falhas(maximo=5, janela_s=600)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


def mesma_origem(request: Request) -> None:
    """Rejeita escrita disparada por outro site. Clientes sem cabeçalho de origem (curl, testes) passam."""
    origem = request.headers.get("origin")
    if origem and urlparse(origem).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="Origem não permitida.")
    if not origem and request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Origem não permitida.")


def editor_atual(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with _db() as c:
        return c.execute("""SELECT e.id, e.email, e.nome, e.papel FROM sessoes s JOIN editores e ON e.id = s.editor_id
                            WHERE s.token_hash = ? AND s.expira_em > ?""", (_hash_token(token), _agora())).fetchone()


def exigir_editor(request: Request) -> sqlite3.Row:
    e = editor_atual(request)
    if not e or e["papel"] not in PAPEIS:
        raise HTTPException(status_code=401, detail="Entre com a sua conta de editor.")
    return e


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api", tags=["editor"])


class EntrarIn(BaseModel):
    email: str = Field(max_length=254)
    senha: str = Field(min_length=1, max_length=200)


class PostitIn(BaseModel):
    pagina: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    ancora: str = Field(default="", max_length=600)
    rotulo: str = Field(default="", max_length=160)
    texto: str = Field(min_length=1, max_length=LIMITE_TEXTO)
    publico: bool = False

    @field_validator("texto", "rotulo", "ancora")
    @classmethod
    def _limpar(cls, v: str) -> str:
        return v.strip()

    @field_validator("texto")
    @classmethod
    def _nao_vazio(cls, v: str) -> str:
        if not v:
            raise ValueError("A nota está vazia.")
        return v


@router.post("/editor/entrar", dependencies=[Depends(mesma_origem)])
def entrar(dados: EntrarIn, request: Request, resposta: Response) -> dict:
    email = dados.email.strip().lower()
    chaves = (f"ip:{_ip(request)}", f"email:{email}")
    if _falhas.bloqueado(*chaves):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos e tente de novo.")
    with _db() as c:
        e = c.execute("SELECT id, nome, papel, senha_hash FROM editores WHERE email = ?", (email,)).fetchone()
        ok = verificar_senha(dados.senha, e["senha_hash"] if e else _HASH_FALSO)
        if not (e and ok):
            _falhas.registrar(*chaves)
            raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")
        token = secrets.token_urlsafe(32)
        agora = datetime.now(timezone.utc)
        c.execute("DELETE FROM sessoes WHERE expira_em <= ?", (agora.isoformat(timespec="seconds"),))
        c.execute("INSERT INTO sessoes (token_hash, editor_id, criado_em, expira_em) VALUES (?,?,?,?)",
                  (_hash_token(token), e["id"], agora.isoformat(timespec="seconds"),
                   (agora + timedelta(days=VALIDADE_DIAS)).isoformat(timespec="seconds")))
    _falhas.zerar(*chaves)
    resposta.set_cookie(COOKIE, token, max_age=VALIDADE_DIAS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")
    return {"logado": True, "nome": e["nome"], "papel": e["papel"]}


class RegistrarIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=40)
    email: str = Field(max_length=254)
    nome: str = Field(min_length=1, max_length=80)
    senha: str = Field(min_length=1, max_length=200)


@router.post("/editor/registrar", dependencies=[Depends(mesma_origem)])
def registrar(dados: RegistrarIn, request: Request, resposta: Response) -> dict:
    chaves = (f"ip:{_ip(request)}", "convite")
    if _falhas.bloqueado(*chaves):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos e tente de novo.")
    try:
        usar_convite(dados.codigo, dados.email, dados.nome, dados.senha)
    except PermissionError as exc:
        _falhas.registrar(*chaves)
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return entrar(EntrarIn(email=dados.email, senha=dados.senha), request, resposta)


@router.post("/editor/sair", dependencies=[Depends(mesma_origem)])
def sair(request: Request, resposta: Response) -> dict:
    token = request.cookies.get(COOKIE)
    if token:
        with _db() as c:
            c.execute("DELETE FROM sessoes WHERE token_hash = ?", (_hash_token(token),))
    resposta.delete_cookie(COOKIE, path="/")
    return {"logado": False}


@router.get("/editor/eu")
def eu(request: Request) -> dict:
    e = editor_atual(request)
    return {"logado": bool(e), "nome": e["nome"] if e else None, "papel": e["papel"] if e else None}


def _json(l: sqlite3.Row, meu_id: int | None) -> dict:
    return {"id": l["id"], "pagina": l["pagina"], "ancora": l["ancora"], "rotulo": l["rotulo"], "texto": l["texto"],
            "publico": bool(l["publico"]), "autor": l["autor"], "criado_em": l["criado_em"],
            "atualizado_em": l["atualizado_em"], "minha": meu_id is not None and l["editor_id"] == meu_id}


_SELECT = "SELECT p.*, e.nome AS autor FROM postits p JOIN editores e ON e.id = p.editor_id"


@router.get("/postits")
def listar_postits(pagina: str, request: Request) -> dict:
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", pagina):
        raise HTTPException(status_code=422, detail="Página inválida.")
    e = editor_atual(request)
    meu = e["id"] if e else None
    with _db() as c:
        linhas = c.execute(f"{_SELECT} WHERE p.pagina = ? AND (p.publico = 1 OR p.editor_id = ?) ORDER BY p.criado_em, p.id",
                           (pagina, meu if meu is not None else -1)).fetchall()
    return {"itens": [_json(l, meu) for l in linhas]}


@router.post("/postits", dependencies=[Depends(mesma_origem)])
def criar_postit(dados: PostitIn, e: sqlite3.Row = Depends(exigir_editor)) -> dict:
    agora = _agora()
    with _db() as c:
        pid = c.execute("INSERT INTO postits (pagina, ancora, rotulo, texto, publico, editor_id, criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?)",
                        (dados.pagina, dados.ancora, dados.rotulo, dados.texto, int(dados.publico), e["id"], agora, agora)).lastrowid
        l = c.execute(f"{_SELECT} WHERE p.id = ?", (pid,)).fetchone()
    return _json(l, e["id"])


def _do_autor(c: sqlite3.Connection, pid: int, e: sqlite3.Row) -> sqlite3.Row:
    l = c.execute(f"{_SELECT} WHERE p.id = ?", (pid,)).fetchone()
    if not l or (l["editor_id"] != e["id"] and not l["publico"]):
        raise HTTPException(status_code=404, detail="Post-it não encontrado.")   # rascunho alheio não revela que existe
    if l["editor_id"] != e["id"]:
        raise HTTPException(status_code=403, detail="Só o autor altera este post-it.")
    return l


@router.put("/postits/{pid}", dependencies=[Depends(mesma_origem)])
def editar_postit(pid: int, dados: PostitIn, e: sqlite3.Row = Depends(exigir_editor)) -> dict:
    with _db() as c:
        antigo = _do_autor(c, pid, e)
        c.execute("UPDATE postits SET texto = ?, publico = ?, ancora = ?, rotulo = ?, atualizado_em = ? WHERE id = ?",
                  (dados.texto, int(dados.publico), dados.ancora, dados.rotulo, _agora(), pid))
        l = c.execute(f"{_SELECT} WHERE p.id = ?", (antigo["id"],)).fetchone()
    return _json(l, e["id"])


@router.delete("/postits/{pid}", dependencies=[Depends(mesma_origem)])
def apagar_postit(pid: int, e: sqlite3.Row = Depends(exigir_editor)) -> dict:
    with _db() as c:
        _do_autor(c, pid, e)
        c.execute("DELETE FROM postits WHERE id = ?", (pid,))
    return {"apagado": pid}


# --------------------------------------------------------------------------- #
# Administração pelo terminal: python backend/editor.py criar|listar|senha|remover
# --------------------------------------------------------------------------- #
def _pedir_senha() -> str:
    a = getpass.getpass("Senha (não aparece enquanto digita): ")
    if a != getpass.getpass("Repita a senha: "):
        raise ValueError("As senhas não conferem.")
    return a


def main() -> int:
    ap = argparse.ArgumentParser(description="Administra as contas de editor.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("criar", help="cria uma conta de editor")
    c.add_argument("--email", required=True)
    c.add_argument("--nome", required=True)
    cv = sub.add_parser("convite", help="gera um código de convite de uso único (vale 60 minutos)")
    cv.add_argument("--minutos", type=int, default=VALIDADE_CONVITE_MIN)
    sub.add_parser("listar", help="lista as contas")
    s = sub.add_parser("senha", help="troca a senha de uma conta (encerra as sessões dela)")
    s.add_argument("--email", required=True)
    r = sub.add_parser("remover", help="remove uma conta e os post-its dela")
    r.add_argument("--email", required=True)
    a = ap.parse_args()
    try:
        if a.cmd == "criar":
            criar_editor(a.email, a.nome, _pedir_senha())
            print(f"Conta de editor criada: {a.email.strip().lower()}")
        elif a.cmd == "convite":
            codigo = criar_convite(minutos=a.minutos)
            print(f"Código de convite (uso único, vale {a.minutos} min): {codigo}")
            print("Quem receber abre o site, Área do editor, \"Tenho um convite\", e cria a própria conta e senha.")
        elif a.cmd == "listar":
            with _db() as conn:
                for l in conn.execute("SELECT email, nome, papel, criado_em FROM editores ORDER BY id"):
                    print(f"{l['email']}  |  {l['nome']}  |  {l['papel']}  |  desde {l['criado_em'][:10]}")
        elif a.cmd == "senha":
            nova = _pedir_senha()
            if len(nova) < MIN_SENHA:
                raise ValueError(f"A senha precisa ter pelo menos {MIN_SENHA} caracteres.")
            with _db() as conn:
                l = conn.execute("SELECT id FROM editores WHERE email = ?", (a.email.strip().lower(),)).fetchone()
                if not l:
                    raise ValueError("Conta não encontrada.")
                conn.execute("UPDATE editores SET senha_hash = ? WHERE id = ?", (hash_senha(nova), l["id"]))
                conn.execute("DELETE FROM sessoes WHERE editor_id = ?", (l["id"],))
            print("Senha trocada; as sessões abertas foram encerradas.")
        elif a.cmd == "remover":
            with _db() as conn:
                n = conn.execute("DELETE FROM editores WHERE email = ?", (a.email.strip().lower(),)).rowcount
            print("Conta removida." if n else "Conta não encontrada.")
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
