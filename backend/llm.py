"""Cliente de LLM (Claude ou Gemini) com saída JSON validada por Pydantic.

Fluxo de `analisar`:
  1. chama o provedor pedindo JSON no esquema do modelo Pydantic;
  2. valida a resposta; se violar o contrato, repete a chamada informando o erro
     (até `max_tentativas`);
  3. devolve o objeto validado + metadados de uso.

Nada que falhe na validação chega ao banco.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from config import cfg
from schemas import schema_para_llm

log = logging.getLogger("impacto.llm")

T = TypeVar("T", bound=BaseModel)

MAX_TOKENS_SAIDA = 32_000


class ErroLLM(RuntimeError):
    """Falha ao obter uma resposta válida do modelo."""


class ErroConfiguracaoLLM(ErroLLM):
    """Falha que se repetiria em toda chamada (chave ausente/inválida): aborte o lote."""


class RecusaLLM(ErroLLM):
    """O classificador de segurança do provedor recusou a solicitação."""


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #
def modelo_em_uso() -> str:
    return cfg.gemini_model if cfg.llm_provider == "gemini" else cfg.anthropic_model


def analisar(system: str, usuario: str, modelo: type[T], *, max_tentativas: int = 3) -> tuple[T, dict[str, Any]]:
    """Executa a análise e devolve (objeto_validado, info_de_uso)."""
    schema = schema_para_llm(modelo)
    mensagem = usuario
    erro_anterior = ""

    for tentativa in range(1, max_tentativas + 1):
        texto, info = _chamar(system, mensagem, schema)
        try:
            dados = json.loads(_extrair_json(texto))
            return modelo.model_validate(dados), info
        except (json.JSONDecodeError, ValidationError) as exc:
            erro_anterior = _resumir_erro(exc)
            log.warning("Resposta fora do contrato (tentativa %d/%d): %s", tentativa, max_tentativas, erro_anterior)
            mensagem = (
                f"{usuario}\n\nCORREÇÃO NECESSÁRIA: a resposta anterior foi rejeitada pela validação "
                f"automática pelos motivos abaixo. Responda de novo com o JSON COMPLETO e corrigido.\n"
                f"{erro_anterior}"
            )

    raise ErroLLM(f"Resposta inválida após {max_tentativas} tentativas. Último erro: {erro_anterior}")


# --------------------------------------------------------------------------- #
# Despacho por provedor
# --------------------------------------------------------------------------- #
def _chamar(system: str, usuario: str, schema: dict) -> tuple[str, dict]:
    if cfg.llm_provider == "anthropic":
        return _chamar_claude(system, usuario, schema)
    if cfg.llm_provider == "gemini":
        return _chamar_gemini(system, usuario, schema)
    raise ErroConfiguracaoLLM(f"LLM_PROVIDER inválido: {cfg.llm_provider!r} (use 'anthropic' ou 'gemini').")


# --- Claude ----------------------------------------------------------------- #
_cliente_claude = None


def _claude():
    global _cliente_claude
    if _cliente_claude is None:
        try:
            import anthropic

            _cliente_claude = anthropic.Anthropic(
                api_key=cfg.anthropic_api_key or None,  # None -> variável de ambiente / perfil `ant auth login`
                max_retries=4,
                timeout=600.0,
            )
        except Exception as exc:  # noqa: BLE001 - qualquer falha aqui é de configuração
            raise ErroConfiguracaoLLM(
                f"Não foi possível iniciar o cliente Anthropic ({exc}). Defina ANTHROPIC_API_KEY no .env."
            ) from exc
    return _cliente_claude


def _chamar_claude(system: str, usuario: str, schema: dict) -> tuple[str, dict]:
    import anthropic

    cliente = _claude()
    base: dict[str, Any] = dict(
        model=cfg.anthropic_model,
        max_tokens=MAX_TOKENS_SAIDA,
        system=system,
        messages=[{"role": "user", "content": usuario}],
        # Claude Opus 5.5 não aceita temperature/top_p; a profundidade é controlada por `effort`.
        output_config={"effort": cfg.anthropic_effort, "format": {"type": "json_schema", "schema": schema}},
    )
    com_fallback = dict(base, betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"})

    def executar(kwargs: dict) -> Any:
        # Streaming evita timeout em entradas longas (planos de governo têm dezenas de páginas).
        with cliente.beta.messages.stream(**kwargs) as fluxo:
            return fluxo.get_final_message()

    try:
        try:
            msg = executar(com_fallback if cfg.anthropic_fallback else base)
        except anthropic.BadRequestError as exc:
            # Conta/região sem acesso ao beta de fallback: repete sem ele em vez de falhar o lote.
            if cfg.anthropic_fallback and "fallback" in str(exc).lower():
                log.warning("Beta de fallback indisponível (%s); repetindo sem fallback. "
                            "Defina ANTHROPIC_FALLBACK=false para silenciar.", exc)
                msg = executar(base)
            else:
                raise
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise ErroConfiguracaoLLM(f"Credencial Anthropic recusada: {exc.message}") from exc
    except anthropic.NotFoundError as exc:
        raise ErroConfiguracaoLLM(f"Modelo {cfg.anthropic_model!r} não encontrado: {exc.message}") from exc
    except anthropic.BadRequestError as exc:
        raise ErroLLM(f"Requisição rejeitada pela API: {exc.message}") from exc
    except anthropic.RateLimitError as exc:
        raise ErroLLM("Limite de requisições da API atingido (tente de novo mais tarde).") from exc
    except anthropic.APIStatusError as exc:
        raise ErroLLM(f"Erro {exc.status_code} da API Anthropic: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ErroLLM(f"Falha de rede ao chamar a API Anthropic: {exc}") from exc

    if msg.stop_reason == "refusal":
        categoria = getattr(getattr(msg, "stop_details", None), "category", None)
        raise RecusaLLM(f"Solicitação recusada pelo classificador de segurança (categoria: {categoria}).")
    if msg.stop_reason == "max_tokens":
        raise ErroLLM("Resposta cortada por limite de tokens de saída.")

    texto = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    info = {
        "modelo": getattr(msg, "model", cfg.anthropic_model),
        "tokens_entrada": getattr(msg.usage, "input_tokens", None),
        "tokens_saida": getattr(msg.usage, "output_tokens", None),
    }
    return texto, info


# --- Gemini (REST) ------------------------------------------------------------ #
def _chamar_gemini(system: str, usuario: str, schema: dict) -> tuple[str, dict]:
    if not cfg.gemini_api_key:
        raise ErroConfiguracaoLLM("GEMINI_API_KEY não definida no .env.")

    instrucao = (
        f"{system}\n\nResponda SOMENTE com um objeto JSON válido que obedeça a este JSON Schema, "
        f"sem comentários nem texto fora do JSON:\n{json.dumps(schema, ensure_ascii=False)}"
    )
    corpo = {
        "systemInstruction": {"parts": [{"text": instrucao}]},
        "contents": [{"role": "user", "parts": [{"text": usuario}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1,
                             "maxOutputTokens": 32_000},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg.gemini_model}:generateContent"
    cabecalhos = {"x-goog-api-key": cfg.gemini_api_key, "Content-Type": "application/json"}

    resp = None
    for espera in (0, 2, 6, 15):
        if espera:
            time.sleep(espera)
        try:
            resp = httpx.post(url, json=corpo, headers=cabecalhos, timeout=300.0)
        except httpx.HTTPError as exc:
            log.warning("Falha de rede no Gemini: %s", exc)
            continue
        if resp.status_code in (429, 500, 502, 503, 504):
            log.warning("Gemini respondeu %s; nova tentativa.", resp.status_code)
            continue
        break

    if resp is None:
        raise ErroLLM("Gemini inacessível após várias tentativas.")
    if resp.status_code in (400, 401, 403, 404):
        raise ErroConfiguracaoLLM(f"Gemini recusou a requisição ({resp.status_code}): {resp.text[:300]}")
    if resp.status_code != 200:
        raise ErroLLM(f"Gemini respondeu {resp.status_code}: {resp.text[:300]}")

    dados = resp.json()
    candidatos = dados.get("candidates") or []
    if not candidatos:
        raise RecusaLLM(f"Gemini não devolveu candidatos: {json.dumps(dados.get('promptFeedback', {}))[:200]}")
    cand = candidatos[0]
    if cand.get("finishReason") not in (None, "STOP"):
        raise ErroLLM(f"Gemini encerrou com finishReason={cand.get('finishReason')}.")
    texto = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    uso = dados.get("usageMetadata", {})
    return texto, {
        "modelo": cfg.gemini_model,
        "tokens_entrada": uso.get("promptTokenCount"),
        "tokens_saida": uso.get("candidatesTokenCount"),
    }


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _extrair_json(texto: str) -> str:
    """Remove cercas ```json e texto ao redor, caso o modelo os inclua."""
    texto = texto.strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", texto).strip()
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio != -1 and fim > inicio:
        return texto[inicio : fim + 1]
    return texto


def _resumir_erro(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        partes = []
        for e in exc.errors()[:8]:
            local = ".".join(str(x) for x in e["loc"]) or "(raiz)"
            partes.append(f"- {local}: {e['msg']}")
        return "\n".join(partes)
    return f"- JSON malformado: {exc}"
