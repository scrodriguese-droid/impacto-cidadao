"""Acesso ao PostgreSQL (psycopg 3).

* A API usa um pool de conexões aberto no `lifespan` do FastAPI.
* Os coletores (scripts) usam `conexao_avulsa()`.
* Linhas voltam como dicionários; parâmetros são sempre passados separados da
  SQL (nunca interpolados) para evitar injeção.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import cfg

log = logging.getLogger("impacto.db")

_pool: ConnectionPool | None = None


def abrir_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            cfg.database_url,
            min_size=1,
            max_size=8,
            # prepare_threshold=None: compatível com o pooler "transaction mode" do Supabase.
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=False,
        )
        _pool.open(wait=True, timeout=15)
        log.info("Pool de conexões aberto.")
    return _pool


def fechar_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def conexao() -> Iterator[psycopg.Connection]:
    """Conexão do pool (commit automático ao sair sem erro)."""
    with abrir_pool().connection() as conn:
        yield conn


@contextmanager
def conexao_avulsa() -> Iterator[psycopg.Connection]:
    """Conexão única para scripts."""
    with psycopg.connect(cfg.database_url, row_factory=dict_row, prepare_threshold=None) as conn:
        yield conn


def consultar(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    with conexao() as conn:
        return list(conn.execute(sql, params).fetchall())


def consultar_um(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    with conexao() as conn:
        return conn.execute(sql, params).fetchone()


def verificar() -> bool:
    try:
        return consultar_um("SELECT 1 AS ok") == {"ok": 1}
    except Exception:  # noqa: BLE001
        return False
