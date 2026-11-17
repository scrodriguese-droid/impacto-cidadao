"""Dinâmica da dívida: os três motores sempre somam a variação do saldo, e o diagnóstico aponta o maior fator."""
import pytest

import divida_dinamica as dd


def _series(saldo_dez_anterior, saldo_dez, prim_mes, jur_mes, ano=2024):
    saldo = {f"{ano - 1}-12": saldo_dez_anterior, f"{ano}-12": saldo_dez}
    prim = {f"{ano}-{m:02d}": prim_mes for m in range(1, 13)}
    jur = {f"{ano}-{m:02d}": jur_mes for m in range(1, 13)}
    return saldo, prim, jur


def test_motores_somam_a_variacao_e_o_cambio_e_o_residuo():
    # saldo +350 bi; primário −100 bi (superávit); juros +500 bi; sobra −50 bi de câmbio e ajustes (o exemplo didático)
    saldo, prim, jur = _series(5_000_000, 5_350_000, -100_000 / 12, 500_000 / 12)
    a = dd._por_ano(saldo, prim, jur)[0]
    assert (a["variacao_bi"], a["fiscal_bi"], a["juros_bi"]) == (350.0, -100.0, 500.0)
    assert a["cambio_bi"] == pytest.approx(-50.0)
    assert a["fiscal_bi"] + a["juros_bi"] + a["cambio_bi"] == pytest.approx(a["variacao_bi"])
    assert a["principal"] == "juros" and a["completo"] is True
    assert "não porque o governo gastou demais" in a["diagnostico"] and "R$ 350,0 bi" in a["diagnostico"]


def test_ano_com_fluxo_incompleto_e_omitido():
    saldo, prim, jur = _series(1_000_000, 1_100_000, 0, 8_000)
    del jur["2024-05"]
    assert dd._por_ano(saldo, prim, jur) == []


def test_ano_em_andamento_usa_o_ultimo_mes_e_diz_ate_quando():
    saldo = {"2025-12": 1_000_000, "2026-03": 1_100_000}
    prim = {f"2026-{m:02d}": 10_000 for m in (1, 2, 3)}
    jur = {f"2026-{m:02d}": 30_000 for m in (1, 2, 3)}
    a = dd._por_ano(saldo, prim, jur)[0]
    assert a["completo"] is False and a["ate_mes"] == 3 and "janeiro a março de 2026" in a["diagnostico"]


def test_diagnostico_cobre_deficit_fiscal_e_cambio():
    assert "déficit primário" in dd.diagnostico(2020, 600, 500, 100, 0)["texto"]
    assert dd.diagnostico(2020, -300, -400, 100, 0)["principal"] == "fiscal"
    d = dd.diagnostico(2020, 200, 0, 50, 150)
    assert d["principal"] == "cambio" and "câmbio e ajustes" in d["texto"]
