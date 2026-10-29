"""Análise das dimensões: regras da fase do ciclo e da tendência estrutural (dados sintéticos, sem rede)."""
import pytest

import analise_dimensoes as ad
import macro


def _trimestres(tris: list[float], inicio_ano: int = 2020) -> dict[str, float]:
    return {f"{inicio_ano + i // 4}-{(i % 4 + 1) * 3:02d}": v for i, v in enumerate(tris)}


def _dados(tris, desemp=8.0, desemp_antes=8.0, ipca=4.0, selic=10.0, selic_antes=10.0):
    meses = [macro._mes_antes("2022-12", n) for n in range(0, 140)]
    de = {m: desemp_antes for m in meses}
    de["2022-11"] = desemp
    de["2022-10"] = desemp
    return {"tri": _trimestres(tris), "acum4": {}, "desemp": de,
            "selic": [{"data": "2019-01-01", "valor": selic_antes}, {"data": "2022-06-01", "valor": selic}],
            "ipca": {"2022-12": ipca}}


@pytest.fixture(autouse=True)
def _meta(monkeypatch):
    monkeypatch.setattr(macro, "_meta_inflacao", lambda mes: (3.0, 1.5))   # teto de 4,5%


def test_recessao_tecnica_com_dois_trimestres_negativos():
    f = ad.fase_em("2022-12", _dados([0.5, 0.4, 0.3, 0.2, 0.1, 0.0, -0.5, -0.8]))
    assert f["fase"] == "recessao"


def test_pico_com_desemprego_na_minima_e_inflacao_acima_do_teto():
    d = _dados([0.5, 0.4, 0.6, 0.5, 0.6, 0.7, 0.8, 0.9], desemp=6.0, desemp_antes=7.0, ipca=6.0)
    d["desemp"]["2022-10"] = d["desemp"]["2022-11"] = 6.0
    assert ad.fase_em("2022-12", d)["fase"] == "pico"


def test_desaceleracao_com_ritmo_menor_e_juro_real_alto():
    f = ad.fase_em("2022-12", _dados([0.8, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 0.3], desemp=8.0, ipca=4.0, selic=12.0, selic_antes=12.0))
    assert f["fase"] == "desaceleracao"


def test_expansao_com_pib_crescendo_e_desemprego_caindo():
    f = ad.fase_em("2022-12", _dados([0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], desemp=7.0, desemp_antes=8.0, ipca=3.0, selic=8.0, selic_antes=8.0))
    assert f["fase"] == "expansao"


def test_sem_dado_do_pib_nao_classifica():
    assert ad.fase_em("2022-12", _dados([0.5, 0.5])) is None


def test_cagr_e_variacao_textual():
    assert ad._cagr({2015: 100.0, 2025: 121.0}, 10)[0] == pytest.approx(1.9, abs=0.05)
    assert ad._variacao_desde({2012: 100.0, 2025: 40.0}, 2012) == "queda de 60%"
