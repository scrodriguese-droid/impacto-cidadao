"""Histórico da dívida: as medidas de cada marco saem da série oficial (dados sintéticos, sem rede)."""
import historico_divida as hd


def _serie(inicio: str, valores: list[float]) -> list[dict]:
    ano, mes = int(inicio[:4]), int(inicio[5:7])
    saida = []
    for i, v in enumerate(valores):
        n = ano * 12 + mes - 1 + i
        saida.append({"mes": f"{n // 12}-{n % 12 + 1:02d}", "valor": v})
    return saida


def test_marcos_medem_a_variacao_dentro_do_periodo():
    # dívida bruta: 50% em 2020-01, sobe até 60% em 2020-06 e volta a 55% em 2020-12
    valores = [50 + i * 2 for i in range(6)] + [60 - i * 5 / 6 for i in range(1, 7)]
    series = {"dbgg": _serie("2020-01", valores), "dlsp": []}
    pandemia = next(m for m in hd.marcos(series, "2020-12") if m["id"] == "pandemia")
    med = pandemia["medidas"][0]
    assert med["serie"] == "dbgg"
    assert (med["de"]["mes"], med["ate"]["mes"]) == ("2020-01", "2020-12")
    assert med["maximo"]["mes"] == "2020-06" and med["maximo"]["valor"] == 60
    assert med["variacao_pp"] == round(valores[-1] - 50, 2)


def test_marco_sem_dados_no_periodo_fica_sem_medidas():
    series = {"dbgg": _serie("2010-01", [50.0, 51.0]), "dlsp": []}
    marcos = hd.marcos(series, "2010-02")
    assert next(m for m in marcos if m["id"] == "recessao")["medidas"] == []


def test_ultimo_marco_vai_ate_o_ultimo_mes_da_serie():
    series = {"dbgg": _serie("2023-01", [70.0, 71.0, 72.0]), "dlsp": []}
    ultimo = next(m for m in hd.marcos(series, "2023-03") if m["id"] == "arcabouco")
    assert ultimo["fim"] == "2023-03"
    assert ultimo["medidas"][0]["variacao_pp"] == 2.0
