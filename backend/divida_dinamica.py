"""A dinâmica da dívida: o filme (o que fez a dívida mudar) contra a fotografia (quanto ela vale hoje).

Parte da identidade contábil da dívida líquida do setor público (DLSP):

    DLSP(t) = DLSP(t-1) + resultado primário + juros + ajustes

Todos os números vêm do SGS do Banco Central, sem ajuste nosso:
* 4478: saldo da DLSP, em R$ milhões (a fotografia);
* 4649: resultado primário mensal, em R$ milhões (convenção do BCB: positivo = déficit, que aumenta a dívida);
* 4616: juros nominais mensais, em R$ milhões (positivo = juros apropriados, que aumentam a dívida).

O que o projeto calcula, e a fonte não publica nesta forma: o terceiro motor, "câmbio e ajustes", é a DIFERENÇA entre a
variação do saldo e (primário + juros). Ali entram a variação do dólar sobre as reservas e a dívida externa, privatizações,
reconhecimento de dívidas e ajustes metodológicos. Por construção, os três motores sempre somam a variação do ano.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import macro

log = logging.getLogger("impacto.divida_dinamica")

SGS_SALDO, SGS_PRIMARIO, SGS_JUROS = 4478, 4649, 4616
PRIMEIRO_ANO = 2003   # a série de saldo começa em dez/2001; 2002 tem fluxos, mas deixamos um ano de folga
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _bi(milhoes: float) -> float:
    return round(milhoes / 1000, 1)


def _reais(v: float) -> str:
    """R$ 1.234,5 bi (sem sinal), no formato brasileiro."""
    return f"R$ {abs(v):,.1f} bi".replace(",", "X").replace(".", ",").replace("X", ".")


def diagnostico(ano: int, v: float, f: float, j: float, a: float, ate_mes: int | None = None) -> dict[str, Any]:
    """Texto do "diagnóstico direto", gerado só a partir dos três motores (em R$ bilhões)."""
    periodo = f"em {ano}" if ate_mes is None else f"de janeiro a {MESES[ate_mes - 1]} de {ano}"
    motores = {"fiscal": f, "juros": j, "cambio": a}
    principal = max(motores, key=lambda k: abs(motores[k]))
    sobe = v >= 0
    abre = f"A dívida {'subiu' if sobe else 'caiu'} {_reais(v)} {periodo}"
    if principal == "juros" and j > 0:
        if f < -0.05:
            causa = (f"não porque o governo gastou demais no dia a dia (ele até economizou {_reais(f)}), "
                     f"mas porque o peso dos juros (+{_reais(j)}) foi o maior fator")
        elif f > 0.05:
            causa = f"principalmente pelo peso dos juros (+{_reais(j)}), somado ao déficit primário de {_reais(f)}"
        else:
            causa = f"quase só pelo peso dos juros (+{_reais(j)}): as contas do dia a dia ficaram no zero a zero"
    elif principal == "fiscal" and f > 0:
        causa = f"principalmente porque o governo gastou {_reais(f)} a mais do que arrecadou no dia a dia (déficit primário)"
    elif principal == "fiscal":
        causa = f"principalmente porque o governo arrecadou {_reais(f)} a mais do que gastou no dia a dia (superávit primário)"
    else:
        causa = (f"principalmente por câmbio e ajustes patrimoniais ({'+' if a > 0 else '−'}{_reais(a)}): "
                 "a variação do dólar e eventos pontuais, não as contas do dia a dia")
    extra = ""
    if principal == "juros":
        sem_juros = f + a
        extra = (f" Sem os juros, a dívida teria {'caído' if sem_juros < 0 else 'subido'} {_reais(sem_juros)}."
                 if abs(sem_juros) >= 0.05 else " Sem os juros, a dívida teria ficado praticamente parada.")
    return {"principal": principal, "texto": f"{abre}, {causa}.{extra}"}


def _por_ano(saldo: dict[str, float], prim: dict[str, float], jur: dict[str, float]) -> list[dict[str, Any]]:
    meses_com_saldo = sorted(saldo)
    ultimo_mes = meses_com_saldo[-1]
    saida = []
    for ano in range(PRIMEIRO_ANO, int(ultimo_mes[:4]) + 1):
        inicio_chave = f"{ano - 1}-12"
        fim_chave = f"{ano}-12" if f"{ano}-12" in saldo else ultimo_mes
        if inicio_chave not in saldo or not fim_chave.startswith(str(ano)):
            continue
        ate = int(fim_chave[5:])
        chaves = [f"{ano}-{m:02d}" for m in range(1, ate + 1)]
        if any(c not in prim or c not in jur for c in chaves):
            continue   # fluxo incompleto: melhor omitir o ano do que somar parte dele
        f, j = sum(prim[c] for c in chaves), sum(jur[c] for c in chaves)
        variacao = saldo[fim_chave] - saldo[inicio_chave]
        a = variacao - f - j
        v_bi, f_bi, j_bi = _bi(variacao), _bi(f), _bi(j)
        a_bi = round(v_bi - f_bi - j_bi, 1)   # garante que a soma dos arredondados feche exatamente
        completo = ate == 12
        d = diagnostico(ano, v_bi, f_bi, j_bi, a_bi, None if completo else ate)
        saida.append({
            "ano": ano, "completo": completo, "ate_mes": ate, "ate_rotulo": f"{MESES[ate - 1]} de {ano}",
            "saldo_inicio_bi": _bi(saldo[inicio_chave]), "saldo_fim_bi": _bi(saldo[fim_chave]),
            "variacao_bi": v_bi, "fiscal_bi": f_bi, "juros_bi": j_bi, "cambio_bi": a_bi,
            "principal": d["principal"], "diagnostico": d["texto"],
        })
    return saida


def dinamica() -> dict[str, Any]:
    def produzir() -> dict[str, Any]:
        def mensal(codigo: int) -> dict[str, float]:
            return {p["data"][:7]: p["valor"] for p in macro._bcb(codigo, PRIMEIRO_ANO - 1)}
        saldo, prim, jur = mensal(SGS_SALDO), mensal(SGS_PRIMARIO), mensal(SGS_JUROS)
        anos = _por_ano(saldo, prim, jur)
        if not anos:
            raise macro.ErroFonte("séries da dívida líquida sem dados")
        # fotografia: saldo de dezembro de cada ano (e o último mês disponível), em R$ bilhões e % do PIB
        pib = {p["data"][:7]: p["valor"] for p in macro._bcb(4513, PRIMEIRO_ANO - 1)}
        foto = []
        for ano in range(PRIMEIRO_ANO - 1, int(max(saldo)[:4]) + 1):
            chave = f"{ano}-12" if f"{ano}-12" in saldo else max(saldo)
            if chave.startswith(str(ano)):
                foto.append({"ano": ano, "mes": chave, "saldo_bi": _bi(saldo[chave]), "pib_pct": pib.get(chave)})
        return {
            "consultado_em": macro._agora(), "anos": anos, "fotografia": foto,
            "fonte": {"nome": "Banco Central do Brasil — SGS (séries 4478, 4649, 4616 e 4513)", "url": macro.PAGINA_SGS,
                      "periodo": f"{PRIMEIRO_ANO} a {max(saldo)}"},
            "metodo": {
                "identidade": "DLSP(t) = DLSP(t−1) + resultado primário + juros + câmbio e ajustes. Os dois primeiros vêm direto do Banco Central.",
                "cambio": "Câmbio e ajustes é calculado pelo projeto: a variação do saldo menos (primário + juros). Inclui o efeito do dólar sobre "
                          "reservas e dívida externa, privatizações e reconhecimento de dívidas. Por isso os três motores sempre somam a variação.",
                "sinais": "Positivo aumenta a dívida; negativo reduz. No primário, o Banco Central publica déficit como positivo.",
            },
        }
    return macro._memo(("divida_dinamica", date.today().isoformat()), produzir)
