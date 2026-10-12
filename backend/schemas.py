"""Vocabulários controlados e modelos de validação.

Tudo que sai da IA passa por estes modelos antes de tocar o banco. Se a resposta
violar o contrato (palavras demais, valor fora do vocabulário, campo faltando),
o cliente de LLM pede uma correção ou marca a análise como falha — nunca grava
dado fora do padrão.

ATENÇÃO: os valores de PerfilId e CategoriaId precisam bater com as tabelas
`perfis` e `categorias` de database/schema.sql.
"""
from __future__ import annotations

import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- #
# Vocabulários
# --------------------------------------------------------------------------- #
PerfilId = Literal[
    "clt", "mei", "estudante", "aposentado", "servidor",
    "produtor_rural", "desempregado", "beneficiario_social",
]
CategoriaId = Literal[
    "impostos", "economia", "emprego", "previdencia", "assistencia", "saude",
    "educacao", "seguranca", "habitacao", "transporte", "meio_ambiente", "agro", "outros",
]
StatusTramitacao = Literal[
    "Proposta da Presidência",
    "Em análise no Congresso",
    "Em vigor (MP aguardando votação)",
    "Aprovada pelo Congresso",
    "Sancionada (em vigor)",
    "Sancionada com vetos",
    "Perdeu a eficácia",
    "Arquivada ou rejeitada",
]
Relevancia = Literal["alta", "media", "baixa", "nenhuma"]
CanalExecucao = Literal[
    "beneficio_direto",            # pago/entregue direto à pessoa
    "programa_via_municipio",      # executado ou aderido pela prefeitura
    "repasse_a_municipios",        # transferência de recursos aos municípios
    "regulacao_nacional",          # regra geral que vale igual no país
    "investimento_federal_direto", # obra/serviço federal
    "nao_especificado",            # o plano não diz como chegaria às pessoas
]

PERFIS: tuple[str, ...] = get_args(PerfilId)
CATEGORIAS: tuple[str, ...] = get_args(CategoriaId)
STATUS: tuple[str, ...] = get_args(StatusTramitacao)
CANAIS: tuple[str, ...] = get_args(CanalExecucao)

# "todos" = medida que afeta a população em geral; não é um perfil selecionável.
PerfilOuTodos = Literal[
    "clt", "mei", "estudante", "aposentado", "servidor",
    "produtor_rural", "desempregado", "beneficiario_social", "todos",
]


# --------------------------------------------------------------------------- #
# Utilidades de texto
# --------------------------------------------------------------------------- #
def contar_palavras(texto: str) -> int:
    return len(re.findall(r"\S+", texto))


# Fim de frase = pontuação + espaço + letra MAIÚSCULA. Dígito não conta: "art. 5º" e "R$ 1.200,00".
_FIM_DE_FRASE = re.compile(r"(?<=[.!?…])\s+(?=[\"“(]?[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ])")


def contar_frases(texto: str) -> int:
    """Conta frases sem tropeçar em 'R$ 1.200,00' ou 'art. 5º'."""
    texto = texto.strip()
    if not texto:
        return 0
    return len([p for p in _FIM_DE_FRASE.split(texto) if p.strip()])


def _sem_repeticao(itens: list[str]) -> list[str]:
    vistos: dict[str, None] = {}
    for item in itens:
        vistos.setdefault(item, None)
    return list(vistos)


# --------------------------------------------------------------------------- #
# Saída da IA — proposições (leis, MPs, PECs)
# --------------------------------------------------------------------------- #
class AnaliseProposicao(BaseModel):
    """JSON estrito que a IA devolve para cada proposição."""

    model_config = ConfigDict(extra="forbid")

    titulo_simples: str = Field(description="Título em linguagem simples, no máximo 8 palavras.")
    resumo_cidadao: str = Field(description="Resumo para o cidadão, no máximo 3 frases curtas.")
    perfis_impactados: list[PerfilOuTodos] = Field(description="Perfis de cidadão realmente afetados.")
    categorias: list[CategoriaId] = Field(description="Áreas temáticas afetadas.")
    impacto_pratico_bolso_rotina: str = Field(description="O que muda no bolso e na rotina, de forma concreta.")
    ponto_positivo: str = Field(description="Argumento neutro, geralmente apresentado a favor da medida.")
    ponto_atencao: str = Field(description="Ponto neutro de atenção/crítica geralmente apresentado.")
    status_tramitacao: StatusTramitacao
    relevancia_bolso: Relevancia = Field(
        description="alta: altera renda, imposto, benefício ou preço de muita gente; "
        "media: grupo específico ou efeito indireto; baixa: efeito pequeno/restrito; "
        "nenhuma: matéria administrativa ou orçamentária sem efeito direto sobre pessoas."
    )

    @field_validator(
        "titulo_simples", "resumo_cidadao", "impacto_pratico_bolso_rotina",
        "ponto_positivo", "ponto_atencao",
    )
    @classmethod
    def _texto_nao_vazio(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            raise ValueError("campo de texto vazio")
        return v

    @field_validator("titulo_simples")
    @classmethod
    def _titulo_curto(cls, v: str) -> str:
        n = contar_palavras(v)
        if n > 8:
            raise ValueError(f"titulo_simples tem {n} palavras; o máximo é 8")
        return v

    @field_validator("resumo_cidadao")
    @classmethod
    def _resumo_curto(cls, v: str) -> str:
        n = contar_frases(v)
        if n > 3:
            raise ValueError(f"resumo_cidadao tem {n} frases; o máximo é 3")
        return v

    @field_validator("impacto_pratico_bolso_rotina")
    @classmethod
    def _impacto_enxuto(cls, v: str) -> str:
        n = contar_palavras(v)
        if n > 90:
            raise ValueError(f"impacto_pratico_bolso_rotina tem {n} palavras; o máximo é 90")
        return v

    @field_validator("ponto_positivo", "ponto_atencao")
    @classmethod
    def _ponto_enxuto(cls, v: str) -> str:
        n = contar_palavras(v)
        if n > 60:
            raise ValueError(f"o ponto tem {n} palavras; o máximo é 60")
        return v

    @field_validator("perfis_impactados", "categorias")
    @classmethod
    def _lista_util(cls, v: list[str]) -> list[str]:
        v = _sem_repeticao(v)
        if not v:
            raise ValueError("a lista não pode ser vazia")
        return v


# --------------------------------------------------------------------------- #
# Saída da IA — planos de governo
# --------------------------------------------------------------------------- #
class PropostaPlano(BaseModel):
    model_config = ConfigDict(extra="forbid")

    descricao: str = Field(description="O que o plano propõe, em até 30 palavras, sem adjetivos.")
    trecho_literal: str = Field(description="Cópia EXATA do plano (8 a 40 palavras) que sustenta a proposta.")
    tem_meta_numerica: bool
    tem_prazo: bool
    tem_fonte_de_recurso: bool
    canal_de_execucao: CanalExecucao
    perfis_beneficiados: list[PerfilOuTodos]

    @field_validator("descricao", "trecho_literal")
    @classmethod
    def _nao_vazio(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            raise ValueError("campo vazio")
        return v

    @field_validator("descricao")
    @classmethod
    def _descricao_curta(cls, v: str) -> str:
        n = contar_palavras(v)
        if n > 40:
            raise ValueError(f"descricao tem {n} palavras; o máximo é 40")
        return v

    @field_validator("trecho_literal")
    @classmethod
    def _trecho_tamanho(cls, v: str) -> str:
        n = contar_palavras(v)
        if n < 4 or n > 60:
            raise ValueError(f"trecho_literal tem {n} palavras; use de 8 a 40")
        return v


class TemaPlano(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categoria: CategoriaId
    mencionado: bool
    resumo_neutro: str = Field(description="Até 2 frases descrevendo o que o plano diz sobre o tema.")
    propostas: list[PropostaPlano] = Field(description="No máximo 5 propostas; vazio se o tema não é abordado.")

    @field_validator("resumo_neutro")
    @classmethod
    def _resumo(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if contar_frases(v) > 3:
            raise ValueError("resumo_neutro deve ter no máximo 2 frases")
        return v


class AnalisePlano(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumo_geral: str = Field(description="Até 3 frases sobre a estrutura e o foco do documento, sem juízo de valor.")
    temas: list[TemaPlano] = Field(description="Um item para cada categoria, na ordem informada.")

    @field_validator("resumo_geral")
    @classmethod
    def _resumo(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            raise ValueError("resumo_geral vazio")
        if contar_frases(v) > 4:
            raise ValueError("resumo_geral deve ter no máximo 3 frases")
        return v

    @field_validator("temas")
    @classmethod
    def _um_por_categoria(cls, v: list[TemaPlano]) -> list[TemaPlano]:
        vistos = [t.categoria for t in v]
        if len(vistos) != len(set(vistos)):
            raise ValueError("há categorias repetidas em 'temas'")
        faltando = [c for c in CATEGORIAS if c not in vistos]
        if faltando:
            raise ValueError(f"faltam categorias em 'temas': {', '.join(faltando)}")
        for t in v:
            if not t.mencionado and t.propostas:
                raise ValueError(f"tema '{t.categoria}' marcado como não mencionado mas traz propostas")
            if len(t.propostas) > 5:
                raise ValueError(f"tema '{t.categoria}' tem mais de 5 propostas")
        return v


# --------------------------------------------------------------------------- #
# Esquema JSON enviado à IA (saída estruturada)
# --------------------------------------------------------------------------- #
# Restrições de tamanho são checadas pelos validadores acima (com nova tentativa);
# aqui removemos palavras-chave que nem todo provedor aceita em saída estruturada.
_CHAVES_IGNORADAS = {"title", "default", "minLength", "maxLength", "minItems", "maxItems",
                     "uniqueItems", "pattern", "format"}


def schema_para_llm(modelo: type[BaseModel]) -> dict[str, Any]:
    """Gera um JSON Schema 'plano' (sem $ref/$defs) para um modelo Pydantic."""
    bruto = modelo.model_json_schema()
    defs = bruto.pop("$defs", {})

    def resolver(no: Any, dentro_de_properties: bool = False) -> Any:
        if isinstance(no, dict):
            if "$ref" in no:
                return resolver(defs[no["$ref"].rsplit("/", 1)[-1]])
            saida = {}
            for chave, valor in no.items():
                if not dentro_de_properties and chave in _CHAVES_IGNORADAS:
                    continue
                saida[chave] = resolver(valor, dentro_de_properties=(chave == "properties"))
            return saida
        if isinstance(no, list):
            return [resolver(x) for x in no]
        return no

    return resolver(bruto)
