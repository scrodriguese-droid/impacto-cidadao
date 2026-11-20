"""Pontos principais de PPA e LDO, extraídos do texto do projeto (sem rede)."""
import pontos_lei as pl

PPA = ["Art. 3º São prioridades da administração pública federal, incluídas aquelas: I - combate à fome; II - educação básica; "
       "e III - saúde: atenção primária. Parágrafo único. Além disso... Art. 4º São agendas transversais do PPA: I - mulheres; e II - meio ambiente. "
       "Parágrafo único. Texto. Art. 6º Integram o PPA 2024-2027: I - Anexo I - Dimensão estratégica, com metas; II - Anexo II - Sumário executivo; "
       "III - Anexo III - Programas finalísticos; IV - Anexo IV - Programas de gestão; V - Anexo V - Agendas; "
       "VI - Anexo VI - Prioridades; VII - Anexo VII - Investimentos; e VIII - Anexo VIII - Investimentos das estatais. § 1º Texto."]

LDO = ["Art. 2º A elaboração será compatível com a meta de superávit primário de R$ 73.216.092.959,00 (setenta e três bilhões) para o Governo Central, "
       "conforme o Anexo. § 1º admite-se intervalo: I - limite superior equivalente a superávit primário de R$ 109.824.139.439,00 (cento e nove); "
       "e II - limite inferior equivalente a superávit primário de R$ 36.608.046.480,00 (trinta e seis). § 2º Texto. "
       "§ 3º será computado na meta o montante equivalente a 39,4% (trinta e nove inteiros) das despesas da União com precatórios."]


def test_ppa_prioridades_agendas_e_anexos():
    r = pl.extrair_ppa(PPA)
    por_rotulo = {i["rotulo"]: i["lista"] for i in r["itens"]}
    assert por_rotulo["Prioridades do governo federal"] == ["Combate à fome", "Educação básica", "Saúde: atenção primária"]
    assert por_rotulo["Agendas transversais"] == ["Mulheres", "Meio ambiente"]
    anexos = next(v for k, v in por_rotulo.items() if k.startswith("O que compõe"))
    assert len(anexos) == 8 and anexos[0] == "Dimensão estratégica, com metas" and anexos[-1] == "Investimentos das estatais"


def test_ldo_meta_faixa_e_precatorios():
    itens = {i["rotulo"]: i for i in pl.extrair_ldo(LDO)["itens"]}
    assert itens["Meta de resultado primário"]["valor"] == "R$ 73,2 bilhões"
    assert itens["Faixa de tolerância na execução"]["valor"] == "R$ 36,6 bilhões a R$ 109,8 bilhões"
    assert itens["Precatórios dentro da meta"]["valor"] == "39,4% do valor"


def test_texto_sem_os_trechos_nao_inventa_nada():
    assert pl.extrair_ppa(["Nada de relevante aqui."]) is None
    assert pl.extrair_ldo(["Nada de relevante aqui."]) is None


def test_tipo_desconhecido_nao_tem_pontos():
    assert pl.pontos("LOA") is None
