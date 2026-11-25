"""Municípios com menos de 50 mil habitantes publicam o RREO Simplificado e o RGF Simplificado (semestral).
Sem procurar esses tipos, a tela dizia "ainda não publicou relatórios" para quem publica. Estes testes fixam a busca."""
import pytest

import economia
from fontes import ErroFonte

PEQUENO = 3100104   # município de ~6 mil habitantes (código IBGE de 7 dígitos)
GRANDE = 3550308
ESTADO = 31


class _FalsaApi:
    """Responde ao endpoint /rreo conforme o `co_tipo_demonstrativo` pedido."""
    def __init__(self, publica):
        self.publica = publica   # conjunto de (endpoint, tipo)
        self.chamadas = []

    def json(self, url, params):
        endpoint = url.rsplit("/", 1)[-1]
        self.chamadas.append((endpoint, params.get("co_tipo_demonstrativo")))
        achou = (endpoint, params.get("co_tipo_demonstrativo")) in self.publica
        return {"items": [{"cod_conta": "TotalReceitas"}] if achou else []}


@pytest.fixture(autouse=True)
def _limpa():
    economia._TIPO_RREO.clear()
    economia._cache.clear()
    yield
    economia._TIPO_RREO.clear()
    economia._cache.clear()


def test_municipio_pequeno_cai_no_rreo_simplificado(monkeypatch):
    api = _FalsaApi({("rreo", "RREO Simplificado")})
    monkeypatch.setattr(economia, "_http", api)
    assert economia._periodo_mais_recente(PEQUENO) is not None
    assert economia._TIPO_RREO[PEQUENO] == "RREO Simplificado"


def test_municipio_com_relatorio_completo_nao_consulta_o_simplificado(monkeypatch):
    api = _FalsaApi({("rreo", "RREO")})
    monkeypatch.setattr(economia, "_http", api)
    assert economia._periodo_mais_recente(GRANDE) is not None
    assert economia._TIPO_RREO[GRANDE] == "RREO"
    assert all(tipo == "RREO" for _, tipo in api.chamadas)


def test_uniao_e_estado_so_procuram_o_relatorio_completo(monkeypatch):
    api = _FalsaApi(set())   # nada publicado
    monkeypatch.setattr(economia, "_http", api)
    assert economia._periodo_mais_recente(1) is None
    assert economia._periodo_mais_recente(ESTADO) is None
    assert {tipo for _, tipo in api.chamadas} == {"RREO"}


def test_ente_que_nao_publica_nada_continua_sem_dados(monkeypatch):
    monkeypatch.setattr(economia, "_http", _FalsaApi(set()))
    assert economia._periodo_mais_recente(PEQUENO) is None
    assert PEQUENO not in economia._TIPO_RREO


def test_demais_consultas_usam_o_tipo_descoberto(monkeypatch):
    pedidos = []
    monkeypatch.setattr(economia, "_itens", lambda endpoint, **p: pedidos.append(p) or [])
    economia._TIPO_RREO[PEQUENO] = "RREO Simplificado"
    economia._rreo(PEQUENO, 2026, 3, "RREO-Anexo 02")
    economia._rreo(GRANDE, 2026, 3, "RREO-Anexo 02")
    assert [p["co_tipo_demonstrativo"] for p in pedidos] == ["RREO Simplificado", "RREO"]


def _rgf_linhas(uso=51.49):
    return [{"cod_conta": "DespesaComPessoalTotal", "coluna": "% sobre a RCL Ajustada", "valor": uso},
            {"cod_conta": "LimiteMaximoDespesaComPessoalTotal", "coluna": "% sobre a RCL Ajustada", "valor": 54},
            {"cod_conta": "LimitePrudencialDespesaComPessoalTotal", "coluna": "% sobre a RCL Ajustada", "valor": 51.3},
            {"cod_conta": "LimiteDeAlertaDespesaComPessoalTotal", "coluna": "% sobre a RCL Ajustada", "valor": 48.6},
            {"cod_conta": "ReceitaCorrenteLiquidaLimiteLegal", "coluna": "Valor", "valor": 48508474.42}]


def test_pessoal_le_o_rgf_simplificado_semestral(monkeypatch):
    def itens(endpoint, **p):
        if (p["co_tipo_demonstrativo"], p["in_periodicidade"], p["nr_periodo"]) == ("RGF Simplificado", "S", 1) and p["an_exercicio"] == 2026:
            return _rgf_linhas()
        return []
    monkeypatch.setattr(economia, "_itens", itens)
    economia._TIPO_RREO[PEQUENO] = "RREO Simplificado"
    r = economia._pessoal(PEQUENO, 2026)
    assert r["pct_rcl"] == 51.49 and r["referencia"] == "1º semestre de 2026" and r["simplificado"] is True
    assert r["limite_maximo"] == 54 and r["rcl"] == pytest.approx(48508474.42)


def test_pessoal_continua_preferindo_o_quadrimestre_completo(monkeypatch):
    pedidos = []

    def itens(endpoint, **p):
        pedidos.append((p["co_tipo_demonstrativo"], p["in_periodicidade"], p["nr_periodo"]))
        return [{**l, "coluna": l["coluna"].replace(" Ajustada", "")} for l in _rgf_linhas(26.08)] if p["nr_periodo"] == 2 and p["in_periodicidade"] == "Q" else []
    monkeypatch.setattr(economia, "_itens", itens)
    r = economia._pessoal(GRANDE, 2026)
    assert r["referencia"] == "2º quadrimestre de 2026" and r["simplificado"] is False
    assert pedidos[0] == ("RGF", "Q", 3)   # o completo vem primeiro para quem não é simplificado


def test_pessoal_sem_nenhum_rgf_devolve_vazio(monkeypatch):
    def itens(endpoint, **p):
        raise ErroFonte("fora do ar")
    monkeypatch.setattr(economia, "_itens", itens)
    assert economia._pessoal(PEQUENO, 2026) is None


def _painel_com_populacao(monkeypatch, ente):
    linha = {"cod_conta": "TotalReceitas", "coluna": "Até o Bimestre", "valor": 100.0, "populacao": 8569324, "instituicao": "Ente de teste"}
    monkeypatch.setattr(economia, "_periodo_mais_recente", lambda e: (2026, 4))
    monkeypatch.setattr(economia, "_rreo", lambda *a, **k: [linha])
    monkeypatch.setattr(economia, "_pessoal", lambda *a, **k: None)
    monkeypatch.setattr(economia, "_situacao_fiscal", lambda *a, **k: None)
    monkeypatch.setattr(economia, "_fluxo_federal", lambda *a, **k: None)
    monkeypatch.setattr(economia, "_estoque_federal", lambda *a, **k: None)
    monkeypatch.setattr(economia, "_ipca_acumulado", lambda ano, mes: None)
    return economia.painel_economia(ente)


def test_uniao_nao_mostra_populacao_porque_o_siconfi_nao_a_traz(monkeypatch):
    # para o ente 1 a API devolve 8.569.324, que não é a população do Brasil; estados e municípios têm população de verdade
    assert _painel_com_populacao(monkeypatch, 1)["populacao"] is None
    assert _painel_com_populacao(monkeypatch, GRANDE)["populacao"] == 8569324
    assert _painel_com_populacao(monkeypatch, ESTADO)["populacao"] == 8569324


def test_periodos_dos_detalhes_leem_bem_na_frase_valores(monkeypatch):
    p = _painel_com_populacao(monkeypatch, GRANDE)
    periodos = {g["id"]: g["detalhe_periodo"] for g in p["nivel2"]["receitas"]["grupos"]}
    assert periodos["proprios"] == "dos últimos 12 meses" and periodos["outras"] == "acumulados no exercício"
