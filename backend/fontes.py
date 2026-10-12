"""Clientes das fontes oficiais de dados abertos.

* Câmara dos Deputados  — https://dadosabertos.camara.leg.br/api/v2
* Senado Federal        — https://legis.senado.leg.br/dadosabertos  (endpoint /processo;
                          o antigo /materia foi descontinuado em 2026)
* TSE (DivulgaCandContas) — https://divulgacandcontas.tse.jus.br/divulga/rest
* IBGE (localidades e SIDRA)

Todas as chamadas se identificam com um User-Agent honesto (cfg.user_agent),
respeitam Retry-After e fazem backoff em 429/5xx.
"""
from __future__ import annotations

import io
import logging
import re
import time
import unicodedata
from datetime import date
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any, Iterator

import httpx

from config import cfg

log = logging.getLogger("impacto.fontes")
# O pypdf avisa sobre fontes CFF a cada página ("fontTools is required..."), mas a extração
# de texto é idêntica sem ele (verificado em 12 planos do TSE); silenciamos o ruído.
logging.getLogger("pypdf").setLevel(logging.ERROR)

TAMANHO_MAX_ARQUIVO = 40 * 1024 * 1024  # 40 MB


class ErroFonte(RuntimeError):
    """Falha ao consultar uma fonte oficial."""


class AcessoNegado(ErroFonte):
    """A fonte respondeu 401/403 (ex.: proteção anti-bot do site)."""


# --------------------------------------------------------------------------- #
# HTTP com retry
# --------------------------------------------------------------------------- #
class ClienteHTTP:
    def __init__(self, base_url: str = "", *, pausa: float = 0.15, timeout: float = 45.0) -> None:
        self._cliente = httpx.Client(
            base_url=base_url,
            headers={"User-Agent": cfg.user_agent, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
        )
        self._pausa = pausa

    def get(self, url: str, params: dict | None = None, *, accept: str | None = None) -> httpx.Response:
        cabecalhos = {"Accept": accept} if accept else None
        ultimo: Exception | None = None
        for tentativa in range(5):
            if tentativa:
                time.sleep(min(2 ** tentativa, 20))
            try:
                resp = self._cliente.get(url, params=params, headers=cabecalhos)
            except httpx.HTTPError as exc:
                ultimo = exc
                log.debug("Falha de rede em %s: %s", url, exc)
                continue
            if resp.status_code == 429:
                espera = int(resp.headers.get("Retry-After", "5") or 5)
                time.sleep(min(espera, 60))
                ultimo = ErroFonte("429 Too Many Requests")
                continue
            if resp.status_code in (401, 403):
                raise AcessoNegado(f"{resp.status_code} em {url}")
            if resp.status_code >= 500:
                ultimo = ErroFonte(f"{resp.status_code} em {url}")
                continue
            if resp.status_code >= 400:
                raise ErroFonte(f"{resp.status_code} em {url}")
            time.sleep(self._pausa)
            return resp
        raise ErroFonte(f"Falha ao consultar {url}: {ultimo}")

    def json(self, url: str, params: dict | None = None) -> Any:
        resp = self.get(url, params)
        try:
            return resp.json()
        except ValueError as exc:
            raise ErroFonte(f"Resposta não é JSON em {url}") from exc


# --------------------------------------------------------------------------- #
# Texto: PDF, HTML, normalização e conferência de citações
# --------------------------------------------------------------------------- #
def extrair_paginas_pdf(conteudo: bytes) -> list[str]:
    """Texto de cada página do PDF (lista vazia/curta indica PDF escaneado)."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        leitor = PdfReader(io.BytesIO(conteudo))
        if leitor.is_encrypted:
            leitor.decrypt("")
        return [(pagina.extract_text() or "") for pagina in leitor.pages]
    except (PyPdfError, ValueError, KeyError, OSError) as exc:
        raise ErroFonte(f"PDF ilegível: {exc}") from exc


class _HtmlParaTexto(HTMLParser):
    _IGNORAR = {"script", "style", "noscript", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._partes: list[str] = []
        self._ignorando = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in self._IGNORAR:
            self._ignorando += 1
        elif tag in {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self._partes.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in self._IGNORAR and self._ignorando:
            self._ignorando -= 1

    def handle_data(self, data):  # noqa: ANN001
        if not self._ignorando:
            self._partes.append(data)

    def texto(self) -> str:
        return re.sub(r"[ \t\r\f\v]+", " ", "".join(self._partes)).strip()


def html_para_texto(html: str) -> str:
    parser = _HtmlParaTexto()
    parser.feed(html)
    return re.sub(r"\n\s*\n+", "\n\n", parser.texto())


def texto_paginado(paginas: list[str]) -> str:
    """Junta as páginas com marcas [[PÁGINA n]] (úteis para a IA e para auditoria)."""
    return "\n\n".join(f"[[PÁGINA {i}]]\n{t.strip()}" for i, t in enumerate(paginas, start=1))


def normalizar(texto: str) -> str:
    """Forma canônica p/ comparar citações: sem acentos, espaços, hifenização ou pontuação."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-zA-Z]+", "", texto).lower()


def localizar_trecho(trecho: str, paginas_norm: list[str]) -> int | None:
    """Número da página (1-based) onde `trecho` aparece; None se não existir no documento.

    Aceita citações que atravessam a quebra entre duas páginas consecutivas.
    """
    alvo = normalizar(trecho)
    if len(alvo) < 15:
        return None
    for i, pagina in enumerate(paginas_norm):
        if alvo in pagina:
            return i + 1
    for i in range(len(paginas_norm) - 1):
        if alvo in paginas_norm[i] + paginas_norm[i + 1]:
            return i + 1
    return None


# --------------------------------------------------------------------------- #
# Câmara dos Deputados
# --------------------------------------------------------------------------- #
class Camara:
    BASE = "https://dadosabertos.camara.leg.br/api/v2"

    def __init__(self) -> None:
        self.http = ClienteHTTP(self.BASE)

    def listar(self, sigla_tipo: str, ano: int, desde: date | None = None, max_itens: int = 300) -> Iterator[dict]:
        params: dict[str, Any] = {
            "siglaTipo": sigla_tipo, "ano": ano, "ordem": "DESC", "ordenarPor": "id",
            "itens": 100, "pagina": 1,
        }
        if desde:
            params["dataApresentacaoInicio"] = desde.isoformat()
        entregues = 0
        while entregues < max_itens:
            dados = self.http.json("/proposicoes", params)
            for item in dados.get("dados", []):
                yield item
                entregues += 1
                if entregues >= max_itens:
                    return
            if not any(link.get("rel") == "next" for link in dados.get("links", [])):
                return
            params["pagina"] += 1

    def detalhe(self, id_proposicao: int | str) -> dict:
        return self.http.json(f"/proposicoes/{id_proposicao}").get("dados", {})

    def autores(self, id_proposicao: int | str) -> list[dict]:
        return self.http.json(f"/proposicoes/{id_proposicao}/autores").get("dados", [])

    def tramitacoes(self, id_proposicao: int | str) -> list[dict]:
        eventos = self.http.json(f"/proposicoes/{id_proposicao}/tramitacoes").get("dados", [])
        return sorted(eventos, key=lambda e: (e.get("dataHora") or "", e.get("sequencia") or 0))

    @staticmethod
    def autoria_executivo(autores: list[dict]) -> bool:
        for a in autores:
            nome = (a.get("nome") or "").lower()
            if "poder executivo" in nome or "presidência da república" in nome or "presidencia da republica" in nome:
                return True
            if str(a.get("codTipo")) == "30000":  # "Órgão do Poder Executivo"
                return True
        return False

    def baixar_texto(self, url: str) -> str | None:
        """Texto do inteiro teor (PDF ou HTML). None se indisponível."""
        return baixar_documento(self.http, url)


def baixar_documento(http: ClienteHTTP, url: str) -> str | None:
    if not url:
        return None
    try:
        resp = http.get(url, accept="application/pdf, text/html;q=0.9, */*;q=0.5")
    except ErroFonte as exc:
        log.warning("Não foi possível baixar %s: %s", url, exc)
        return None
    if len(resp.content) > TAMANHO_MAX_ARQUIVO:
        log.warning("Documento muito grande (%d bytes): %s", len(resp.content), url)
        return None
    tipo = resp.headers.get("content-type", "").lower()
    try:
        if "pdf" in tipo or resp.content[:5] == b"%PDF-":
            paginas = extrair_paginas_pdf(resp.content)
            texto = "\n\n".join(p.strip() for p in paginas if p.strip())
        elif "html" in tipo:
            texto = html_para_texto(resp.text)
        else:
            texto = resp.text
    except ErroFonte as exc:
        log.warning("Documento ilegível (%s): %s", url, exc)
        return None
    texto = re.sub(r"[ \t]+", " ", texto).strip()
    return texto or None


# --------------------------------------------------------------------------- #
# Senado Federal
# --------------------------------------------------------------------------- #
class Senado:
    BASE = "https://legis.senado.leg.br/dadosabertos"

    def __init__(self) -> None:
        self.http = ClienteHTTP(self.BASE)

    def listar_processos(self, sigla: str, ano: int) -> list[dict]:
        dados = self.http.json("/processo", {"sigla": sigla, "ano": ano, "v": 1})
        return dados if isinstance(dados, list) else []

    @staticmethod
    def autoria_executivo(processo: dict) -> bool:
        autoria = (processo.get("autoria") or "").lower()
        return "presidência da república" in autoria or "poder executivo" in autoria

    @staticmethod
    def url_pagina(processo: dict) -> str:
        codigo = processo.get("codigoMateria")
        return f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{codigo}" if codigo else ""


# --------------------------------------------------------------------------- #
# TSE — DivulgaCandContas
# --------------------------------------------------------------------------- #
class Tse:
    BASE = "https://divulgacandcontas.tse.jus.br/divulga/rest"
    COD_TIPO_PLANO_DE_GOVERNO = "5"

    def __init__(self) -> None:
        self.http = ClienteHTTP(self.BASE, pausa=0.3)

    def listar_candidatos(self, ano: int, eleicao_id: int, cargo: int = 1, uf: str = "BR") -> list[dict]:
        """cargo 1 = Presidente."""
        dados = self.http.json(f"/v1/candidatura/listar/{ano}/{uf}/{eleicao_id}/{cargo}/candidatos")
        return dados.get("candidatos", [])

    def detalhe(self, ano: int, eleicao_id: int, sq_candidato: int, uf: str = "BR") -> dict:
        return self.http.json(f"/v1/candidatura/buscar/{ano}/{uf}/{eleicao_id}/candidato/{sq_candidato}")

    def baixar_pdf(self, arquivo_id: int) -> bytes:
        resp = self.http.get(f"/arquivo/doc/{arquivo_id}", accept="application/pdf")
        if resp.content[:5] != b"%PDF-":
            raise ErroFonte(f"Arquivo {arquivo_id} não é um PDF (content-type: {resp.headers.get('content-type')}).")
        if len(resp.content) > TAMANHO_MAX_ARQUIVO:
            raise ErroFonte(f"Arquivo {arquivo_id} excede {TAMANHO_MAX_ARQUIVO // (1024 * 1024)} MB.")
        return resp.content

    @classmethod
    def url_pdf(cls, arquivo_id: int) -> str:
        return f"{cls.BASE}/arquivo/doc/{arquivo_id}"

    @classmethod
    def resumir_candidato(cls, d: dict) -> dict:
        """Mantém SÓ o necessário (minimização de dados — sem CPF, título, bens, nascimento)."""
        planos = [
            {"arquivo_id": int(a["idArquivo"]), "nome": a.get("nome") or ""}
            for a in (d.get("arquivos") or [])
            if str(a.get("codTipo")) == cls.COD_TIPO_PLANO_DE_GOVERNO and a.get("idArquivo")
        ]
        vices = d.get("vices") or []
        vice = None
        if vices:
            primeiro = vices[0]
            vice = primeiro.get("nm_CANDIDATO") or primeiro.get("nomeUrna") if isinstance(primeiro, dict) else str(primeiro)
        return {
            "sq_candidato": int(d["id"]),
            "nome_urna": d.get("nomeUrna") or "",
            "numero": d.get("numero"),
            "sigla_partido": (d.get("partido") or {}).get("sigla"),
            "situacao_candidatura": d.get("descricaoSituacao"),
            "totalizacao": d.get("descricaoTotalizacao"),
            "vice": vice,
            "planos": planos,
        }


# --------------------------------------------------------------------------- #
# IBGE — municípios e população
# --------------------------------------------------------------------------- #
class Ibge:
    LOCALIDADES = "https://servicodados.ibge.gov.br/api/v1/localidades"
    SIDRA = "https://apisidra.ibge.gov.br/values"

    def __init__(self) -> None:
        self.http = ClienteHTTP()

    def todos_municipios(self) -> list[dict]:
        bruto = self.http.json(f"{self.LOCALIDADES}/municipios", {"orderBy": "nome"})
        saida = []
        for m in bruto:
            micro = m.get("microrregiao") or {}
            uf = ((micro.get("mesorregiao") or {}).get("UF")
                  or ((m.get("regiao-imediata") or {}).get("regiao-intermediaria") or {}).get("UF") or {})
            saida.append({"codigo": m["id"], "nome": m["nome"], "uf": uf.get("sigla", "")})
        return saida

    def municipio(self, codigo: int) -> dict | None:
        try:
            m = self.http.json(f"{self.LOCALIDADES}/municipios/{codigo}")
        except ErroFonte:
            return None
        if not isinstance(m, dict) or "id" not in m:
            return None
        micro = m.get("microrregiao") or {}
        uf = ((micro.get("mesorregiao") or {}).get("UF")
              or ((m.get("regiao-imediata") or {}).get("regiao-intermediaria") or {}).get("UF") or {})
        return {"codigo": m["id"], "nome": m["nome"], "uf": uf.get("sigla", ""),
                "regiao": (uf.get("regiao") or {}).get("nome", "")}

    def populacao(self, codigo: int) -> dict | None:
        """Estimativa populacional mais recente (SIDRA tabela 6579, variável 9324)."""
        try:
            linhas = self.http.json(f"{self.SIDRA}/t/6579/n6/{codigo}/v/9324/p/last")
        except ErroFonte:
            return None
        if not isinstance(linhas, list) or len(linhas) < 2:
            return None
        try:
            return {"valor": int(linhas[1]["V"]), "ano": int(linhas[1]["D3N"])}
        except (KeyError, ValueError):
            return None


@lru_cache(maxsize=1)
def _ibge() -> Ibge:
    return Ibge()


@lru_cache(maxsize=1)
def municipios_cache() -> tuple[tuple[str, dict], ...]:
    """Lista de municípios com nome normalizado p/ busca (carregada uma vez por processo)."""
    return tuple((normalizar(m["nome"]), m) for m in _ibge().todos_municipios())


def buscar_municipios(termo: str, limite: int = 10) -> list[dict]:
    alvo = normalizar(termo)
    if len(alvo) < 2:
        return []
    comeca, contem = [], []
    for nome_norm, m in municipios_cache():
        if nome_norm.startswith(alvo):
            comeca.append(m)
        elif alvo in nome_norm:
            contem.append(m)
    return (comeca + contem)[:limite]


@lru_cache(maxsize=512)
def contexto_municipio(codigo: int) -> dict | None:
    m = _ibge().municipio(codigo)
    if not m:
        return None
    return {**m, "populacao": _ibge().populacao(codigo)}
