"""Glossário (frontend/assets/glossario.json): os `aliases` alimentam a marcação automática dos termos no texto
(frontend/assets/glossario-auto.js), então precisam ser únicos e não podem ser palavras comuns soltas."""
import json
import re
from pathlib import Path

import pytest

CAMINHO = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "glossario.json"
DADOS = json.loads(CAMINHO.read_text(encoding="utf-8"))
VERBETES = {sigla: v for secao in DADOS.values() for sigla, v in secao.items()}
# palavras que existem na língua comum: marcá-las no texto corrido seria errado ("estoque", "pago", "lei"…)
COMUNS = {"estoque", "fluxo", "pago", "paga", "lei", "dívida", "divida", "juros", "juro", "inflação sozinha", "pl", "des", "custo"}


def test_toda_entrada_tem_os_campos_da_dica():
    for sigla, v in VERBETES.items():
        for campo in ("termo_tecnico", "nome_cidadao", "explicacao_simples"):
            assert str(v.get(campo, "")).strip(), (sigla, campo)


def test_aliases_sao_listas_de_textos_curtos_sem_repeticao_entre_verbetes():
    vistos: dict[str, str] = {}
    for sigla, v in VERBETES.items():
        for a in v.get("aliases", []):
            assert isinstance(a, str) and 2 <= len(a) <= 40, (sigla, a)
            chave = a.lower()
            assert chave not in vistos, f"'{a}' aparece em {vistos[chave]} e em {sigla}"
            vistos[chave] = sigla


def test_aliases_nao_sao_palavras_comuns_soltas():
    for sigla, v in VERBETES.items():
        for a in v.get("aliases", []):
            assert a.lower() not in COMUNS, (sigla, a)


def test_siglas_curtas_so_valem_em_maiusculas():
    # "LOA" ou "PEC" escritos em minúsculas seriam outra coisa; o script exige a escrita exata de quem está em caixa-alta
    for sigla, v in VERBETES.items():
        for a in v.get("aliases", []):
            if len(a) <= 4 and re.fullmatch(r"[A-Za-zÀ-ÿ]+", a):
                assert a.isupper() or a[0].isupper() and len(a) >= 4, (sigla, a)


@pytest.mark.parametrize("sigla", ["JURO_REAL", "COPOM", "TETO_DE_GASTOS", "ARCABOUCO_FISCAL"])
def test_conceitos_usados_no_radar_estao_no_glossario(sigla):
    assert sigla in VERBETES and VERBETES[sigla].get("aliases")


def test_conceitos_do_radar_sao_encontrados_pelos_aliases():
    texto = "A Selic está em 13,75% ao ano e o juro real é de 9,2 p.p.; os juros sobre a dívida somam R$ 1,18 tri."
    achados = {sigla for sigla, v in VERBETES.items() for a in v.get("aliases", []) if re.search(rf"(?<!\w){re.escape(a)}(?!\w)", texto, re.I)}
    assert {"SELIC", "JURO_REAL", "JUROS_NOMINAIS"} <= achados
