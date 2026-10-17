"""Coletor de autoria dos projetos de lei da Câmara — quem propõe, o que é aprovado e sobre qual tema.

Fonte: arquivos abertos oficiais da Câmara dos Deputados (um conjunto por ano), o universo completo:
  https://dadosabertos.camara.leg.br/arquivos/proposicoes/csv/proposicoes-AAAA.csv
  https://dadosabertos.camara.leg.br/arquivos/proposicoesAutores/csv/proposicoesAutores-AAAA.csv
  https://dadosabertos.camara.leg.br/arquivos/proposicoesTemas/csv/proposicoesTemas-AAAA.csv

Os arquivos são grandes (~130 MB por ano), por isso o trabalho é feito AQUI, fora da API, e o resultado
(compacto) é gravado em dados/autoria/autoria_camara.json, que a API só lê.

Regras (todas declaradas na tela):
  * "projeto" = PL, PLP ou PEC apresentado por deputado(a); o ano é o da data de apresentação;
  * "aprovado" = a situação atual é "Transformado em Norma Jurídica" (virou lei ou emenda constitucional);
  * cada deputado(a) que assina como autor(a) recebe o projeto (apoiadores que só assinaram não contam);
  * projetos de comissões, do Executivo e do Senado não entram no ranking de políticos;
  * os temas são os oficiais da Câmara; um projeto pode ter mais de um tema.

Uso (a partir da raiz do projeto):
    python backend/coletor_autoria.py                 # de 2023 (legislatura atual) até o ano corrente
    python backend/coletor_autoria.py --desde 2024
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import RAIZ, cfg  # noqa: E402

log = logging.getLogger("impacto.autoria")

BASE = "https://dadosabertos.camara.leg.br/arquivos"
TIPOS = {"PL", "PLP", "PEC"}
SITUACAO_NORMA_JURIDICA = "1140"   # "Transformado em Norma Jurídica"
COD_AUTOR_DEPUTADO = "10000"       # "Deputado(a)"
SAIDA = RAIZ / "dados" / "autoria" / "autoria_camara.json"

csv.field_size_limit(10_000_000)


def _baixar(nome: str, ano: int, destino: Path) -> Path:
    url = f"{BASE}/{nome}/csv/{nome}-{ano}.csv"
    arq = destino / f"{nome}-{ano}.csv"
    log.info("Baixando %s", url)
    with httpx.stream("GET", url, headers={"User-Agent": cfg.user_agent}, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        with arq.open("wb") as f:
            for bloco in r.iter_bytes(1 << 20):
                f.write(bloco)
    return arq


def _linhas(arq: Path):
    with arq.open(encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f, delimiter=";")


def coletar(desde: int, ate: int) -> dict:
    projetos: list[dict] = []          # índice = posição; os políticos apontam para ela
    politicos: dict[str, dict] = {}
    temas: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="autoria_") as tmp:
        pasta = Path(tmp)
        for ano in range(desde, ate + 1):
            try:
                arq_p = _baixar("proposicoes", ano, pasta)
            except httpx.HTTPError as exc:
                log.warning("Sem arquivo de proposições de %s: %s", ano, exc)
                continue
            arq_a = _baixar("proposicoesAutores", ano, pasta)
            arq_t = _baixar("proposicoesTemas", ano, pasta)

            # 1) projetos do tipo certo, apresentados no próprio ano
            por_id: dict[str, int] = {}
            for r in _linhas(arq_p):
                if r["siglaTipo"] not in TIPOS:
                    continue
                apres = (r.get("dataApresentacao") or "")[:10]
                if not apres or int(apres[:4]) < desde:
                    continue
                por_id[r["id"]] = len(projetos)
                projetos.append({
                    "id": int(r["id"]), "ident": f"{r['siglaTipo']} {r['numero']}/{r['ano']}",
                    "data": apres, "ano": int(apres[:4]),
                    "ementa": (r.get("ementa") or "").strip()[:260], "temas": [],
                    "aprovado": r.get("ultimoStatus_idSituacao") == SITUACAO_NORMA_JURIDICA,
                    "situacao": (r.get("ultimoStatus_descricaoSituacao") or "").strip(),
                    "url": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={r['id']}",
                    "autores": [],
                })
            # 2) autores (só deputados que assinam como proponentes)
            for r in _linhas(arq_a):
                i = por_id.get(r["idProposicao"])
                if i is None or r["codTipoAutor"] != COD_AUTOR_DEPUTADO or r["proponente"] != "1" or not r["idDeputadoAutor"]:
                    continue
                pid = r["idDeputadoAutor"]
                pol = politicos.setdefault(pid, {"id": int(pid), "nome": r["nomeAutor"], "partido": "", "uf": ""})
                pol["partido"] = r["siglaPartidoAutor"] or pol["partido"]
                pol["uf"] = r["siglaUFAutor"] or pol["uf"]
                projetos[i]["autores"].append(int(pid))
            # 3) temas oficiais
            for r in _linhas(arq_t):
                i = por_id.get(r["uriProposicao"].rsplit("/", 1)[-1])
                if i is None:
                    continue
                temas[r["codTema"]] = r["tema"]
                if int(r["codTema"]) not in projetos[i]["temas"]:
                    projetos[i]["temas"].append(int(r["codTema"]))
            log.info("%s: %s projetos acumulados", ano, len(projetos))
    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anos": [desde, ate], "temas": {int(k): v for k, v in temas.items()},
        "politicos": {int(k): v for k, v in politicos.items()},
        "projetos": [p for p in projetos],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--desde", type=int, default=2023, help="primeiro ano (padrão: início da legislatura atual)")
    ap.add_argument("--ate", type=int, default=date.today().year)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dados = coletar(a.desde, a.ate)
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("Gravado %s (%d projetos, %d deputados)", SAIDA, len(dados["projetos"]), len(dados["politicos"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
