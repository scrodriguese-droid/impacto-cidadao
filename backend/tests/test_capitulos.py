"""Capítulos do plano: leitura do sumário e localização dos títulos (sem banco, sem IA)."""
import capitulos


def _pg(n, texto, indice=False):
    return {"pagina": n, "texto": texto, "indice": indice}


def test_sumario_com_pontilhado_e_titulo_quebrado():
    paginas = [
        _pg(1, "Capa"),
        _pg(2, "SUMÁRIO\nSEGURANÇA PÚBLICA ........ 3\nDIRETRIZES PARA O\nPROGRAMA DE SAÚDE ........ 4\n"
               "EDUCAÇÃO ........ 5\nCULTURA ........ 6\nESPORTE ........ 7\n", indice=True),
        _pg(3, "SEGURANÇA PÚBLICA\nTexto sobre policiamento."),
        _pg(4, "DIRETRIZES PARA O PROGRAMA DE SAÚDE\nTexto sobre hospitais."),
        _pg(5, "EDUCAÇÃO\nTexto sobre escolas."),
    ]
    idx = capitulos._paginas_do_sumario(paginas)
    assert idx == [1]
    titulos = capitulos._entradas(paginas, idx)
    assert titulos[:3] == ["SEGURANÇA PÚBLICA", "DIRETRIZES PARA O PROGRAMA DE SAÚDE", "EDUCAÇÃO"]
    achados = capitulos._localizar(paginas, titulos[:3], 2)
    assert [i for _, i in achados] == [2, 3, 4]


def test_sumario_sem_numeros_uma_linha_por_entrada():
    paginas = [_pg(1, "Sumário\nSaúde\nEducação\nTransporte\n"), _pg(2, "SAÚDE\nx"), _pg(3, "Educação\ny")]
    assert capitulos._entradas(paginas, [0]) == ["Saúde", "Educação", "Transporte"]


def test_abertura_literal_corta_sem_quebrar_palavra():
    texto = "EDUCAÇÃO\n" + "palavra " * 100
    ab = capitulos._abertura(texto, "EDUCAÇÃO")
    assert ab.endswith("…") and not ab.startswith("EDUCAÇÃO") and len(ab) <= capitulos.TAM_ABERTURA + 1
