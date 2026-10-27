"""Histórico da dívida pública brasileira (longo prazo) e os marcos que explicam as altas e quedas.

As séries são as oficiais, sem ajuste nosso. O texto dos marcos é editorial e vem sempre acompanhado das medidas
feitas na própria série (primeiro e último mês do período, máximo e mínimo), para o leitor conferir o que o texto afirma."""
from __future__ import annotations

import logging

import macro

log = logging.getLogger("impacto.historico_divida")

INICIO = 2001   # a dívida líquida (SGS 4513) começa em dez/2001; a bruta (SGS 13762), em dez/2006

MARCOS = [
    {"id": "reservas", "inicio": "2003-01", "fim": "2013-12", "tipo": "queda",
     "rotulo": "Dívida líquida cai e o país acumula reservas",
     "o_que_houve": "A economia cresceu, em boa parte puxada pelos preços altos das commodities que o Brasil exporta (soja, minério, petróleo). "
                    "A arrecadação subiu e o governo fez superávits primários. Ao mesmo tempo, o Banco Central comprou dólares e formou um "
                    "colchão de reservas internacionais. A dívida líquida desconta esses ativos, por isso caiu tanto.",
     "ressalva": "A dívida bruta quase não caiu no mesmo período: comprar dólares exige emitir dívida, e a bruta não desconta as reservas. "
                 "Por isso as duas medidas contam histórias diferentes."},
    {"id": "recessao", "inicio": "2014-01", "fim": "2016-12", "tipo": "alta",
     "rotulo": "Disparada da dívida bruta",
     "o_que_houve": "O país entrou numa recessão forte, a arrecadação federal caiu de repente e o governo passou a ter déficits primários "
                    "seguidos: gastava mais do que arrecadava, sem contar os juros. O que faltava foi coberto com mais dívida.",
     "ressalva": "Os juros da dívida também pesaram, e um PIB menor encarece a proporção, porque o PIB é o denominador da fração."},
    {"id": "pandemia", "inicio": "2020-01", "fim": "2020-12", "tipo": "pico",
     "rotulo": "Pandemia de covid-19: o pico da série",
     "o_que_houve": "O governo ampliou as despesas para enfrentar a pandemia (auxílio emergencial, compra de vacinas, repasses a hospitais, "
                    "estados e municípios) enquanto a arrecadação caía e o PIB encolhia. A dívida bruta chegou ao ponto mais alto da série.",
     "ressalva": "Parte do salto veio do encolhimento do PIB, que reduz o denominador da fração, e não só dos gastos extraordinários."},
    {"id": "reducao", "inicio": "2021-01", "fim": "2022-12", "tipo": "queda",
     "rotulo": "Redução transitória",
     "o_que_houve": "A atividade econômica voltou, a inflação alta fez o PIB nominal crescer rápido (o denominador da fração), a arrecadação "
                    "surpreendeu para cima e as despesas da pandemia acabaram. Dividendos e receitas extraordinárias também ajudaram.",
     "ressalva": "Parte da queda veio do efeito da inflação sobre o PIB, que não se repete quando a inflação desacelera: não foi só economia do governo."},
    {"id": "arcabouco", "inicio": "2023-01", "fim": None, "tipo": "alta",
     "rotulo": "Nova alta, já sob o novo arcabouço fiscal",
     "o_que_houve": "O novo arcabouço fiscal (Lei Complementar 200, de 2023) busca o equilíbrio do resultado primário. Mas a Selic em patamar "
                    "elevado encarece a rolagem do estoque: os juros que se somam à dívida superam o que o resultado primário consegue abater.",
     "ressalva": "Não há estabilização nos dados oficiais deste período: os números mostram o que o Banco Central de fato registra."},
]
NOMES = {"dbgg": "Dívida bruta do governo geral", "dlsp": "Dívida líquida do setor público"}


def marcos(series: dict[str, list[dict]], ultimo_mes: str) -> list[dict]:
    """Cada marco ganha as medidas da série oficial dentro do período: de onde saiu, aonde chegou, máximo e mínimo."""
    saida = []
    for m in MARCOS:
        fim = m["fim"] or ultimo_mes
        medidas = []
        for chave in ("dbgg", "dlsp"):
            dentro = [p for p in series.get(chave, []) if m["inicio"] <= p["mes"] <= fim]
            if len(dentro) < 2:
                continue
            a, b = dentro[0], dentro[-1]
            medidas.append({"serie": chave, "nome": NOMES[chave], "de": a, "ate": b, "variacao_pp": round(b["valor"] - a["valor"], 2),
                            "maximo": max(dentro, key=lambda p: p["valor"]), "minimo": min(dentro, key=lambda p: p["valor"])})
        saida.append({**m, "fim": fim, "medidas": medidas})
    return saida


def historico() -> dict:
    def produzir() -> dict:
        def mensal(codigo: int) -> list[dict]:
            return [{"mes": p["data"][:7], "valor": p["valor"]} for p in macro._bcb(codigo, INICIO)]
        series = {"dbgg": mensal(13762), "dlsp": mensal(4513)}
        erros: list[str] = []
        try:   # a DPF em reais é um complemento: se o Tesouro falhar, o gráfico principal continua
            total = macro._estoque_dpf()["total_por_mes"]
            series["dpf"] = [{"mes": m, "valor": round(v / 1e9, 2)} for m, v in sorted(total.items())]
        except Exception as exc:  # noqa: BLE001
            log.warning("DPF fora do histórico da dívida: %s", exc)
            erros.append("O estoque da Dívida Pública Federal em reais não respondeu agora.")
        bruta = series["dbgg"]
        atual, maior, menor = bruta[-1], max(bruta, key=lambda p: p["valor"]), min(bruta, key=lambda p: p["valor"])
        rot, fmt = macro._rotulo_mes, macro._fmt
        indicadores = [
            {"id": "dbgg", "nome": "Dívida bruta do governo geral (DBGG)", "unidade": "% do PIB", "codigo_sgs": 13762, "inicio": bruta[0]["mes"],
             "o_que_mede": "Tudo o que União, estados e municípios devem, sem descontar o que têm a receber. É a medida mais usada em comparações entre países."},
            {"id": "dlsp", "nome": "Dívida líquida do setor público (DLSP)", "unidade": "% do PIB", "codigo_sgs": 4513, "inicio": series["dlsp"][0]["mes"],
             "o_que_mede": "A dívida do setor público depois de descontar ativos, como as reservas internacionais."}]
        if "dpf" in series:
            indicadores.append({"id": "dpf", "nome": "Dívida Pública Federal (DPF)", "unidade": "R$ bilhões", "codigo_sgs": None, "inicio": series["dpf"][0]["mes"],
                                "o_que_mede": "Estoque em reais dos títulos emitidos pelo Tesouro Nacional, em valores correntes (sem corrigir pela inflação)."})
        return {
            "consultado_em": macro._agora(), "erros": erros, "series": series, "ultimo_mes": atual["mes"],
            "marcos": marcos(series, atual["mes"]), "indicadores": indicadores,
            "resumo": (f"A dívida bruta do governo geral está em {fmt(atual['valor'])}% do PIB ({rot(atual['mes'])}). Na série, desde "
                       f"{rot(bruta[0]['mes'])}, o ponto mais alto foi {fmt(maior['valor'])}% ({rot(maior['mes'])}) e o mais baixo, "
                       f"{fmt(menor['valor'])}% ({rot(menor['mes'])})."),
            "notas": ["A DBGG segue a metodologia atual do Banco Central, disponível desde dez/2006; a DLSP, desde dez/2001.",
                      "As séries em % do PIB usam o PIB acumulado em 12 meses: a inflação alta pode reduzir a proporção sem que a dívida em reais caia.",
                      "A DPF em reais está em valores correntes: cresce também pela inflação e pelos juros, e não é comparável com as séries em % do PIB."],
            "fonte": {"nome": "Banco Central do Brasil — SGS (séries 13762 e 4513) e Tesouro Nacional — Estoque da Dívida Pública Federal",
                      "url": "https://dadosabertos.bcb.gov.br/"},
        }
    return macro._memo(("historico_divida",), produzir)
