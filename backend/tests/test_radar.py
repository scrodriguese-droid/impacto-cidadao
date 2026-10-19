"""Radar econômico: regras do diagnóstico fiscal e do painel econômico e social (dados sintéticos, sem rede)."""
import pytest

import macro


def _meses(inicio_ano: int, n: int) -> list[str]:
    return [f"{inicio_ano + i // 12}-{i % 12 + 1:02d}" for i in range(n)]


def _fiscal(monkeypatch, divida, nominal, ipca=5.0, selic=11.0, primario=1.0, desde="2021-01"):
    """Roda o radar fiscal com séries de 36 meses (2020-01 em diante); `divida` e `nominal` são listas ou números."""
    meses = _meses(2020, 36)
    fx = lambda v, i: v[i] if isinstance(v, list) else v  # noqa: E731
    dados = {"dbgg_pib": {m: fx(divida, i) for i, m in enumerate(meses)},
             "nominal12": {m: fx(nominal, i) for i, m in enumerate(meses)},
             "primario12": {m: primario for m in meses},
             "ipca12": {m: ipca for m in meses}}
    monkeypatch.setattr(macro, "_dados_mensais", lambda: (dados, [{"data": "2019-01-01", "valor": selic}]))
    return macro.classificar_historico(desde)


def test_fiscal_sustentavel_sem_sinais(monkeypatch):
    h = _fiscal(monkeypatch, 60.0, 2.0, ipca=5.0, selic=7.0)
    assert h[-1]["estagio"] == "sustentavel"


def test_fiscal_sob_pressao_com_dois_sinais_e_divida_estavel(monkeypatch):
    # dívida alta e parada, déficit nominal alto: 2 sinais ligados, mas a dívida não sobe
    h = _fiscal(monkeypatch, 80.0, 7.0, ipca=5.0, selic=7.0)
    assert h[-1]["estagio"] == "pressao"
    assert sum(s["disparado"] for s in h[-1]["sinais"]) == 2


def test_fiscal_em_deterioracao_com_dois_sinais_e_divida_subindo(monkeypatch):
    divida = [70.0 + i * 0.5 for i in range(36)]  # +6 p.p. em 12 meses
    h = _fiscal(monkeypatch, divida, 7.0, ipca=5.0, selic=7.0)
    assert h[-1]["estagio"] == "deterioracao"


def test_fiscal_um_sinal_com_divida_subindo_vira_pressao(monkeypatch):
    divida = [50.0 + i * 0.3 for i in range(36)]  # sobe 3,6 p.p. em 12 meses, sem chegar a 75%
    h = _fiscal(monkeypatch, divida, 6.0, ipca=5.0, selic=7.0)
    assert h[-1]["estagio"] == "pressao"


def test_fiscal_margem_de_saida_evita_oscilar_no_limite(monkeypatch):
    # liga em 75, só desliga abaixo de 70: 74 → 76 → 72 (continua ligado) → 69 (desliga)
    divida = [74.0, 76.0, 72.0, 72.0, 69.0] + [69.0] * 31
    h = _fiscal(monkeypatch, divida, 6.0, ipca=5.0, selic=7.0, desde="2020-01")
    ligado = [next(s for s in x["sinais"] if s["id"] == "divida")["disparado"] for x in h[:5]]
    assert ligado == [False, True, True, True, False]


def test_fiscal_alta_da_divida_tambem_tem_margem_de_saida(monkeypatch):
    # +1,5 p.p. em 12 meses liga a "dívida em alta"; depois só +0,5 (ainda subindo) mantém ligada
    divida = [70.0] * 12 + [71.5] * 6 + [70.5] * 18
    h = _fiscal(monkeypatch, divida, 6.0, ipca=5.0, selic=7.0, desde="2021-01")
    assert h[0]["var_divida_12m"] == pytest.approx(1.5)
    # 1 sinal (nominal) + dívida em alta => sob pressão enquanto a dívida seguir acima do nível de 12 meses antes
    assert h[0]["estagio"] == "pressao"


def _social(monkeypatch, desemprego_antes, desemprego, ipca, pib_acum4, ipca_antes=None):
    """Um único mês de referência (2025-06) com os valores pedidos; 12 meses antes = `*_antes`."""
    ipca_antes = ipca if ipca_antes is None else ipca_antes
    pontos_ipca = [{"data": f"{m}-01", "valor": ipca_antes if m < "2025-01" else ipca} for m in _meses(2023, 36)]
    monkeypatch.setattr(macro, "serie", lambda k, *a, **kw: {"pontos": pontos_ipca if k == "ipca12" else [{"data": "2023-01-01", "valor": 3.0}]})
    monkeypatch.setattr(macro, "_sidra_desemprego", lambda: [
        {"data": "2024-05-01", "valor": desemprego_antes, "referencia": "mar-abr-mai 2024"},
        {"data": "2025-05-01", "valor": desemprego, "referencia": "mar-abr-mai 2025"}])
    monkeypatch.setattr(macro, "_sidra_pib", lambda: {"acum4": {"2025-03": {"valor": pib_acum4, "ref": "1º trimestre 2025"}},
                                                      "tri": {"2025-03": {"valor": 0.4, "ref": "1º trimestre 2025"}}})
    return next(x for x in macro.classificar_social_historico("2025-06") if x["mes"] == "2025-06")


def test_social_melhora_com_dois_indicadores_melhores(monkeypatch):
    x = _social(monkeypatch, desemprego_antes=7.0, desemprego=6.0, ipca=3.0, pib_acum4=2.5)
    por_id = {i["id"]: i["status"] for i in x["itens"]}
    assert por_id == {"desemprego": "melhora", "inflacao": "estavel", "pib": "melhora"}
    assert x["estagio"] == "melhora"


def test_social_um_indicador_bom_nao_basta_para_melhora(monkeypatch):
    x = _social(monkeypatch, desemprego_antes=6.0, desemprego=6.1, ipca=3.0, pib_acum4=2.5)
    assert x["estagio"] == "estabilidade"


def test_social_piora_quando_dois_indicadores_pioram(monkeypatch):
    x = _social(monkeypatch, desemprego_antes=6.0, desemprego=7.0, ipca=7.0, pib_acum4=0.5)
    por_id = {i["id"]: i["status"] for i in x["itens"]}
    assert por_id["desemprego"] == "piora" and por_id["inflacao"] == "piora"
    assert x["estagio"] == "piora"


def test_social_inflacao_fora_da_meta_e_inflacao_de_volta_a_banda(monkeypatch):
    fora = _social(monkeypatch, 6.0, 6.0, ipca=6.0, pib_acum4=2.0)
    assert next(i for i in fora["itens"] if i["id"] == "inflacao")["rotulo"] == "Acima do teto da meta"
    volta = _social(monkeypatch, 6.0, 6.0, ipca=3.5, pib_acum4=2.0, ipca_antes=6.0)
    assert next(i for i in volta["itens"] if i["id"] == "inflacao")["status"] == "melhora"
