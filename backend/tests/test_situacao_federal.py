"""Visão "Governo Federal" da situação fiscal: receita, despesa e dívida declaradas pelo próprio ente no RREO (Anexo 06),
sem misturar com as séries do Banco Central (setor público consolidado)."""
import economia


def _linha(cod, coluna, valor):
    return {"cod_conta": cod, "coluna": coluna, "valor": valor}


# Valores da União no 4º bimestre de 2026 (RREO Anexo 06), em reais
A6 = [
    _linha("RREO6TotalReceitaPrimaria", "RECEITAS REALIZADAS (a)", 2_096_979_235_519.67),
    _linha("RREO6TotalDespesaPrimaria", "DESPESAS EMPENHADAS", 2_691_212_349_932.04),
    _linha("RREO6TotalDespesaPrimaria", "DESPESAS PAGAS (a)", 1_974_876_257_274.12),
    _linha("RREO6TotalDespesaPrimaria", "RESTOS A PAGAR PROCESSADOS PAGOS (b)", 100_546_209_425.79),
    _linha("RREO6TotalDespesaPrimaria", "PAGOS (c)", 108_938_653_399.75),
    _linha("RREO6AjusteMetodologicoDoResultadoPrimarioAcimaDaLinha", "VALOR", -7_487_594_022.33),
    _linha("DividaConsolidada", "Em 31/12/2024 (a)", 11_545_342_915_303.7),
    _linha("DividaConsolidada", "Até o Bimestre 2026 (b)", 12_264_073_856_966.4),
    _linha("DividaConsolidadaLiquida", "Até o Bimestre 2026 (b)", 8_824_948_368_201.45),
]


def test_fluxo_federal_soma_despesa_paga_com_os_restos_a_pagar_e_fecha_com_o_resultado_primario():
    f = economia._fluxo_federal(A6, 2026, 4)
    assert f["receita_primaria"] == 2_096_979_235_519.67
    assert f["despesa_primaria_paga"] == round(1_974_876_257_274.12 + 100_546_209_425.79 + 108_938_653_399.75, 2)
    # receita − despesa paga + ajuste metodológico = resultado primário oficial (déficit de R$ 94,87 bi)
    assert round(f["receita_primaria"] - f["despesa_primaria_paga"] + f["ajuste_metodologico"], 2) == -94_869_478_602.32
    assert f["periodo"] == "acumulado até o 4º bimestre de 2026"


def test_fluxo_federal_sem_os_dados_do_anexo_6_fica_indisponivel_em_vez_de_inventar():
    assert economia._fluxo_federal([], 2026, 4) is None
    assert economia._fluxo_federal([_linha("RREO6TotalReceitaPrimaria", "RECEITAS REALIZADAS (a)", 1.0)], 2026, 4) is None


def test_estoque_federal_traz_a_divida_bruta_e_a_liquida_da_posicao_atual_nao_a_de_31_12():
    e = economia._estoque_federal(A6, 2026, 4)
    por_id = {i["id"]: i for i in e["itens"]}
    assert por_id["bruta"]["valor_rs"] == 12_264_073_856_966.4        # "Até o Bimestre", não "Em 31/12"
    assert por_id["liquida"]["valor_rs"] == 8_824_948_368_201.45
    assert por_id["liquida"]["valor_rs"] < por_id["bruta"]["valor_rs"] and "caixa" in por_id["liquida"]["nota"]
    assert e["periodo"] == "posição até o 4º bimestre de 2026" and "SICONFI" in e["fonte"]["nome"]


def test_estoque_federal_sem_divida_no_anexo_6_fica_indisponivel():
    assert economia._estoque_federal([], 2026, 4) is None
    so_bruta = economia._estoque_federal([_linha("DividaConsolidada", "Até o Bimestre 2026 (b)", 10.0)], 2026, 4)
    assert [i["id"] for i in so_bruta["itens"]] == ["bruta"]
