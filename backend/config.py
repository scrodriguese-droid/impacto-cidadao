"""Configuração central do ImpactoCidadão.

Todas as opções vêm de variáveis de ambiente (arquivo `.env` na raiz do projeto).
Veja `.env.example` para a lista completa e comentada.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")


def _texto(nome: str, padrao: str = "") -> str:
    return os.getenv(nome, padrao).strip()


def _inteiro(nome: str, padrao: int) -> int:
    bruto = os.getenv(nome, str(padrao)).strip()
    try:
        return int(bruto)
    except ValueError as exc:
        raise RuntimeError(f"A variável {nome} deve ser um número inteiro (valor atual: {bruto!r}).") from exc


def _booleano(nome: str, padrao: bool) -> bool:
    return os.getenv(nome, str(padrao)).strip().lower() in {"1", "true", "sim", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # Banco de dados
    database_url: str

    # IA
    llm_provider: str                 # "anthropic" | "gemini"
    anthropic_model: str
    anthropic_effort: str             # low | medium | high | xhigh | max
    anthropic_fallback: bool          # re-executa em outro modelo se houver recusa do classificador
    gemini_model: str
    anthropic_api_key: str = field(default="", repr=False)
    gemini_api_key: str = field(default="", repr=False)

    # Coleta
    user_agent: str = ""
    tse_ano: int = 2026
    tse_eleicao_id: int = 20322002026  # "Eleição Geral Federal 2026" em /v1/eleicao/ordinarias
    max_chars_texto: int = 400_000     # ~100 mil tokens; acima disso a análise é adiada (não truncamos)
    max_chars_plano: int = 1_500_000   # planos de governo podem ter centenas de páginas
    pasta_planos: Path = RAIZ / "dados" / "planos"
    manifesto_candidatos: Path = RAIZ / "dados" / "candidatos_presidente_2026.json"

    # API
    voto_sal: str = field(default="", repr=False)
    cors_origins: tuple[str, ...] = ("http://localhost:8000", "http://127.0.0.1:8000")
    pasta_frontend: Path = RAIZ / "frontend"

    @classmethod
    def carregar(cls) -> "Config":
        contato = _texto("CONTATO_EMAIL")
        ua = "ImpactoCidadao/1.0 (projeto civico de transparencia" + (f"; {contato})" if contato else ")")
        origens = tuple(o.strip() for o in _texto("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip())
        return cls(
            database_url=_texto("DATABASE_URL", "postgresql://impacto:impacto@127.0.0.1:5432/impacto"),
            llm_provider=_texto("LLM_PROVIDER", "anthropic").lower(),
            anthropic_model=_texto("ANTHROPIC_MODEL", "claude-opus-5-5"),
            anthropic_effort=_texto("ANTHROPIC_EFFORT", "medium"),
            anthropic_fallback=_booleano("ANTHROPIC_FALLBACK", True),
            gemini_model=_texto("GEMINI_MODEL", "gemini-2.5-flash"),
            anthropic_api_key=_texto("ANTHROPIC_API_KEY"),
            gemini_api_key=_texto("GEMINI_API_KEY"),
            user_agent=ua,
            tse_ano=_inteiro("TSE_ANO", 2026),
            tse_eleicao_id=_inteiro("TSE_ELEICAO_ID", 20322002026),
            max_chars_texto=_inteiro("MAX_CHARS_TEXTO", 400_000),
            max_chars_plano=_inteiro("MAX_CHARS_PLANO", 1_500_000),
            voto_sal=_texto("VOTO_SAL", "troque-este-sal-em-producao"),
            cors_origins=origens,
        )


cfg = Config.carregar()
