"""Radiografia por período: postura monetária, resultado primário e recessão técnica (dados sintéticos, sem rede)."""
import analise_dimensoes as ad
import macro
import radiografia_periodos as rp


def _dados(tri: dict, selic: list[dict], ipca: float = 4.0):
    meses = {macro._mes_antes("2022-12", n): 8.0 for n in range(0, 140)}
    return {"tri": tri, "acum4": {"2020-12": -3.3}, "desemp": meses, "selic": selic, "ipca": {m: ipca for m in meses}}


def _extra(prim=(1.0, 9.0), div=(74.0, 87.0)):
    return {"primario12": {"2019-12": prim[0], "2020-12": prim[1]}, "dbgg": {"2019-12": div[0], "2020-12": div[1]},
            "renda_real": {2019: 100.0, 2020: 96.0}, "manuf": {2019: 10.0, 2020: 10.5}}


SPEC = {"id": "2020", "rotulo": "2020", "titulo": "t", "inicio": "2020-01", "fim": "2020-12", "ano_ini": 2019, "ano_fim": 2020, "normas": []}


def _periodo(tri, selic, **kw):
    return rp._periodo(SPEC, _dados(tri, selic), _extra(**kw), "2020-12")


def test_fins_de_trimestre():
    assert rp._fins_de_trimestre("2021-01", "2022-12") == ["2021-03", "2021-06", "2021-09", "2021-12", "2022-03", "2022-06", "2022-09", "2022-12"]


def test_recessao_tecnica_conta_o_trimestre_anterior_ao_periodo():
    p = _periodo({"2019-12": -0.5, "2020-03": -2.0, "2020-06": 1.0}, [{"data": "2019-01-01", "valor": 4.5}])
    assert p["ciclo"]["recessao_tecnica"] is True
    p = _periodo({"2019-12": 0.5, "2020-03": -2.0, "2020-06": 1.0}, [{"data": "2019-01-01", "valor": 4.5}])
    assert p["ciclo"]["recessao_tecnica"] is False


def test_postura_afrouxamento_quando_a_selic_cai_dois_pontos_ou_mais():
    p = _periodo({"2020-03": 0.1}, [{"data": "2019-01-01", "valor": 4.5}, {"data": "2020-08-01", "valor": 2.0}])
    assert p["monetaria"]["postura"] == "afrouxamento"
    assert (p["monetaria"]["selic_inicio"], p["monetaria"]["selic_fim"], p["monetaria"]["selic_minima"]) == (4.5, 2.0, 2.0)


def test_postura_restritiva_com_juros_altos_e_estaveis():
    p = _periodo({"2020-03": 0.1}, [{"data": "2019-01-01", "valor": 13.75}], )
    assert p["monetaria"]["postura"] == "restritiva"


def test_resultado_primario_piorou_e_divida_subiu():
    p = _periodo({"2020-03": 0.1}, [{"data": "2019-01-01", "valor": 4.5}])
    assert p["fiscal"]["situacao"] == "piorou" and p["fiscal"]["variacao_pp"] == 8.0
    assert p["fiscal"]["variacao_divida_pp"] == 13.0


def test_crescimento_per_capita_do_periodo():
    p = _periodo({"2020-03": 0.1}, [{"data": "2019-01-01", "valor": 4.5}])
    assert p["estrutura"]["pib_per_capita_real_cresc_ano"] == -4.0
