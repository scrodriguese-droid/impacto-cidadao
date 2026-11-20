"""A meta da LDO contra o resultado de 12 meses: sinais, faixas e textos."""
import lei_x_realidade as lr

META = lr.METAS_LDO[2026]


def _serie(valor: float) -> list[dict]:
    return [{"data": "2026-08-01", "valor": 9.9}, {"data": "2026-09-01", "valor": valor}]   # BCB: positivo = déficit


def test_meta_da_ldo_2026_confere_com_a_lei():
    assert META["meta_pib"] == 0.25 and META["meta_bi"] == 34.26
    assert META["inferior_pib"] == 0.0 and META["superior_pib"] == 0.5     # meta ± 0,25 ponto do PIB
    assert round(META["meta_pib"] - META["banda_pp"], 2) == META["inferior_pib"]


def test_deficit_abaixo_do_limite_inferior():
    r = lr.comparar_meta(META, _serie(0.39))        # déficit de 0,39% do PIB
    assert r["estado"] == "abaixo" and r["realidade"]["resultado_pib"] == -0.39 and r["realidade"]["quem"] == "déficit"
    assert "0,39% do PIB abaixo do limite inferior" in r["leitura"]
    assert r["realidade"]["referencia"] == "setembro de 2026"       # usa o último ponto, não o anterior


def test_superavit_abaixo_da_meta_mas_dentro_da_faixa():
    r = lr.comparar_meta(META, _serie(-0.10))       # superávit de 0,10%: abaixo de 0,25, acima de 0
    assert r["estado"] == "na_faixa" and r["realidade"]["quem"] == "superávit"


def test_superavit_na_meta_ou_acima():
    assert lr.comparar_meta(META, _serie(-0.25))["estado"] == "atingida"
    assert lr.comparar_meta(META, _serie(-0.80))["estado"] == "atingida"


def test_zero_exato_conta_como_dentro_da_faixa():
    assert lr.comparar_meta(META, _serie(0.0))["estado"] == "na_faixa"


def test_sem_serie_nao_ha_comparacao():
    assert lr.comparar_meta(META, []) is None


def test_ano_sem_ldo_cadastrada_nao_compara():
    assert lr.meta_primaria(1999) is None


def test_nota_avisa_que_a_meta_e_anual():
    assert "ano-calendário" in lr.comparar_meta(META, _serie(0.39))["nota"]
