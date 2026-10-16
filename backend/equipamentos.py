"""Equipamentos públicos de um município, para o mapa: saúde (CNES/DATASUS) e demais serviços (OpenStreetMap).

Fontes verificadas em out/2026:

* Saúde — API de Dados Abertos da Saúde, CNES (https://apidadosabertos.saude.gov.br/cnes/estabelecimentos):
  latitude e longitude, esfera, natureza jurídica, tipo de unidade, telefone e turno. É público o
  estabelecimento cuja natureza jurídica começa por 1 (administração pública); hospitais e UPAs de outra
  natureza só entram se atendem pelo SUS, marcados como "conveniado ao SUS".
* Educação, segurança e assistência — OpenStreetMap (Overpass API). A esfera quase nunca é informada: é
  INFERIDA pelo nome e pelo operador e marcada como tal. Escolas privadas não entram.
* INEP (Censo Escolar) e MDS (CadSUAS/CRAS) exigem autenticação nas APIs públicas e não foram usados.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

from config import RAIZ
from fontes import ClienteHTTP, ErroFonte

log = logging.getLogger("impacto.equipamentos")

CNES = "https://apidadosabertos.saude.gov.br/cnes/estabelecimentos"
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter")
PASTA = RAIZ / "dados" / "equipamentos"
TTL_CNES_S = 12 * 3600
TTL_OSM_S = 14 * 24 * 3600

_http = ClienteHTTP(pausa=0.02, timeout=60.0)
_trava = threading.Lock()
_cache: dict[tuple, tuple[float, Any]] = {}
_osm_em_curso: set[int] = set()

# Tipos de unidade do CNES que o mapa consulta (TP_UNID)
TIPOS_CNES = {
    1: "Posto de saúde", 2: "UBS / Centro de saúde", 4: "Policlínica", 5: "Hospital geral", 7: "Hospital especializado",
    15: "Unidade mista", 20: "Pronto-socorro geral", 21: "Pronto-socorro especializado", 36: "Centro de especialidades / clínica",
    39: "Unidade de apoio ao diagnóstico e terapia", 43: "Farmácia", 50: "Vigilância em saúde", 69: "Hemocentro",
    70: "CAPS (saúde mental)", 71: "Centro de apoio à saúde da família", 73: "UPA / Pronto atendimento", 74: "Academia da saúde",
    85: "Centro de imunização",
}
TIPOS_HOSPITALARES = {5, 7, 15, 20, 21, 73}
LIMITE_PAGINAS_POR_TIPO = 40

SIGLAS = {"UBS", "UPA", "CAPS", "CSF", "CRAS", "CREAS", "SAMU", "AME", "CEMEI", "EMEI", "EMEF", "EMEIEF", "EMEB", "ETEC", "FATEC",
          "CEI", "CMEI", "IFSP", "USP", "UNICAMP", "UNESP", "INSS", "DP", "DDM", "PM", "SP", "CTI", "CEO", "UTI", "PS", "CER", "NASF", "ESF", "PSF", "II", "III", "IV"}
MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "em", "para", "a", "o"}


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(t: str) -> str:
    s = unicodedata.normalize("NFKD", t or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _bonito(nome: str) -> str:
    """CNES entrega nomes em caixa alta: capitaliza, preservando siglas."""
    palavras = []
    for i, p in enumerate(re.split(r"(\s+)", nome.strip())):
        if not p or p.isspace():
            palavras.append(p)
            continue
        base = re.sub(r"[^\wÀ-ÿ]", "", p).upper()
        if base in SIGLAS:
            palavras.append(p.upper())
        elif p.lower() in MINUSCULAS and i > 0:
            palavras.append(p.lower())
        else:
            palavras.append(p[:1].upper() + p[1:].lower())
    return "".join(palavras)


# --------------------------------------------------------------------------- #
# Saúde: CNES
# --------------------------------------------------------------------------- #
def _cnes_tipo(codigo6: int, tipo: int) -> list[dict]:
    saida: list[dict] = []
    for pagina in range(LIMITE_PAGINAS_POR_TIPO):
        r = _http.json(CNES, {"codigo_municipio": codigo6, "codigo_tipo_unidade": tipo, "limit": 20, "offset": pagina * 20})
        dados = r.get("estabelecimentos") if isinstance(r, dict) else None
        if not dados:
            break
        saida += dados
        if len(dados) < 20:
            break
    return saida


def _feicao_cnes(x: dict) -> dict | None:
    lat, lon = x.get("latitude_estabelecimento_decimo_grau"), x.get("longitude_estabelecimento_decimo_grau")
    tipo = x.get("codigo_tipo_unidade")
    natureza = str(x.get("descricao_natureza_juridica_estabelecimento") or "")
    publico = natureza.startswith("1")
    sus = (x.get("estabelecimento_faz_atendimento_ambulatorial_sus") or "").upper() == "SIM" or x.get("estabelecimento_possui_atendimento_hospitalar")
    if lat is None or lon is None:
        return None
    if publico:
        esfera = {"MUNICIPAL": "Municipal", "ESTADUAL": "Estadual", "FEDERAL": "Federal"}.get((x.get("descricao_esfera_administrativa") or "").upper(), "Pública")
        origem, conveniado = "cadastro (CNES)", False
    elif tipo in TIPOS_HOSPITALARES and sus and natureza.startswith("3"):
        esfera, origem, conveniado = "Conveniado ao SUS", "cadastro (CNES): entidade sem fins lucrativos que atende pelo SUS", True
    else:
        return None
    endereco = ", ".join(p for p in (_bonito(x.get("endereco_estabelecimento") or ""), str(x.get("numero_estabelecimento") or "").strip(),
                                     _bonito(x.get("bairro_estabelecimento") or "")) if p)
    return {
        "id": f"cnes-{x.get('codigo_cnes')}", "nome": _bonito(x.get("nome_fantasia") or x.get("nome_razao_social") or "Estabelecimento de saúde"),
        "categoria": "saude", "tipo": TIPOS_CNES.get(tipo, f"Estabelecimento de saúde (tipo {tipo})"), "esfera": esfera,
        "esfera_origem": origem, "publico": publico, "conveniado_sus": conveniado,
        "lat": float(lat), "lon": float(lon), "endereco": endereco or None, "cep": x.get("codigo_cep_estabelecimento"),
        "telefone": (x.get("numero_telefone_estabelecimento") or "").strip() or None,
        "funcionamento": (x.get("descricao_turno_atendimento") or "").capitalize() or None,
        "fonte": "CNES / DATASUS", "url_fonte": "https://cnes.datasus.gov.br/pages/estabelecimentos/consulta.jsp",
        "atualizado_em": (x.get("data_atualizacao") or "")[:10] or None,
    }


def _saude(codigo7: int) -> list[dict]:
    codigo6 = codigo7 // 10
    itens: dict[str, dict] = {}
    for tipo in TIPOS_CNES:
        for x in _cnes_tipo(codigo6, tipo):
            f = _feicao_cnes(x)
            if f:
                itens[f["id"]] = f
    return list(itens.values())


# --------------------------------------------------------------------------- #
# Demais serviços: OpenStreetMap
# --------------------------------------------------------------------------- #
_RX_MUN = re.compile(r"\b(emei|emef|emeief|emeb|emebs|cemei|cmei|cei|emeei|prefeitura|municipal|guarda civil|guarda municipal)\b")
_RX_EST = re.compile(r"\b(estadual|governo do estado|estado de|policia civil|policia militar|corpo de bombeiros|bombeiros|etec|fatec|unicamp|usp|unesp|delegacia|distrito policial|dp|ddm)\b|^(ee|e e) ")
_RX_FED = re.compile(r"\b(federal|uniao|policia rodoviaria federal|policia federal|inss|ifsp|instituto federal|ufscar|unifesp)\b")


def _esfera_osm(tags: dict) -> tuple[str, str]:
    txt = _norm(f"{tags.get('operator', '')} {tags.get('name', '')}")
    if _RX_FED.search(txt):
        return "Federal", "inferida pelo nome ou operador (OpenStreetMap)"
    if _RX_EST.search(txt):
        return "Estadual", "inferida pelo nome ou operador (OpenStreetMap)"
    if _RX_MUN.search(txt):
        return "Municipal", "inferida pelo nome ou operador (OpenStreetMap)"
    if tags.get("operator:type") == "government":
        return "Pública (esfera não informada)", "tag operator:type=government (OpenStreetMap)"
    return "Não informada", "não informada no OpenStreetMap"


CAT_OSM = {"school": ("educacao", "Escola"), "kindergarten": ("educacao", "Educação infantil / creche"), "childcare": ("educacao", "Creche"),
           "college": ("educacao", "Ensino técnico / superior"), "university": ("educacao", "Universidade"), "library": ("educacao", "Biblioteca"),
           "police": ("seguranca", "Delegacia / base policial"), "fire_station": ("seguranca", "Corpo de Bombeiros"),
           "social_facility": ("assistencia", "Assistência social"), "courthouse": ("outros", "Fórum / tribunal"), "townhall": ("outros", "Prefeitura")}


def _feicao_osm(e: dict) -> dict | None:
    tags = e.get("tags") or {}
    amenity = tags.get("amenity")
    if amenity not in CAT_OSM or not tags.get("name"):
        return None
    lat = e.get("lat") if e.get("type") == "node" else (e.get("center") or {}).get("lat")
    lon = e.get("lon") if e.get("type") == "node" else (e.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None
    categoria, tipo = CAT_OSM[amenity]
    nome = tags["name"]
    if re.search(r"\b(cras|creas)\b", _norm(nome)):
        categoria, tipo = "assistencia", "CRAS / CREAS"
    esfera, origem = _esfera_osm(tags)
    if tags.get("operator:type") in ("private", "private_non_profit") and esfera in ("Não informada", "Pública (esfera não informada)"):
        return None  # escola ou serviço privado: fora do mapa de equipamentos públicos
    if categoria == "educacao" and esfera == "Não informada" and re.search(r"\b(colegio|objetivo|anglo|sesi|senai|senac|cursinho|particular)\b", _norm(nome)):
        return None
    endereco = ", ".join(p for p in (tags.get("addr:street"), tags.get("addr:housenumber")) if p)
    return {
        "id": f"osm-{e['type'][0]}{e['id']}", "nome": nome, "categoria": categoria, "tipo": tipo, "esfera": esfera, "esfera_origem": origem,
        "publico": esfera in ("Municipal", "Estadual", "Federal", "Pública (esfera não informada)"), "conveniado_sus": False,
        "lat": float(lat), "lon": float(lon), "endereco": endereco or None, "cep": tags.get("addr:postcode"),
        "telefone": tags.get("phone") or tags.get("contact:phone"), "funcionamento": tags.get("opening_hours"),
        "fonte": "OpenStreetMap", "url_fonte": f"https://www.openstreetmap.org/{e['type']}/{e['id']}", "atualizado_em": None,
    }


def _overpass(consulta: str) -> dict:
    ultimo: Exception | None = None
    for base in OVERPASS:
        for tentativa in range(2):
            try:
                resp = ClienteHTTP(pausa=0.0, timeout=180.0)._cliente.post(base, data={"data": consulta})
                if resp.status_code == 200:
                    return resp.json()
                ultimo = ErroFonte(f"{resp.status_code} em {base}")
            except Exception as exc:  # noqa: BLE001
                ultimo = exc
            time.sleep(2)
    raise ErroFonte(f"Overpass indisponível: {ultimo}")


def _relacao_osm(codigo7: int) -> int | None:
    r = _overpass(f'[out:json][timeout:90];rel["IBGE:GEOCODIGO"="{codigo7}"][boundary=administrative];out ids;')
    ids = [e["id"] for e in r.get("elements", [])]
    return ids[0] if ids else None


def _baixar_osm(codigo7: int) -> None:
    try:
        rel = _relacao_osm(codigo7)
        if not rel:
            raise ErroFonte("Município não localizado no OpenStreetMap")
        consulta = (f'[out:json][timeout:120];area({3600000000 + rel})->.a;(nwr["amenity"~"^(school|kindergarten|college|university|childcare|library|'
                    f'police|fire_station|social_facility|townhall|courthouse)$"](area.a););out center tags;')
        brutos = _overpass(consulta).get("elements", [])
        itens = [f for f in (_feicao_osm(e) for e in brutos) if f]
        PASTA.mkdir(parents=True, exist_ok=True)
        (PASTA / f"osm_{codigo7}.json").write_text(json.dumps({"atualizado_em": _agora(), "itens": itens}, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("OSM indisponível para %s: %s", codigo7, exc)
        PASTA.mkdir(parents=True, exist_ok=True)
        (PASTA / f"osm_{codigo7}.erro").write_text(_agora(), encoding="utf-8")
    finally:
        with _trava:
            _osm_em_curso.discard(codigo7)


def _osm(codigo7: int) -> tuple[str, list[dict], str | None]:
    """(estado, itens, atualizado_em): estado = ok | carregando | indisponivel."""
    arq = PASTA / f"osm_{codigo7}.json"
    try:
        if arq.exists():
            d = json.loads(arq.read_text(encoding="utf-8"))
            if time.time() - arq.stat().st_mtime < TTL_OSM_S:
                return "ok", d["itens"], d.get("atualizado_em")
    except (OSError, ValueError):
        pass
    err = PASTA / f"osm_{codigo7}.erro"
    if err.exists() and time.time() - err.stat().st_mtime < 3600:
        return "indisponivel", [], None
    with _trava:
        if codigo7 not in _osm_em_curso:
            _osm_em_curso.add(codigo7)
            threading.Thread(target=_baixar_osm, args=(codigo7,), daemon=True, name=f"osm-{codigo7}").start()
    antigo: list[dict] = []
    try:
        if arq.exists():
            antigo = json.loads(arq.read_text(encoding="utf-8"))["itens"]
    except (OSError, ValueError):
        pass
    return "carregando", antigo, None


# --------------------------------------------------------------------------- #
# API do módulo
# --------------------------------------------------------------------------- #
def listar(codigo7: int) -> dict:
    chave = ("cnes", codigo7)
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < TTL_CNES_S:
        saude = _cache[chave][1]
    else:
        saude = _saude(codigo7)
        _cache[chave] = (agora, saude)
    estado_osm, osm, atualizado_osm = _osm(codigo7)
    itens = saude + osm
    centro = None
    if itens:
        centro = {"lat": round(sum(i["lat"] for i in itens) / len(itens), 5), "lon": round(sum(i["lon"] for i in itens) / len(itens), 5)}
    contagem: dict[str, int] = {}
    for i in itens:
        contagem[i["esfera"]] = contagem.get(i["esfera"], 0) + 1
    return {
        "municipio": codigo7, "consultado_em": _agora(), "centro": centro, "itens": itens, "contagem_por_esfera": contagem,
        "fontes": {
            "cnes": {"nome": "CNES / DATASUS (API de Dados Abertos da Saúde)", "itens": len(saude), "url": "https://apidadosabertos.saude.gov.br/"},
            "osm": {"nome": "OpenStreetMap (colaborativo)", "estado": estado_osm, "itens": len(osm), "atualizado_em": atualizado_osm,
                    "url": "https://www.openstreetmap.org/copyright"},
        },
        "avisos": [
            "Escolas, segurança e assistência vêm do OpenStreetMap, que é colaborativo e pode estar incompleto; a esfera é inferida pelo nome.",
            "Escolas do Censo Escolar (INEP) e CRAS/CREAS do CadSUAS (MDS) não têm API aberta sem cadastro e não entram no mapa.",
        ],
    }
