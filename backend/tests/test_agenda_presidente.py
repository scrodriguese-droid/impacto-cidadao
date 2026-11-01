"""Agenda do Presidente: leitura do HTML do Planalto e classificação dos compromissos (sem rede)."""
import agenda_presidente as ap

HTML = """
<ul class="list-compromissos">
<li class="item-compromisso-wrapper"><div class="item-compromisso">
  <div class="compromisso-horarios"><div class="horario"><time class="compromisso-inicio">09h30</time></div></div>
  <div class="compromisso-dados"><h2 class="compromisso-titulo toggle closed">Presidente da Câmara, Hugo Motta</h2>
    <div class="compromisso-participantes"><label>Participante(s)</label><ul>
      <li>Chefe do Cerimonial da Presidência, Fulano, </li><li>Ministra da Casa Civil</li></ul></div>
    <div class="compromisso-footer"><div class="compromisso-local">Palácio do Planalto</div></div></div></div></li>
<li class="item-compromisso-wrapper"><div class="item-compromisso">
  <div class="compromisso-horarios"><div class="horario"><time class="compromisso-inicio">15h00</time><time class="compromisso-fim">16h00</time></div></div>
  <div class="compromisso-dados"><h2 class="compromisso-titulo">Cerimônia de sanção de lei</h2>
    <div class="compromisso-footer"><div class="compromisso-local">Belo Horizonte/MG</div></div></div></div></li>
</ul>
"""


def test_parse_dia_le_horario_titulo_participantes_e_local():
    a, b = ap.parse_dia(HTML)
    assert (a["inicio"], a["fim"], a["titulo"], a["local"]) == ("09h30", None, "Presidente da Câmara, Hugo Motta", "Palácio do Planalto")
    assert a["participantes"] == ["Chefe do Cerimonial da Presidência, Fulano", "Ministra da Casa Civil"]
    assert (b["inicio"], b["fim"], b["participantes"]) == ("15h00", "16h00", [])


def test_pagina_sem_compromissos():
    assert ap.parse_dia("<html><ul class='list-compromissos'></ul></html>") == []


def test_classificacao_usa_palavra_inteira_e_local():
    a, b = ap.parse_dia(HTML)
    assert set(ap.classificar(a)) == {"congresso", "ministros"}   # "Cerimonial" não vira cerimônia
    assert set(ap.classificar(b)) == {"cerimonias", "fora"}


def test_nome_de_lider_casa_no_titulo():
    c = {"titulo": "Paulo Pimenta e Randolfe Rodrigues", "participantes": [], "local": "Palácio da Alvorada"}
    assert ap.classificar(c, ("Paulo Pimenta",)) == ["congresso"]
    assert ap.classificar(c) == []


def test_separar_cargo_e_pessoa():
    assert ap.separar("Ministra da Casa Civil, Miriam Belchior") == ("Ministra da Casa Civil", "Miriam Belchior")
    # várias pessoas no mesmo título: não tenta adivinhar
    assert ap.separar("Ministro A, Fulano, e Secretário B, Beltrano") == ("Ministro A, Fulano, e Secretário B, Beltrano", None)
    assert ap.separar("Reunião de trabalho") == ("Reunião de trabalho", None)


def test_area_vem_do_nome_do_ministerio():
    assert ap.area("Ministro das Relações Exteriores, Mauro Vieira") == "Relações Exteriores"
    assert ap.area("Ministro da Secretaria de Comunicação Social, Sidônio Palmeira") == "Comunicação Social"
    assert ap.area("Ministra da Casa Civil, Miriam Belchior") == "Casa Civil"
    assert ap.area("Chefe do Gabinete Pessoal do Presidente da República, Oswaldo Malatesta") == "Gabinete Pessoal"
    assert ap.area("Audiência com representantes do setor") is None
