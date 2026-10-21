"""Régua de maturidade das promessas: critérios objetivos e níveis neutros."""
import maturidade as m


def test_niveis_pelos_tres_criterios_centrais():
    assert m.nivel(True, True, True) == "estruturada"
    assert m.nivel(True, False, False) == "parcial"
    assert m.nivel(False, False, False) == "generica"


def test_texto_com_meta_prazo_e_fonte():
    t = "Vamos construir 6.000 creches até 2030, financiadas pelo Fundeb e por parcerias público-privadas."
    r = m.avaliar_texto(t)
    assert r["nivel"] == "estruturada" and r["criterios"]["meta_numerica"]


def test_slogan_e_carta_de_intencoes():
    assert m.avaliar_texto("Vamos melhorar a educação com carinho e dedicação.")["nivel"] == "generica"


def test_proposta_analisada_usa_campos_verificados():
    p = {"tem_meta_numerica": True, "tem_prazo": True, "tem_fonte_recurso": False, "canal_execucao": "regulacao_nacional"}
    r = m.avaliar_proposta(p)
    assert r["nivel"] == "parcial" and r["criterios"]["quem_executa"]


def test_contagem_por_paginas_do_tema():
    r = m.contar_paginas(["Construir 500 creches até 2030, com recursos do Fundeb.", "Vamos cuidar de todos."])
    assert r == {"paginas": 2, "com_meta_numerica": 1, "com_prazo": 1, "com_fonte_de_recursos": 1, "com_quem_executa": 0}
