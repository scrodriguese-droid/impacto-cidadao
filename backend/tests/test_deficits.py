"""Déficits do RREO: o resultado OFICIAL do ente tem prioridade sobre a nossa aproximação (despesa empenhada − receita)."""
import pytest

import economia


def _l(cod, coluna, valor):
    return {"cod_conta": cod, "coluna": coluna, "valor": valor}


def _a1(receita, despesa_empenhada):
    return [_l("TotalReceitas", "Até o Bimestre", receita), _l("TotalDespesas", "DESPESAS EMPENHADAS ATÉ O BIMESTRE", despesa_empenhada)]


def _a6_aproximacao(receita_prim, despesa_prim_empenhada):
    return [_l("RREO6TotalReceitaPrimaria", "RECEITAS REALIZADAS (a)", receita_prim),
            _l("RREO6TotalDespesaPrimaria", "DESPESAS EMPENHADAS", despesa_prim_empenhada)]


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    monkeypatch.setattr(economia, "_ipca_acumulado", lambda ano, mes: None)   # o operacional não é o assunto aqui


def test_uniao_usa_o_resultado_oficial_e_guarda_a_aproximacao():
    # números reais de ago/2026: empenhada 2.691,2 − receita 2.097,0 = 594,2 de "déficit"; o RREO publica −94,9 (déficit de 94,9)
    a6 = _a6_aproximacao(2097.0e9, 2691.2e9) + [_l("RREO6ResultadoPrimarioEstadosMunicipios", "VALOR", -94.87e9),
                                                 _l("ResultadoNominalAcimaDaLinha", "VALOR", -802.44e9)]
    d = economia._deficits(_a1(4000e9, 5046.8e9), a6, 2026, 4)
    assert d["primario"]["valor"] == pytest.approx(94.87e9) and d["primario"]["oficial"] is True
    assert d["primario"]["aproximado"] == pytest.approx(594.2e9)
    assert d["nominal"]["valor"] == pytest.approx(802.44e9) and d["nominal"]["oficial"] is True
    assert "oficial" in d["primario"]["base"]


def test_municipio_com_superavit_oficial_nao_aparece_como_deficit():
    # a aproximação dava DÉFICIT (+0,32), mas o RREO publica SUPERÁVIT de 0,13 (positivo no RREO): aqui vira −0,13
    a6 = _a6_aproximacao(1.00e9, 1.32e9) + [_l("ResultadoPrimarioSemRPPSAcimaDaLinha", "VALOR", 0.13e9),
                                            _l("ResultadoNominalAcimaDaLinhaSemRPPS", "VALOR", 0.13e9)]
    d = economia._deficits(_a1(1.5e9, 1.8e9), a6, 2026, 4)
    assert d["primario"]["valor"] == pytest.approx(-0.13e9) and d["primario"]["aproximado"] > 0
    assert d["nominal"]["valor"] == pytest.approx(-0.13e9)


def test_sem_resultado_oficial_cai_na_aproximacao_e_diz_que_nao_e_oficial():
    d = economia._deficits(_a1(1000.0, 1200.0), _a6_aproximacao(900.0, 1100.0), 2026, 4)
    assert d["primario"]["oficial"] is False and d["nominal"]["oficial"] is False
    assert d["primario"]["valor"] == pytest.approx(200.0) == pytest.approx(d["primario"]["aproximado"])
    assert d["nominal"]["valor"] == pytest.approx(200.0)


def test_sem_rpps_tem_prioridade_sobre_com_rpps():
    a6 = _a6_aproximacao(1.0e9, 1.1e9) + [_l("ResultadoPrimarioComRPPSAcimaDaLinha", "VALOR", 0.15e9),
                                          _l("ResultadoPrimarioSemRPPSAcimaDaLinha", "VALOR", 0.13e9)]
    assert economia._deficits(_a1(1.5e9, 1.8e9), a6, 2026, 4)["primario"]["valor"] == pytest.approx(-0.13e9)
