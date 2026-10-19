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


def _social(monkeypatch, desemprego_antes, desemprego, ipca, pib_acum4, ipca_antes=None, renda=None, informal=None):
    """Um único mês de referência (2025-06) com os valores pedidos; 12 meses antes = `*_antes`.
    `renda` e `informal` são pares (antes, agora); sem eles, o indicador não entra no painel."""
    ipca_antes = ipca if ipca_antes is None else ipca_antes
    monkeypatch.setattr(macro, "_sidra_rendimento", lambda: {} if renda is None else {
        "2024-05": {"valor": renda[0], "ref": "mar-abr-mai 2024"}, "2025-05": {"valor": renda[1], "ref": "mar-abr-mai 2025"}})
    monkeypatch.setattr(macro, "_sidra_informalidade", lambda: {} if informal is None else {
        "2024-03": {"valor": informal[0], "ref": "1º trimestre 2024"}, "2025-03": {"valor": informal[1], "ref": "1º trimestre 2025"}})
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


def _status(x, id_):
    return next(i for i in x["itens"] if i["id"] == id_)["status"]


@pytest.mark.parametrize("antes,agora,esperado", [(3000, 3150, "melhora"), (3000, 2900, "piora"), (3000, 3010, "estavel")])
def test_social_rendimento_real_compara_com_12_meses_antes(monkeypatch, antes, agora, esperado):
    x = _social(monkeypatch, 6.0, 6.0, ipca=3.0, pib_acum4=2.0, renda=(antes, agora))
    assert _status(x, "rendimento") == esperado
    item = next(i for i in x["itens"] if i["id"] == "rendimento")
    assert item["formato"] == "reais" and item["valor"] == agora and item["variacao_pct"] == pytest.approx((agora / antes - 1) * 100, abs=0.01)


@pytest.mark.parametrize("antes,agora,esperado", [(38.0, 37.0, "melhora"), (37.0, 38.0, "piora"), (38.0, 38.2, "estavel")])
def test_social_informalidade_menor_e_melhor(monkeypatch, antes, agora, esperado):
    x = _social(monkeypatch, 6.0, 6.0, ipca=3.0, pib_acum4=2.0, informal=(antes, agora))
    assert _status(x, "informalidade") == esperado


def test_social_renda_ajuda_a_virar_melhora(monkeypatch):
    sem = _social(monkeypatch, 6.0, 6.0, ipca=3.0, pib_acum4=2.0)
    com = _social(monkeypatch, 6.0, 6.0, ipca=3.0, pib_acum4=2.0, renda=(3000, 3200))
    assert sem["estagio"] == "estabilidade" and com["estagio"] == "melhora"


def test_social_queda_da_renda_com_inflacao_alta_segura_a_melhora(monkeypatch):
    # desemprego e PIB melhoram, mas a inflação está fora da meta e o poder de compra caiu: não é "melhora"
    x = _social(monkeypatch, desemprego_antes=7.0, desemprego=6.0, ipca=7.0, pib_acum4=2.5, renda=(3000, 2800))
    assert _status(x, "rendimento") == "piora" and _status(x, "inflacao") == "piora"
    assert x["estagio"] == "estabilidade"


def test_cotidiano_mostra_os_dois_lados_sem_repetir_a_lista(monkeypatch):
    soc = _social(monkeypatch, 7.0, 6.0, ipca=7.0, pib_acum4=2.5, renda=(3000, 3200), informal=(38.0, 37.0))
    fis = {"estagio": "deterioracao", "sinais": [{"id": "juro_real", "disparado": True}]}
    ctx = {"selic": 14.0, "ipca12": 7.0, "juro_real": 7.0, "juros_pib": 8.9, "juros_rs": 1.18e12}
    c = macro._cotidiano(fis, soc, ctx)
    renda = next(i["texto"] for i in c["itens"] if i["tema"] == "Emprego, renda e preços")
    assert "desemprego" in renda and "rendimento" in renda.lower() and "acima do de um ano atrás" in renda
    assert "informalidade" not in renda.lower() and "PIB" not in renda   # já estão na lista do painel
    assert "acima do teto da meta" in renda                               # inflação fora da banda é dita
    assert "melhoram" in c["titulo"] and "deterioração" in c["titulo"]
