"""Partidos: leitura das tabelas do TSE, grafo de fusões e mapa de afinidade (sem rede)."""
import math

import partidos

FONTE = {
    "registrados": [
        {"sigla": "UNIÃO", "nome": "UNIÃO BRASIL", "deferimento": "2022-02-08"},
        {"sigla": "PSD", "nome": "PARTIDO SOCIAL DEMOCRÁTICO", "deferimento": "2011-09-27"},
        {"sigla": "MDB", "nome": "MOVIMENTO DEMOCRÁTICO BRASILEIRO", "deferimento": "1981-06-30"},
    ],
    "fusoes": [{"de": ["Partido Social Liberal (PSL)", "Democratas (DEM)"], "para": "União Brasil (UNIÃO)", "processo": "p1", "data": "2022-02-08"}],
    "incorporacoes": [{"de": ["Partido Social Democrático (PSD)*"], "para": "Partido Trabalhista Brasileiro (PTB)", "processo": "p2", "data": "2003-02-20"}],
    "mudancas": [
        {"de": ["Partido da Frente Liberal (PFL)"], "para": "Democratas (DEM)", "processo": "p3", "data": "2007-06-12"},
        {"de": ["Partido do Movimento Democrático Brasileiro (PMDB)"], "para": "Movimento Democrático Brasileiro (MDB)", "processo": "p4", "data": "2018-05-15"},
    ],
}


def test_chave_ignora_asteriscos_e_acentos():
    assert partidos._chave("Partido Progressista Brasileiro (PPB) *") == "PPB"
    assert partidos._chave("Patriota (PATRI).") == "PATRI"
    assert partidos._chave("PC do B") == partidos._chave("PCdoB")


def test_partes_divide_so_entre_siglas():
    assert partidos._partes("Partido A (A) e Partido B (B)") == ["Partido A (A)", "Partido B (B)"]


def test_grafo_distingue_psd_extinto_do_atual():
    g = partidos._construir({k: [dict(x) for x in v] for k, v in FONTE.items()})
    nos = {n["id"]: n for n in g["nos"]}
    psds = [n for n in g["nos"] if n["nomes"][-1]["sigla"] == "PSD"]
    assert len(psds) == 2
    assert sum(n["vivo"] for n in psds) == 1
    uniao = nos["uniao"]
    assert uniao["inicio"]["tipo"] == "fusao" and uniao["registro_tse"] == "2022-02-08"
    assert {a["de"] for a in g["arestas"] if a["para"] == "uniao"} == {"psl", "dem"}
    assert [n["sigla"] for n in nos["dem"]["nomes"]] == ["PFL", "DEM"]
    assert nos["mdb"]["inicio"]["data"] == "1981-06-30" and len(nos["mdb"]["nomes"]) == 2
    assert all(c["confere"] for c in g["conferencia"])


def test_mds_preserva_proximidade():
    dist = {("A", "B"): 0.1, ("A", "C"): 0.9, ("B", "C"): 0.8, ("A", "D"): 0.85, ("B", "D"): 0.9, ("C", "D"): 0.15}
    pts = partidos._mds(["A", "B", "C", "D"], dist)
    d = lambda a, b: math.dist(pts[a], pts[b])  # noqa: E731
    assert d("A", "B") < d("A", "C") and d("C", "D") < d("A", "D")


def test_alinhar_remove_rotacao_e_reflexao():
    ref = {"A": (1.0, 0.0), "B": (0.0, 1.0), "C": (-1.0, 0.0), "D": (0.0, -1.0)}
    girado = {s: (-y, x) for s, (x, y) in ref.items()}
    refletido = {s: (x, -y) for s, (x, y) in girado.items()}
    for p in (girado, refletido):
        al = partidos._alinhar(p, ref)
        assert all(abs(al[s][0] - ref[s][0]) < 1e-6 and abs(al[s][1] - ref[s][1]) < 1e-6 for s in ref)
