"""Coletor e analisador dos planos de governo de candidatos à Presidência.

Pipeline:  TSE (candidatos + PDF do plano)  ->  texto  ->  IA (mesmo prompt p/ todos)
           ->  conferência das citações contra o PDF  ->  PostgreSQL

Garantias de neutralidade implementadas aqui:
  1. todos os candidatos passam pelo MESMO prompt, esquema, modelo e parâmetros,
     em ordem alfabética;
  2. cada proposta carrega um trecho literal; o código confere se ele existe no
     PDF e DESCARTA o que não existe (a IA não "inventa" promessas);
  3. a página da citação é calculada pelo código, não declarada pela IA;
  4. dados pessoais sensíveis do TSE (CPF, título, bens) nunca são gravados.

Uso (a partir da raiz do projeto):
    python backend/coletor_candidatos.py                  # todos os deferidos
    python backend/coletor_candidatos.py --so-segundo-turno
    python backend/coletor_candidatos.py --sq 280002542548 --forcar
    python backend/coletor_candidatos.py --sem-ia         # só cadastra e baixa PDFs
    python backend/coletor_candidatos.py --fonte manifesto

Se o download do PDF for bloqueado (403), salve o arquivo manualmente em
dados/planos/<SQ_CANDIDATO>.pdf e rode de novo — o coletor usa o arquivo local.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

import db
from config import cfg
from fontes import ErroFonte, Tse, extrair_paginas_pdf, localizar_trecho, normalizar, texto_paginado
from llm import ErroConfiguracaoLLM, ErroLLM, RecusaLLM, analisar, credenciais_configuradas, modelo_em_uso
from prompts import PROMPT_PLANO, montar_usuario_plano
from schemas import AnalisePlano, PropostaPlano

log = logging.getLogger("impacto.candidatos")

MIN_CARACTERES_POR_PAGINA = 200  # abaixo disso o PDF provavelmente é imagem escaneada


@dataclass
class Placar:
    ok: int = 0
    pulados: int = 0
    pendentes: int = 0
    falhas: int = 0
    descartadas: int = 0
    detalhes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Origem dos dados dos candidatos
# --------------------------------------------------------------------------- #
def candidatos_do_manifesto() -> list[dict]:
    if not cfg.manifesto_candidatos.exists():
        raise ErroFonte(f"Manifesto não encontrado: {cfg.manifesto_candidatos}")
    dados = json.loads(cfg.manifesto_candidatos.read_text(encoding="utf-8"))
    log.info("Manifesto local: %d candidatos (snapshot de %s).", len(dados["candidatos"]), dados.get("gerado_em"))
    return dados["candidatos"]


def candidatos_do_tse(tse: Tse) -> list[dict]:
    lista = tse.listar_candidatos(cfg.tse_ano, cfg.tse_eleicao_id, cargo=1)
    resumidos = []
    for c in lista:
        detalhe = tse.detalhe(cfg.tse_ano, cfg.tse_eleicao_id, int(c["id"]))
        resumidos.append(Tse.resumir_candidato(detalhe))
    log.info("TSE: %d candidatos à Presidência.", len(resumidos))
    return resumidos


def obter_candidatos(args: argparse.Namespace, tse: Tse) -> list[dict]:
    if args.fonte == "manifesto":
        return candidatos_do_manifesto()
    try:
        return candidatos_do_tse(tse)
    except ErroFonte as exc:
        if args.fonte == "api":
            raise
        log.warning("API do TSE indisponível (%s). Usando o manifesto local.", exc)
        return candidatos_do_manifesto()


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def obter_pdf(tse: Tse, sq: int, arquivo_id: int) -> tuple[bytes | None, str]:
    """(bytes, origem). Prioridade: arquivo manual > cache > download."""
    manual = cfg.pasta_planos / f"{sq}.pdf"
    if manual.exists():
        return manual.read_bytes(), "manual"
    cache = cfg.pasta_planos / "cache" / f"{arquivo_id}.pdf"
    if cache.exists():
        return cache.read_bytes(), "cache"
    try:
        conteudo = tse.baixar_pdf(arquivo_id)
    except ErroFonte as exc:
        log.warning("Download do PDF %s falhou: %s", arquivo_id, exc)
        return None, str(exc)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(conteudo)
    return conteudo, "download"


# --------------------------------------------------------------------------- #
# Conferência das citações (anti-alucinação)
# --------------------------------------------------------------------------- #
def verificar_citacoes(analise: AnalisePlano, paginas: list[str]) -> tuple[list[dict], int]:
    """Mantém só as propostas cujo trecho literal existe no PDF.

    Devolve (temas_verificados, quantidade_descartada). Cada tema volta como dict
    pronto para gravação, com a página calculada pelo código.
    """
    paginas_norm = [normalizar(p) for p in paginas]
    descartadas = 0
    temas: list[dict] = []

    for tema in analise.temas:
        validas: list[tuple[PropostaPlano, int]] = []
        for proposta in tema.propostas:
            pagina = localizar_trecho(proposta.trecho_literal, paginas_norm)
            if pagina is None:
                descartadas += 1
                log.debug("Citação não encontrada no PDF (descartada): %.90s", proposta.trecho_literal)
                continue
            validas.append((proposta, pagina))

        mencionado, resumo = tema.mencionado, tema.resumo_neutro
        if tema.mencionado and not validas:
            # A IA disse que o tema aparece, mas nenhuma citação resistiu à conferência.
            # Não afirmamos "silêncio": sinalizamos que não deu para verificar.
            resumo = ("O plano parece tratar deste tema, mas nenhuma citação pôde ser conferida "
                      "automaticamente com o PDF. Consulte o documento original.")
        temas.append({"categoria": tema.categoria, "mencionado": mencionado, "resumo": resumo, "propostas": validas})
    return temas, descartadas


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #
def gravar_candidato(conn, c: dict) -> None:
    conn.execute(
        """
        INSERT INTO candidatos (sq_candidato, ano_eleicao, cargo, nome_urna, numero, sigla_partido,
                                situacao_candidatura, totalizacao, vice)
        VALUES (%(sq)s, %(ano)s, 'Presidente', %(nome)s, %(numero)s, %(partido)s, %(situacao)s, %(totalizacao)s, %(vice)s)
        ON CONFLICT (sq_candidato) DO UPDATE SET
            nome_urna = EXCLUDED.nome_urna, numero = EXCLUDED.numero, sigla_partido = EXCLUDED.sigla_partido,
            situacao_candidatura = EXCLUDED.situacao_candidatura, totalizacao = EXCLUDED.totalizacao,
            vice = COALESCE(EXCLUDED.vice, candidatos.vice)
        """,
        {"sq": c["sq_candidato"], "ano": cfg.tse_ano, "nome": c["nome_urna"], "numero": c.get("numero"),
         "partido": c.get("sigla_partido"), "situacao": c.get("situacao_candidatura"),
         "totalizacao": c.get("totalizacao"), "vice": c.get("vice")},
    )


def _limpar_texto(texto: str) -> str:
    """O PostgreSQL rejeita NUL (\\x00) e UTF-8 inválido; PDFs às vezes trazem ambos."""
    return texto.replace("\x00", "").encode("utf-8", "ignore").decode("utf-8")


def gravar_plano(conn, c: dict, arquivo: dict, *, status: str, erro: str | None = None,
                 paginas: int | None = None, caracteres: int | None = None, sha: str | None = None,
                 analise: AnalisePlano | None = None, temas: list[dict] | None = None,
                 descartadas: int = 0, modelo: str | None = None,
                 paginas_texto: list[str] | None = None) -> None:
    linha = conn.execute(
        """
        INSERT INTO planos_governo (sq_candidato, arquivo_id, nome_arquivo, url_pdf, paginas, caracteres,
                                    pdf_sha256, status, resumo_geral, analise_bruta, propostas_descartadas,
                                    modelo_llm, erro, analisado_em)
        VALUES (%(sq)s, %(arq)s, %(nome)s, %(url)s, %(pag)s, %(car)s, %(sha)s, %(status)s, %(resumo)s,
                %(bruta)s, %(desc)s, %(modelo)s, %(erro)s, %(quando)s)
        ON CONFLICT (arquivo_id) DO UPDATE SET
            nome_arquivo = EXCLUDED.nome_arquivo, paginas = EXCLUDED.paginas, caracteres = EXCLUDED.caracteres,
            pdf_sha256 = EXCLUDED.pdf_sha256, status = EXCLUDED.status, resumo_geral = EXCLUDED.resumo_geral,
            analise_bruta = EXCLUDED.analise_bruta, propostas_descartadas = EXCLUDED.propostas_descartadas,
            modelo_llm = EXCLUDED.modelo_llm, erro = EXCLUDED.erro, analisado_em = EXCLUDED.analisado_em
        RETURNING id
        """,
        {"sq": c["sq_candidato"], "arq": arquivo["arquivo_id"], "nome": arquivo.get("nome"),
         "url": Tse.url_pdf(arquivo["arquivo_id"]), "pag": paginas, "car": caracteres, "sha": sha,
         "status": status, "resumo": analise.resumo_geral if analise else None,
         "bruta": Jsonb(analise.model_dump()) if analise else None, "desc": descartadas,
         "modelo": modelo, "erro": erro, "quando": datetime.now().astimezone() if analise else None},
    ).fetchone()
    plano_id = linha["id"]

    # Um candidato tem um único plano "vigente": remove análises de arquivos anteriores.
    conn.execute("DELETE FROM planos_governo WHERE sq_candidato = %s AND id <> %s", (c["sq_candidato"], plano_id))
    conn.execute("DELETE FROM temas_candidato WHERE plano_id = %s", (plano_id,))
    conn.execute("DELETE FROM propostas_candidato WHERE plano_id = %s", (plano_id,))

    # Texto integral por página: base da busca por palavra-chave (funciona sem IA).
    if paginas_texto is not None:
        conn.execute("DELETE FROM paginas_plano WHERE plano_id = %s", (plano_id,))
        linhas = [(plano_id, n, _limpar_texto(t)) for n, t in enumerate(paginas_texto, start=1) if t and t.strip()]
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO paginas_plano (plano_id, pagina, texto) VALUES (%s, %s, %s)", linhas)

    for tema in temas or []:
        conn.execute(
            "INSERT INTO temas_candidato (plano_id, categoria_id, mencionado, resumo_neutro) VALUES (%s, %s, %s, %s)",
            (plano_id, tema["categoria"], tema["mencionado"], tema["resumo"]),
        )
        for proposta, pagina in tema["propostas"]:
            conn.execute(
                """
                INSERT INTO propostas_candidato (plano_id, sq_candidato, categoria_id, descricao, trecho_literal,
                    pagina, tem_meta_numerica, tem_prazo, tem_fonte_recurso, canal_execucao, perfis_beneficiados)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (plano_id, c["sq_candidato"], tema["categoria"], proposta.descricao, proposta.trecho_literal,
                 pagina, proposta.tem_meta_numerica, proposta.tem_prazo, proposta.tem_fonte_de_recurso,
                 proposta.canal_de_execucao, list(dict.fromkeys(proposta.perfis_beneficiados))),
            )


def plano_ja_analisado(conn, arquivo_id: int, sha: str, sem_ia: bool = False) -> bool:
    """Nada a fazer: PDF idêntico e (já analisado, ou — sem IA — já lido e indexado para busca)."""
    linha = conn.execute(
        "SELECT p.status, p.pdf_sha256, (SELECT count(*) FROM paginas_plano g WHERE g.plano_id = p.id) AS paginas "
        "FROM planos_governo p WHERE p.arquivo_id = %s", (arquivo_id,)
    ).fetchone()
    if not linha or linha["pdf_sha256"] != sha:
        return False
    if linha["status"] == "ok":
        return True
    return sem_ia and linha["status"] == "pendente" and linha["paginas"] > 0


# --------------------------------------------------------------------------- #
# Processamento de um candidato
# --------------------------------------------------------------------------- #
def processar(c: dict, tse: Tse, args: argparse.Namespace, placar: Placar) -> None:
    nome = c["nome_urna"]
    planos = sorted(c.get("planos") or [], key=lambda a: a["arquivo_id"])
    with db.conexao_avulsa() as conn:
        gravar_candidato(conn, c)

    if not planos:
        placar.pendentes += 1
        placar.detalhes.append(f"{nome}: sem plano de governo registrado no TSE")
        log.warning("%s: nenhum plano de governo encontrado no registro.", nome)
        return

    arquivo = planos[-1]  # o mais recente (maior idArquivo)
    if len(planos) > 1:
        log.info("%s: %d arquivos de plano; usando o mais recente (%s).", nome, len(planos), arquivo["nome"])

    conteudo, origem = obter_pdf(tse, c["sq_candidato"], arquivo["arquivo_id"])
    if conteudo is None:
        with db.conexao_avulsa() as conn:
            gravar_plano(conn, c, arquivo, status="aguardando_pdf",
                         erro=f"Download bloqueado ou falhou ({origem}). Salve o PDF em "
                              f"dados/planos/{c['sq_candidato']}.pdf e rode novamente.")
        placar.pendentes += 1
        placar.detalhes.append(f"{nome}: PDF indisponível — salve em dados/planos/{c['sq_candidato']}.pdf")
        return

    sha = hashlib.sha256(conteudo).hexdigest()
    with db.conexao_avulsa() as conn:
        if not args.forcar and plano_ja_analisado(conn, arquivo["arquivo_id"], sha, args.sem_ia):
            placar.pulados += 1
            log.info("= %s: plano inalterado; nada a refazer.", nome)
            return

    try:
        paginas = extrair_paginas_pdf(conteudo)
    except ErroFonte as exc:
        with db.conexao_avulsa() as conn:
            gravar_plano(conn, c, arquivo, status="falha", erro=str(exc), sha=sha)
        placar.falhas += 1
        log.error("x %s: %s", nome, exc)
        return

    caracteres = sum(len(p) for p in paginas)
    media = caracteres / max(len(paginas), 1)
    if media < MIN_CARACTERES_POR_PAGINA:
        with db.conexao_avulsa() as conn:
            gravar_plano(conn, c, arquivo, status="sem_texto", paginas=len(paginas), caracteres=caracteres, sha=sha,
                         erro="PDF sem texto extraível (provavelmente imagem escaneada); requer OCR.")
        placar.pendentes += 1
        placar.detalhes.append(f"{nome}: PDF sem texto (precisa de OCR)")
        log.warning("! %s: PDF com pouco texto (%.0f caracteres/página); requer OCR.", nome, media)
        return

    if caracteres > cfg.max_chars_plano:
        with db.conexao_avulsa() as conn:
            gravar_plano(conn, c, arquivo, status="texto_longo", paginas=len(paginas), caracteres=caracteres,
                         sha=sha, erro=f"{caracteres:,} caracteres excedem MAX_CHARS_PLANO ({cfg.max_chars_plano:,}).",
                         paginas_texto=paginas)
        placar.pendentes += 1
        placar.detalhes.append(f"{nome}: plano muito longo ({caracteres:,} caracteres)")
        return

    if args.sem_ia:
        with db.conexao_avulsa() as conn:
            gravar_plano(conn, c, arquivo, status="pendente", paginas=len(paginas), caracteres=caracteres, sha=sha,
                         paginas_texto=paginas)
        placar.pendentes += 1
        log.info("+ %s: PDF lido (%d páginas), análise pendente (--sem-ia).", nome, len(paginas))
        return

    try:
        analise, info = analisar(
            PROMPT_PLANO,
            montar_usuario_plano(nome, c.get("sigla_partido") or "", texto_paginado(paginas)),
            AnalisePlano,
        )
    except ErroConfiguracaoLLM:
        raise
    except (RecusaLLM, ErroLLM) as exc:
        with db.conexao_avulsa() as conn:
            gravar_plano(conn, c, arquivo, status="falha", paginas=len(paginas), caracteres=caracteres, sha=sha,
                         erro=str(exc)[:500], modelo=modelo_em_uso(), paginas_texto=paginas)
        placar.falhas += 1
        placar.detalhes.append(f"{nome}: análise falhou — {exc}")
        log.error("x %s: %s", nome, exc)
        return

    temas, descartadas = verificar_citacoes(analise, paginas)
    with db.conexao_avulsa() as conn:
        gravar_plano(conn, c, arquivo, status="ok", paginas=len(paginas), caracteres=caracteres, sha=sha,
                     analise=analise, temas=temas, descartadas=descartadas, modelo=info.get("modelo"),
                     paginas_texto=paginas)
    total = sum(len(t["propostas"]) for t in temas)
    placar.ok += 1
    placar.descartadas += descartadas
    log.info("+ %s: %d propostas verificadas, %d descartadas (citação não encontrada). [%s tokens entrada]",
             nome, total, descartadas, info.get("tokens_entrada"))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def interpretar_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analisa, com o mesmo critério, os planos de governo registrados no TSE.")
    p.add_argument("--fonte", choices=["auto", "api", "manifesto"], default="auto",
                   help="auto: tenta a API do TSE e cai para o manifesto local (padrão)")
    p.add_argument("--sq", type=int, help="processa apenas este SQ_CANDIDATO")
    p.add_argument("--so-segundo-turno", action="store_true", help="apenas candidatos marcados '2º turno' pelo TSE")
    p.add_argument("--incluir-todos", action="store_true",
                   help="inclui candidaturas indeferidas, renunciadas etc. (padrão: só deferidas / 2º turno)")
    p.add_argument("--forcar", action="store_true", help="reanalisa mesmo que o PDF não tenha mudado")
    p.add_argument("--ia", choices=["auto", "sim", "nao"], default="auto",
                   help="análise por IA: auto = só se houver chave configurada (padrão), sim = exige, nao = só lê os PDFs")
    p.add_argument("--sem-ia", action="store_true", help="atalho para --ia nao: cadastra candidatos e lê os PDFs")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def elegivel(c: dict, args: argparse.Namespace) -> bool:
    if args.sq:
        return c["sq_candidato"] == args.sq
    if args.so_segundo_turno:
        return (c.get("totalizacao") or "").strip().lower().startswith("2")
    if args.incluir_todos:
        return True
    situacao = (c.get("situacao_candidatura") or "").lower()
    return situacao.startswith("deferido") or (c.get("totalizacao") or "").lower().startswith("2")


def main(argv: list[str] | None = None) -> int:
    args = interpretar_argumentos(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    tse, placar = Tse(), Placar()

    modo_ia = "nao" if args.sem_ia else args.ia
    usar_ia = {"sim": True, "nao": False, "auto": credenciais_configuradas()}[modo_ia]
    if modo_ia == "auto" and not usar_ia:
        log.info("Sem chave de IA configurada: os planos serão lidos e indexados para busca, sem análise. "
                 "Preencha o .env, ou use --ia sim se a credencial vem do login do CLI.")
    args.sem_ia = not usar_ia

    try:
        candidatos = obter_candidatos(args, tse)
    except (ErroFonte, json.JSONDecodeError, KeyError) as exc:
        log.error("Não foi possível obter a lista de candidatos: %s", exc)
        return 1

    coleta_id = None
    try:
        with db.conexao_avulsa() as conn:
            coleta_id = conn.execute(
                "INSERT INTO coletas (tipo, com_ia) VALUES ('candidatos', %s) RETURNING id", (usar_ia,)
            ).fetchone()["id"]
    except Exception as exc:  # noqa: BLE001 - o registro nunca deve derrubar a coleta
        log.warning("Não foi possível registrar a coleta: %s", exc)

    # Ordem alfabética: nenhum candidato é tratado antes ou depois por qualquer critério.
    escolhidos = sorted((c for c in candidatos if elegivel(c, args)), key=lambda c: normalizar(c["nome_urna"]))
    log.info("%d candidatos a processar (de %d).", len(escolhidos), len(candidatos))
    if not escolhidos:
        return 0

    try:
        for c in escolhidos:
            processar(c, tse, args, placar)
    except ErroConfiguracaoLLM as exc:
        log.error("Configuração da IA inválida: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        log.error("Erro inesperado: %s (o banco está no ar e o schema.sql foi aplicado?)", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrompido pelo usuário.")

    if coleta_id is not None:
        try:
            with db.conexao_avulsa() as conn:
                conn.execute(
                    "UPDATE coletas SET concluida_em = now(), novos = %s, so_andamento = %s, pendentes = %s, "
                    "falhas = %s WHERE id = %s",
                    (placar.ok, placar.pulados, placar.pendentes, placar.falhas, coleta_id),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Não foi possível concluir o registro da coleta: %s", exc)

    log.info("Resumo: %d analisados | %d já atualizados | %d pendentes | %d falhas | %d citações descartadas",
             placar.ok, placar.pulados, placar.pendentes, placar.falhas, placar.descartadas)
    for linha in placar.detalhes:
        log.info("  - %s", linha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
