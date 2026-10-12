"""Testes unitários das regras que não dependem de rede, banco ou IA.

Executar:  cd backend && python -m pytest -q
"""
import json

import pytest
from pydantic import ValidationError

import simuladores as sim
from coletor import inferir_status, montar_linha_do_tempo
from coletor_candidatos import verificar_citacoes
from fontes import html_para_texto, localizar_trecho, normalizar
from llm import _extrair_json
from schemas import (CATEGORIAS, AnaliseProposicao, AnalisePlano, contar_frases, contar_palavras,
                     schema_para_llm)


# --------------------------------------------------------------------------- #
# Simuladores
# --------------------------------------------------------------------------- #
def test_inss_progressivo_e_teto():
    assert sim.calcular_inss(1621.00) == 121.58
    assert sim.calcular_inss(5000.00) == 501.51
    assert sim.calcular_inss(50_000) == sim.calcular_inss(8475.55)  # teto previdenciário


def test_ir_isento_ate_5000_com_economia_igual_ao_imposto_de_2025():
    r = sim.simular_ir(5000.00)
    assert r["faixa"] == "isencao_total"
    assert r["regra_2026"]["imposto_mensal"] == 0.0
    assert r["regra_2025"]["imposto_mensal"] == pytest.approx(312.89, abs=0.05)
    assert r["economia_mensal"] == r["regra_2025"]["imposto_mensal"]


def test_ir_continuidade_na_fronteira_de_5000():
    # Logo acima de R$ 5 mil o redutor quase zera o imposto: não há "degrau".
    r = sim.simular_ir(5000.01)
    assert r["regra_2026"]["imposto_mensal"] < 1.0


def test_ir_reducao_parcial():
    r = sim.simular_ir(6000.00)
    assert r["faixa"] == "reducao_parcial"
    assert r["regra_2026"]["redutor_aplicado"] == pytest.approx(978.62 - 0.133145 * 6000, abs=0.01)
    assert r["regra_2026"]["imposto_mensal"] == pytest.approx(
        r["regra_2025"]["imposto_mensal"] - r["regra_2026"]["redutor_aplicado"], abs=0.01)


def test_ir_sem_beneficio_acima_de_7350():
    r = sim.simular_ir(9000.00)
    assert r["faixa"] == "sem_beneficio"
    assert r["economia_mensal"] == 0.0


def test_ir_nunca_negativo_e_dependentes_reduzem_imposto():
    assert sim.simular_ir(0)["regra_2026"]["imposto_mensal"] == 0.0
    sem = sim.simular_ir(7000, dependentes=0)["regra_2025"]["imposto_mensal"]
    com = sim.simular_ir(7000, dependentes=3)["regra_2025"]["imposto_mensal"]
    assert com <= sem


def test_mei_limites_e_das():
    assert sim.simular_mei(80_000)["situacao_atual"]["codigo"] == "dentro_do_limite"
    assert sim.simular_mei(90_000)["situacao_atual"]["codigo"] == "excesso_ate_20"
    assert sim.simular_mei(100_000)["situacao_atual"]["codigo"] == "excesso_acima_20"
    assert sim.simular_mei(50_000, "servicos")["das_mensal"] == 86.05
    assert sim.simular_mei(50_000, "comercio_industria")["das_mensal"] == 82.05
    assert sim.simular_mei(50_000, "comercio_servicos")["das_mensal"] == 87.05


def test_mei_limite_proporcional_e_cenario_hipotetico():
    r = sim.simular_mei(30_000, meses_de_atividade=4)
    assert r["limite_vigente"] == 27_000.0
    assert r["situacao_atual"]["codigo"] == "excesso_ate_20"
    h = sim.simular_mei(95_000, teto_hipotetico=130_000)
    assert h["cenario_hipotetico"]["mudou_em_relacao_ao_atual"] is True
    assert h["cenario_hipotetico"]["situacao"]["codigo"] == "dentro_do_limite"


# --------------------------------------------------------------------------- #
# Esquemas e validação da saída da IA
# --------------------------------------------------------------------------- #
def _analise_valida(**mudancas) -> dict:
    base = {
        "titulo_simples": "Imposto de Renda: isenção ampliada",
        "resumo_cidadao": "A medida muda a tabela do Imposto de Renda. Vale a partir de janeiro.",
        "perfis_impactados": ["clt", "aposentado"],
        "categorias": ["impostos"],
        "impacto_pratico_bolso_rotina": "Quem ganha até R$ 5.000 por mês deixa de pagar o imposto na fonte.",
        "ponto_positivo": "Aumenta a renda disponível das famílias de renda média.",
        "ponto_atencao": "Reduz a arrecadação e exige compensação em outras fontes.",
        "status_tramitacao": "Sancionada (em vigor)",
        "relevancia_bolso": "alta",
    }
    base.update(mudancas)
    return base


def test_analise_valida_passa():
    assert AnaliseProposicao.model_validate(_analise_valida()).relevancia_bolso == "alta"


def test_titulo_com_mais_de_8_palavras_falha():
    with pytest.raises(ValidationError, match="8"):
        AnaliseProposicao.model_validate(_analise_valida(titulo_simples="um dois três quatro cinco seis sete oito nove"))


def test_resumo_com_mais_de_3_frases_falha():
    with pytest.raises(ValidationError, match="3"):
        AnaliseProposicao.model_validate(_analise_valida(resumo_cidadao="Um. Dois. Três. Quatro."))


def test_valor_fora_do_vocabulario_falha():
    with pytest.raises(ValidationError):
        AnaliseProposicao.model_validate(_analise_valida(perfis_impactados=["alienigena"]))
    with pytest.raises(ValidationError):
        AnaliseProposicao.model_validate(_analise_valida(status_tramitacao="Aprovada"))


def test_campo_extra_e_proibido():
    with pytest.raises(ValidationError):
        AnaliseProposicao.model_validate(_analise_valida(opiniao="isso é ótimo"))


def test_contadores_nao_quebram_em_numeros_e_abreviacoes():
    assert contar_frases("O benefício é de R$ 1.200,00 por mês. Vale conforme o art. 5º da lei.") == 2
    assert contar_palavras("Imposto de Renda") == 3


def test_schema_para_llm_e_plano_e_estrito():
    for modelo in (AnaliseProposicao, AnalisePlano):
        bruto = json.dumps(schema_para_llm(modelo))
        assert "$ref" not in bruto and "$defs" not in bruto
        assert '"additionalProperties": false' in bruto


# --------------------------------------------------------------------------- #
# Conferência de citações (anti-alucinação)
# --------------------------------------------------------------------------- #
def _plano(propostas_por_tema: dict[str, list[dict]]) -> AnalisePlano:
    temas = []
    for cat in CATEGORIAS:
        props = propostas_por_tema.get(cat, [])
        temas.append({"categoria": cat, "mencionado": bool(props),
                      "resumo_neutro": "O plano trata do tema." if props else
                      "Nenhuma proposta sobre este tema foi identificada no plano.",
                      "propostas": props})
    return AnalisePlano.model_validate({"resumo_geral": "Documento de diretrizes.", "temas": temas})


def _proposta(trecho: str) -> dict:
    return {"descricao": "Amplia o acesso a consultas médicas.", "trecho_literal": trecho,
            "tem_meta_numerica": True, "tem_prazo": False, "tem_fonte_de_recurso": False,
            "canal_de_execucao": "programa_via_municipio", "perfis_beneficiados": ["todos"]}


def test_citacao_real_e_aceita_e_pagina_e_calculada_pelo_codigo():
    paginas = ["Introdução geral do documento sem propostas concretas.",
               "Vamos ampliar em 30% as consultas médi-\ncas especializadas no Sistema Único de Saúde até 2028."]
    analise = _plano({"saude": [_proposta("ampliar em 30% as consultas médicas especializadas no Sistema Único de Saúde")]})
    temas, descartadas = verificar_citacoes(analise, paginas)
    saude = next(t for t in temas if t["categoria"] == "saude")
    assert descartadas == 0
    assert saude["propostas"][0][1] == 2  # página 2


def test_citacao_inventada_e_descartada_e_nao_vira_silencio():
    paginas = ["O plano fala de educação e de creches em tempo integral para todas as crianças."]
    analise = _plano({"saude": [_proposta("construir mil hospitais federais em todos os estados até o fim do mandato")]})
    temas, descartadas = verificar_citacoes(analise, paginas)
    saude = next(t for t in temas if t["categoria"] == "saude")
    assert descartadas == 1
    assert saude["propostas"] == []
    assert saude["mencionado"] is True                      # não afirmamos "silêncio"
    assert "não pôde ser" in saude["resumo"] or "nenhuma citação" in saude["resumo"]


def test_normalizar_ignora_acentos_espacos_e_pontuacao():
    assert normalizar("Saúde,  educação e SEGURANÇA!") == normalizar("saude educacao e seguranca")
    pagina = normalizar("Texto sobre saúde, educação e segurança pública no país.")
    assert localizar_trecho("saude educacao e seguranca publica", [pagina]) == 1
    assert localizar_trecho("saude", [pagina]) is None  # trecho curto demais para ser evidência


# --------------------------------------------------------------------------- #
# Regras de status / linha do tempo / utilitários
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("situacao, esperado", [
    ("Transformado em Norma Jurídica", "Sancionada (em vigor)"),
    ("SEM EFICÁCIA", "Perdeu a eficácia"),
    ("Arquivada", "Arquivada ou rejeitada"),
    ("Aguardando Designação de Relator", None),
])
def test_inferir_status(situacao, esperado):
    assert inferir_status("PL", situacao, []) == esperado


def test_inferir_status_mpv_sem_situacao_e_vigente():
    assert inferir_status("MPV", "", []) == "Em vigor (MP aguardando votação)"


def test_linha_do_tempo_marca_ultimo_como_atual_e_remove_repetidos():
    tram = [
        {"dataHora": "2026-09-25T00:00", "siglaOrgao": "EXEC", "descricaoTramitacao": "Apresentação", "despacho": "Publicada"},
        {"dataHora": "2026-09-25T00:00", "siglaOrgao": "EXEC", "descricaoTramitacao": "Apresentação", "despacho": "Publicada"},
        {"dataHora": "2026-10-01T09:55", "siglaOrgao": "MESA", "descricaoTramitacao": "Comissão mista", "despacho": ""},
    ]
    linha = montar_linha_do_tempo(tram)
    assert len(linha) == 2
    assert [e["status"] for e in linha] == ["completed", "current"]


def test_extrair_json_com_cercas():
    assert json.loads(_extrair_json('```json\n{"a": 1}\n```')) == {"a": 1}
    assert json.loads(_extrair_json('Aqui está: {"a": 2} fim')) == {"a": 2}


def test_html_para_texto_ignora_scripts():
    html = "<html><head><title>x</title></head><body><script>alert(1)</script><p>Art. 1º Fica criado.</p></body></html>"
    assert html_para_texto(html) == "Art. 1º Fica criado."
