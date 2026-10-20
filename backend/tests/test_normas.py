"""Hierarquia das normas (Pirâmide de Kelsen): classificação dos tipos de proposição e catálogo servido à tela."""
import typing
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

import main
import normas


@pytest.mark.parametrize("sigla, esperado", [
    ("PEC", "constitucional"), ("PLP", "complementar"), ("PL", "ordinaria"), ("PLN", "ordinaria"),
    ("MPV", "medida_provisoria"), ("PDL", "ato_congresso"), ("PRC", "ato_congresso"),
    (" pec ", "constitucional"), ("pl", "ordinaria"),
])
def test_classifica_cada_sigla_no_seu_degrau(sigla, esperado):
    assert normas.classificar(sigla) == esperado


def test_sigla_desconhecida_ou_ambigua_fica_sem_classificacao():
    # "PLC" significa coisas diferentes na Câmara, no Senado antigo e nos municípios: melhor não classificar do que errar
    for s in ("PLC", "XYZ", "", None):
        assert normas.classificar(s) is None


def test_cada_sigla_pertence_a_um_unico_tipo_e_a_pirâmide_vai_do_mais_rigoroso_ao_mais_simples():
    todas = [s for n in normas.HIERARQUIA for s in n["siglas"]]
    assert len(todas) == len(set(todas))
    assert [n["ordem"] for n in normas.HIERARQUIA] == [1, 2, 3, 4, 5]
    assert [n["degrau"] for n in normas.HIERARQUIA] == [1, 2, 3, 3, 3]      # lei ordinária, MP e atos do Congresso: mesmo degrau
    assert [n["id"] for n in normas.HIERARQUIA][:2] == ["constitucional", "complementar"]


def test_rigor_de_cada_tipo_bate_com_a_constituicao():
    por_id = {n["id"]: n for n in normas.HIERARQUIA}
    assert "3/5" in por_id["constitucional"]["rigor"] and "308" in por_id["constitucional"]["rigor"] and "49" in por_id["constitucional"]["rigor"]
    assert "dois turnos" in por_id["constitucional"]["rigor"].lower()
    assert "maioria absoluta" in por_id["complementar"]["rigor"].lower() and "257" in por_id["complementar"]["rigor"] and "41" in por_id["complementar"]["rigor"]
    assert "maioria simples" in por_id["ordinaria"]["rigor"].lower()
    assert "60 dias" in por_id["medida_provisoria"]["rigor"] and "120" in por_id["medida_provisoria"]["rigor"]
    assert "sem sanção" in por_id["ato_congresso"]["rigor"]


def test_todo_tipo_tem_rotulo_explicacao_e_exemplo_em_linguagem_simples():
    for n in normas.HIERARQUIA:
        for campo in ("rotulo", "nome", "vira", "curto", "o_que_e", "rigor", "exemplo"):
            assert n[campo].strip(), (n["id"], campo)
        assert n["rotulo"] == n["rotulo"].upper() and len(n["rotulo"]) <= 20       # cabe numa etiqueta
    assert normas.ADMINISTRATIVOS["o_que_e"] and normas.ADMINISTRATIVOS["limite"]


def test_filtro_da_api_aceita_exatamente_os_ids_do_catalogo():
    anotacao = typing.get_type_hints(main.listar_proposicoes, include_extras=True)["norma"]      # Annotated[Literal[...] | None, Query]
    literal = next(a for a in typing.get_args(typing.get_args(anotacao)[0]) if a is not type(None))
    assert set(typing.get_args(literal)) == set(normas.IDS)
    assert normas.siglas_da_norma("constitucional") == ["PEC"] and normas.siglas_da_norma("inexistente") == []


def test_proposicao_traz_o_degrau_da_norma():
    linha = defaultdict(lambda: None, sigla_tipo="PLP", identificacao="PLP 10/2026")
    assert main._proposicao_json(linha)["norma"] == "complementar"


def test_endpoint_entrega_o_catalogo_sem_precisar_do_banco():
    r = TestClient(main.app).get("/api/normas")
    assert r.status_code == 200
    corpo = r.json()
    assert [n["id"] for n in corpo["hierarquia"]] == list(normas.IDS)
    assert "estados e municípios" in corpo["nota"] and corpo["fonte"]["url"].startswith("https://www.planalto.gov.br/")
