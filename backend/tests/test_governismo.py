"""De olho no governo: cálculo de governismo, fidelidade, presença e coesão (sem rede)."""
import coletor_governismo as cg
import governismo


def _votos(vid, deps):
    """deps: {id: (partido, código)}"""
    return [(vid, str(i), f"Dep {i}", "SP", "", p, c) for i, (p, c) in deps.items()]


# 4 votações do Plenário; o Governo orientou Sim em v1/v2/v3 e Não em v4 (v5 sem orientação)
PLEN = {"v1": "2025-03-01", "v2": "2025-03-02", "v3": "2025-03-03", "v4": "2025-03-04", "v5": "2025-03-05"}
GOV = {"v1": "Sim", "v2": "Sim", "v3": "Sim", "v4": "Não"}


def _base():
    votos = []
    # dep 1 (A): sempre com o Governo; dep 2 (A): contra, abstém, falta, vota Não no v4 (com o Governo)
    # dep 3 (A) e dep 4 (B) completam as maiorias partidárias; dep 5 (B) entra só no v4 e v5 (suplente)
    votos += _votos("v1", {1: ("A", "S"), 2: ("A", "N"), 3: ("A", "S"), 4: ("B", "N"), 6: ("A", "S")})
    votos += _votos("v2", {1: ("A", "S"), 2: ("A", "A"), 3: ("A", "S"), 4: ("B", "N"), 6: ("A", "S")})
    votos += _votos("v3", {1: ("A", "S"), 3: ("A", "S"), 4: ("B", "N"), 6: ("A", "S")})  # dep 2 faltou
    votos += _votos("v4", {1: ("A", "S"), 2: ("A", "N"), 3: ("A", "S"), 4: ("B", "N"), 5: ("B", "N"), 6: ("A", "S")})
    votos += _votos("v5", {1: ("A", "S"), 2: ("A", "S"), 3: ("A", "S"), 4: ("B", "N"), 5: ("B", "N"), 6: ("A", "S")})
    return votos


def _por_id(r):
    return {d["id"]: d for d in r["deputados"]}


def test_governismo_conta_abstencao_e_falta_contra():
    d = _por_id(cg.consolidar(PLEN, GOV, _base()))
    # dep 1: v1-v3 Sim (com), v4 Sim contra a orientação Não → 3 de 4
    assert d[1]["anos"]["2025"][:4] == [3, 1, 0, 0]
    # dep 2: v1 Não (contra), v2 abstenção, v3 ausente, v4 Não (com) → 1 com, 1 contra, 1 abst., 1 ausente
    assert d[2]["anos"]["2025"][:4] == [1, 1, 1, 1]


def test_suplente_so_e_cobrado_a_partir_do_primeiro_voto():
    d = _por_id(cg.consolidar(PLEN, GOV, _base()))
    # dep 5 só votou em v4 e v5: v1-v3 ficam fora do exercício dele; v4 (Não) com o Governo
    assert d[5]["anos"]["2025"][:4] == [1, 0, 0, 0]
    assert d[5]["primeiro_voto"] == "2025-03-04"


def test_fidelidade_usa_a_maioria_do_proprio_partido():
    d = _por_id(cg.consolidar(PLEN, GOV, _base()))
    x = d[2]["anos"]["2025"]
    # partido A, só Sim/Não: v1 (S,N,S,S→maioria S) dep 2 votou N: infiel; v4 maioria S (1,3,6 votaram S) dep 2 votou N: infiel;
    # v5 maioria S, dep 2 votou S: fiel; v2 (abstenção) e v3 (faltou) não contam
    assert x[4:6] == [1, 3]


def test_presenca_conta_votos_registrados_no_periodo():
    d = _por_id(cg.consolidar(PLEN, GOV, _base()))
    # dep 2 esteve em 5 votações nominais no período dele e registrou voto em 4 (faltou em v3)
    assert d[2]["anos"]["2025"][6:8] == [4, 5]
    assert d[5]["anos"]["2025"][6:8] == [2, 2]


def test_presidir_a_sessao_fica_fora_do_governismo():
    votos = _base() + _votos("v6", {1: ("A", "P"), 3: ("A", "S"), 4: ("B", "S")})
    plen, gov = {**PLEN, "v6": "2025-03-06"}, {**GOV, "v6": "Sim"}
    d = _por_id(cg.consolidar(plen, gov, votos))
    assert sum(d[1]["anos"]["2025"][:4]) == 4  # v6 não entra: ele presidiu
    assert sum(d[3]["anos"]["2025"][:4]) == 5


def test_coesao_do_partido():
    r = cg.consolidar(PLEN, GOV, _base())
    soma, n = r["coesao"]["2025"]["A"]
    # v1 e v4: 3 sim × 1 não → 0,5; v2, v3 e v5: unânimes → 1
    assert n == 5 and round(soma, 4) == 4.0
    # partido B nunca teve 3 votantes numa mesma votação: fica de fora da coesão
    assert "B" not in r["coesao"]["2025"]


def test_rotulo_proposicao():
    assert cg.rotulo_proposicao("Aprovada a Subemenda ao Projeto de Lei Complementar nº 281, de 2019, adotada") == "PLP 281/2019"
    assert cg.rotulo_proposicao("Rejeitado o Projeto de Lei nº 2.338, de 2023.") == "PL 2338/2023"
    assert cg.rotulo_proposicao("Aprovada a Proposta de Emenda à Constituição nº 45, de 2019") == "PEC 45/2019"
    assert cg.rotulo_proposicao("Aprovado o Requerimento de Urgência (Art. 155 do RICD).") is None


def test_faixas():
    assert governismo.faixa(70) == "alto" and governismo.faixa(69.9) == "medio"
    assert governismo.faixa(30) == "medio" and governismo.faixa(29.9) == "baixo" and governismo.faixa(None) is None


def test_projeto_valida_o_rotulo():
    import pytest
    for ruim in ("", "221/2019", "XYZ 1/2020", "PEC 221-2019"):
        with pytest.raises(ValueError):
            governismo.projeto(ruim)


def test_apelidos_sao_lidos_na_hora(tmp_path, monkeypatch):
    arq = tmp_path / "apelidos.json"
    arq.write_text('{"PEC 1/2020": "Teste"}', encoding="utf-8")
    monkeypatch.setattr(governismo, "ARQ_APELIDOS", arq)
    assert governismo._apelidos() == {"PEC 1/2020": "Teste"}
    arq.write_text("não é json", encoding="utf-8")
    assert governismo._apelidos() == {}


def test_pessoa_monta_foto_e_perfil_por_casa():
    dep = governismo._pessoa({"nomeParlamentar": "A", "codigoDeputado": 10, "codigoParlamentar": 99, "siglaPartidoFiliacao": "PT", "uf": "SP"})
    sen = governismo._pessoa({"nomeParlamentar": "B", "codigoParlamentar": 99})
    assert dep["foto"].endswith("/10.jpg") and dep["url"].endswith("/deputados/10")
    assert sen["foto"].endswith("senador99.jpg") and "senadores/senador" in sen["url"]
