"""Histórico do resultado primário: inversão do sinal, governos e resumo (sem rede)."""
import pytest

import historico_primario as hp


def _serie(inicio_ano=2011, anos=3, valor=lambda i: 1.0):
    out, i = [], 0
    for a in range(inicio_ano, inicio_ano + anos):
        for m in range(1, 13):
            out.append({"data": f"{a}-{m:02d}-01", "valor": valor(i)})
            i += 1
    return out


def test_sinal_invertido_para_leitura():
    d = hp.montar(_serie(valor=lambda i: 2.0))   # BCB: positivo = déficit
    assert all(p["valor"] == -2.0 for p in d["pontos"])
    assert "déficit de 2,00% do PIB" in d["resumo"]


def test_superavit_aparece_como_positivo():
    d = hp.montar(_serie(valor=lambda i: -1.5))
    assert d["pontos"][0]["valor"] == 1.5 and "superávit" in d["resumo"]


def test_governo_por_mes_e_virada_de_2016():
    assert hp.governo_do_mes("2016-04")["rotulo"] == "Dilma II"
    assert hp.governo_do_mes("2016-05")["rotulo"] == "Temer"
    assert hp.governo_do_mes("2023-01")["rotulo"] == "Lula III"
    assert hp.governo_do_mes("1990-01") is None


def test_um_valor_por_ano_e_resumo_por_governo():
    d = hp.montar(_serie(2011, 4, valor=lambda i: float(i)))
    assert [a["ano"] for a in d["anos"]] == [2011, 2012, 2013, 2014]
    assert d["anos"][0]["mes"] == "2011-12" and d["anos"][0]["valor"] == -11.0
    g = next(x for x in d["governos"] if x["id"] == "dilma1")
    assert g["meses"] == 48 and g["media"] == round(-sum(range(48)) / 48, 2)


def test_serie_curta_e_recusada():
    with pytest.raises(ValueError):
        hp.montar(_serie(anos=1))
