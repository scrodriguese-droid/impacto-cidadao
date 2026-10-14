# ImpactoCidadão

Traduz **planos de governo**, **leis** e **medidas provisórias** em impacto prático para o perfil do cidadão
(CLT, MEI, estudante, aposentado, produtor rural…) e para a **sua cidade**. Dados 100% de fontes oficiais:
TSE, Câmara dos Deputados, Senado Federal e IBGE. A IA só traduz; nunca é a fonte.

> **Informação, não recomendação de voto.** O conteúdo é gerado por IA e pode conter erros — o app
> sempre aponta para o documento oficial.

## Estrutura

```
impacto-cidadao/
├── .env.example               # copie para .env e preencha
├── docker-compose.yml         # PostgreSQL local (aplica o schema sozinho)
├── database/
│   └── schema.sql             # usuarios, perfis, proposicoes_analisadas, votos_comunitarios,
│                              # notificacoes + candidatos, planos_governo, temas/propostas,
│                              # paginas_plano (texto por página, busca sem acento)
├── backend/
│   ├── main.py                # API FastAPI (rotas /api/...) + serve o front-end
│   ├── coletor.py             # Câmara/Senado -> filtro -> IA -> banco   (leis e MPs)
│   ├── coletor_candidatos.py  # TSE -> PDF do plano -> IA -> conferência de citações -> banco
│   ├── prompts.py             # prompts de sistema (proposições e planos)  ← o "prompt exato"
│   ├── schemas.py             # vocabulários + validação estrita da saída da IA
│   ├── llm.py                 # Claude (padrão) ou Gemini; nova tentativa com a correção explicada
│   ├── fontes.py              # clientes Câmara, Senado, TSE, IBGE; leitura de PDF/HTML
│   ├── simuladores.py         # IR (Lei 15.270/2025) e MEI — sem IA, parâmetros documentados
│   ├── db.py, config.py
│   └── tests/                 # 26 testes unitários (rodam sem rede, banco ou IA)
├── frontend/
│   ├── index.html             # planos de governo (por tema/cidade) + simuladores
│   ├── politicas.html         # políticas públicas: leis e MPs por poder × nível de governo
│   └── assets/                # comum.js (utilidades, cabeçalho/rodapé), tailwind-config.js
└── dados/
    ├── candidatos_presidente_2026.json   # snapshot de contingência (campos mínimos)
    └── planos/                           # coloque PDFs aqui se o download for bloqueado
```

## Como rodar (Windows / PowerShell)

> **Crie o ambiente virtual fora do OneDrive.** O OneDrive tenta sincronizar milhares de arquivos
> do `venv` e o caminho longo do Windows quebra o `pip install` da biblioteca `anthropic`.

```powershell
cd "C:\Users\elois\OneDrive\Ambiente de Trabalho\impacto-cidadao"

# 1) Python (3.11+) em um venv fora do OneDrive
python -m venv "$env:USERPROFILE\.venvs\impacto"
& "$env:USERPROFILE\.venvs\impacto\Scripts\Activate.ps1"
pip install -r backend\requirements.txt

# 2) Configuração
Copy-Item .env.example .env        # edite: ANTHROPIC_API_KEY (ou GEMINI_API_KEY + LLM_PROVIDER=gemini)

# 3) Banco (Docker Desktop precisa estar aberto)
docker compose up -d               # cria o banco e aplica database/schema.sql

# 4) Dados
python backend\coletor_candidatos.py --so-segundo-turno   # planos de governo (comece pelo 2º turno)
python backend\coletor.py --dias 30 --limite 10           # leis e MPs recentes do Executivo

# 5) App
uvicorn main:app --reload --app-dir backend
# abra http://localhost:8000   (documentação da API: http://localhost:8000/docs)
```

Sem Docker? Crie um projeto no [Supabase](https://supabase.com), cole `database/schema.sql` no SQL Editor e
ponha a *connection string* do pooler em `DATABASE_URL`.

Testes: `cd backend && python -m pytest -q`

### Funciona sem IA

Mesmo sem chave de IA, o app é útil: `coletor_candidatos.py --sem-ia` baixa os planos, grava o **texto
de cada página** e a tela de planos passa a oferecer **busca por palavra** (ou por tema) com o trecho
literal e a página de cada candidato (`GET /api/candidatos/buscar`). A regra é igual para todos, em
ordem alfabética, sem interpretação. A busca ignora acentos (`saude` acha `saúde`).

Já tinha o banco criado antes dessa mudança? O schema é idempotente — reaplique:

```powershell
docker cp database\schema.sql impacto-db:/tmp/schema.sql
docker exec impacto-db psql -U impacto -d impacto -v ON_ERROR_STOP=1 -q -f /tmp/schema.sql
python backend\coletor_candidatos.py --sem-ia --forcar    # regrava o texto das páginas
```

### Políticas públicas: por tema, poder e nível de governo

A página `politicas.html` agrupa os atos por **tema** (saúde, educação, impostos…) e permite filtrar por
**poder** (Executivo, Legislativo) e por **nível** (federal, estadual, municipal). O estado de cada célula
da matriz vem de `COBERTURA` em `backend/main.py` — a tela nunca finge cobertura:

| | Executivo | Legislativo |
|---|---|---|
| **Federal** | ✅ MPs e projetos do Executivo (Câmara/Senado) | ✅ amostra de alto impacto (Câmara) |
| **Estadual** | ⏳ Diários Oficiais estaduais | ⏳ Assembleias Legislativas |
| **Municipal** | ⏳ Diários municipais (Querido Diário) | ⏳ Câmaras Municipais |

(O Judiciário — ADPF/ADI — foi retirado do escopo por ora; o STF não tem API oficial confirmada.)

**Como o tema é definido:** nos atos traduzidos, pelas áreas da IA; nos demais, por palavras-chave da
ementa oficial (`backend/temas.py`, a mesma lista da busca nos planos), marcado na tela como "tema
aproximado". Para recalcular sem coletar: `python backend\coletor.py --so-temas`.

Para conectar uma fonte nova: (1) faça o coletor gravar `instancia`, `nivel` (e `uf`/`municipio_ibge`) em
`proposicoes_analisadas`; (2) troque `fonte_conectada` para `True` na célula em `COBERTURA`.
Atos ainda sem tradução de IA aparecem com a ementa oficial, a tramitação e o link da fonte.

### Janela de tempo, frescor e coleta diária

* **Você escolhe o período na tela.** Na página *Políticas públicas*, o botão de período aceita atalhos
  (7 dias, 30 dias, 6 meses, 1 ano…), "últimos **N** dias" livres ou um intervalo de datas. A API aceita
  `desde=AAAA-MM-DD` e `ate=AAAA-MM-DD` em `/api/proposicoes`.
* **O que há para filtrar depende do que foi coletado.** A coleta usa `--dias N` (padrão 60). Se o período
  pedido for mais antigo que a base, a tela avisa e mostra o comando para ampliar
  (ex.: `python backend\coletor.py --dias 365`).
* **A tela mostra a data dos dados:** "Dados coletados em 14/10/2026 às 18:09 · atos apresentados de … a …"
  (vem da tabela `coletas`, uma linha por execução).
* **A listagem não tem teto:** percorre todas as páginas da janela. Atos já examinados e descartados ficam
  em `proposicoes_descartadas`, então a rodada diária só consulta o que é novo.
* **Atos em tramitação fora da janela continuam sendo atualizados** (uma MP de 61 dias atrás que for votada
  hoje muda de situação na próxima coleta). Atos concluídos (sancionados, arquivados…) deixam de ser reexaminados.
* **Tradução por IA:** `--limite N` (padrão 25) vale só para traduções por rodada; o excedente é coletado
  sem tradução e entra na fila da próxima. `--ia auto` (padrão) só traduz se houver chave no `.env`.

**Coleta diária automática (Windows):**

```powershell
.\scripts\agendar.ps1                         # todo dia às 07:30, janela de 30 dias
.\scripts\agendar.ps1 -Hora 06:00 -Dias 60
.\scripts\agendar.ps1 -Remover
Start-ScheduledTask -TaskName ImpactoCidadao-Coleta     # roda agora, para testar
```

O script `scripts\coletar.ps1` abre o Docker Desktop se preciso, roda os dois coletores e registra tudo em
`logs\coleta-AAAA-MM-DD.log`. O PC precisa estar ligado (se perder o horário, roda quando voltar).

### Modos úteis dos coletores

| Comando | Para quê |
|---|---|
| `coletor.py --dias 365` | carga histórica: examina um ano inteiro |
| `coletor.py --ia nao` | só coleta e grava como "pendente" (zero custo de IA) |
| `coletor.py --ia sim` | exige IA (erro se não houver chave) |
| `coletor.py --id 2647155 --forcar` | reanalisa uma proposição específica da Câmara |
| `coletor.py --max-alto-impacto 0` | só Poder Executivo (desliga propostas do Congresso por "alto impacto") |
| `coletor.py --so-temas` | recalcula o tema aproximado dos atos, sem coletar |
| `coletor_candidatos.py --sq <SQ> --forcar` | reanalisa um candidato |
| `coletor_candidatos.py --ia nao` | cadastra candidatos e lê os PDFs, sem chamar a IA |

Os coletores são **idempotentes**: reexecutar não duplica linhas nem refaz o que não mudou.

## Como garantimos neutralidade e fidelidade

1. **Mesmo roteiro para todos.** Todos os candidatos passam pelo mesmo prompt, esquema, modelo e parâmetros,
   processados e exibidos em ordem alfabética, **sem ranking**.
2. **Citação obrigatória e conferida.** Cada proposta traz um trecho literal. O código procura esse trecho no
   PDF e **descarta** o que não existe (`propostas_descartadas` mostra quantas). A página é calculada pelo código.
3. **Silêncio ≠ ausência.** Se nenhuma citação resistir à conferência, o app não afirma que o plano "não fala"
   do tema — diz que não conseguiu verificar.
4. **Descrever, não avaliar.** O prompt proíbe adjetivos, comparações e inferência de intenções. "Detalhamento"
   (meta numérica, prazo, fonte de recursos) é exibido como fato do texto, nunca como nota.
5. **"Na sua cidade" sem chute.** O impacto local é derivado do *canal de execução* que o próprio plano indica
   (pago direto, via prefeitura, repasse, regra nacional…) combinado com dados do IBGE. Sem valores inventados.
6. **Saída da IA validada.** Título ≤ 8 palavras, resumo ≤ 3 frases, vocabulário fechado; resposta fora do
   contrato é rejeitada e refeita — nunca vai para o banco.
7. **Minimização de dados.** CPF, título de eleitor, bens e data de nascimento do TSE nunca são gravados.

## Decisões que você deve conhecer

* **Sem votação sobre candidatos.** Enquetes públicas sobre candidatos podem ser enquadradas como *pesquisa
  eleitoral*, que exige registro prévio na Justiça Eleitoral (Lei 9.504/1997, art. 33). Por isso a votação
  existe só para leis/MPs. Valide o uso com um advogado eleitoralista antes de divulgar o app em período eleitoral.
* **IA em conteúdo sobre eleições.** O app rotula tudo como gerado por IA e sempre linka o original. Confirme
  com assessoria jurídica se a sua forma de divulgação exige outros avisos (Res. TSE 23.610/2019).
* **PDFs do TSE.** O download usa um *User-Agent* honesto (sem disfarce de navegador). Se o TSE responder 403,
  baixe o plano no navegador e salve como `dados/planos/<SQ_CANDIDATO>.pdf`; o coletor usa o arquivo local.
* **PDFs escaneados** (imagem) ficam como `sem_texto` e aparecem no app como "análise pendente" — falta OCR.
* **Simuladores** usam as regras vigentes (IR: tabela de 2025 vs. regra de 2026; MEI: DAS 2026). Parâmetros
  ficam no topo de `simuladores.py` para atualizar quando a lei mudar.
* **Créditos extraordinários** ("Abre crédito…") são ignorados por padrão (`--incluir-creditos` traz de volta).

## Próximos passos sugeridos

Login (Supabase Auth) + tela de notificações (a tabela e o *trigger* já existem) · OCR para planos escaneados ·
compilar o Tailwind em vez do CDN · cache HTTP/CDN na frente da API · revisão editorial por amostragem das
traduções (a tabela guarda `analise_bruta` e o modelo usado para auditoria).

## Economia e indicadores macro

* **/economia.html** — contas de município, estado e União (SICONFI/Tesouro): saúde fiscal, déficits
  nominal/primário/operacional, receitas e despesas, execução orçamentária. `GET /api/economia/{codigo}`
  (1 = União, 2 dígitos = UF, 7 dígitos = município).
* **/macro.html** — IPCA, Selic, desemprego (PNAD), resultado fiscal e dívida pública, com filtros,
  comparação, tabela, exportação CSV e fonte/data de consulta em cada série.
  `GET /api/macro/{catalogo,resumo,serie/{id},divida}`.

Fontes (abertas, sem chave): Banco Central SGS (433, 13522, 432, 4649, 4616, 4583, 5793, 5727, 5760, 4513, 4478),
IBGE SIDRA (tabela 6381), Tesouro Transparente (estoque da DPF, emissões e resgates, Tesouro Direto) e SICONFI.
Os códigos do SGS foram conferidos nos metadados do BCB e entre si (nominal = primário + juros).

Limitações: a dívida da DPF é bruta e só da União (não é a dívida líquida do setor público); o déficit operacional
é cálculo próprio aproximado; mínimos constitucionais de Saúde/Educação não estão na API aberta do SICONFI; os
arquivos do Tesouro (10–15 MB) são baixados na primeira consulta e ficam em cache por 6 h. Em redes com inspeção
TLS, instale `truststore` (já em `requirements.txt`) para usar o repositório de certificados do sistema.

## Arquitetura do site (7 módulos)

| # | Módulo | Estado | Onde |
|---|--------|--------|------|
| 1 | Filtro de perfil e cidade (IBGE) | Disponível | topo de `index.html`; preferências compartilhadas (`ic_prefs`) |
| 2 | Leis e projetos de lei (Câmara/Senado) | Disponível | `politicas.html` |
| 3 | Radar de realidade (promessas × Constituição e LRF) | Parcial: planos de governo com citação; falta a avaliação de lei/PEC, esfera e fonte na LOA | `index.html#candidatos` |
| 4 | Ciclo orçamentário e calculadora de imposto | Disponível: “para onde vai o meu imposto” (ilustração proporcional) e PPA/LDO/LOA da União (Câmara, PLN); estados e municípios: lacuna declarada (sem base aberta) + dotação inicial do RREO | `orcamento.html`, `economia.html#n3`, `index.html#simuladores` |
| 5 | Emendas parlamentares e execução local | Planejado: só links oficiais (lista própria exige chave gratuita da CGU) | `economia.html#n4` |
| 6 | Economia do setor público e saúde fiscal | Disponível: arrecadação, LRF, déficits, reservas, dívida | `economia.html`, `macro.html` |
| 7 | Composição partidária e bancadas | Disponível: Câmara e Senado (cadeiras por partido, limiares de maioria e 3/5); câmaras municipais: lacuna declarada (sem fonte aberta confiável da composição atual) | `bancadas.html`, `GET /api/bancadas` |

### Módulo 4 — `orcamento.html`

* `GET /api/orcamento/imposto/{codigo}?valor=` — reparte um valor anual de imposto pelas funções de governo,
  proporcional à despesa liquidada (RREO Anexo 02). É ilustração: imposto não é carimbado por área, e cada
  tributo abastece uma esfera (IR → União; ICMS/IPVA → estado; IPTU/ISS → município). Pode usar o resultado do
  simulador de IR (regra 2026 × 12).
* `GET /api/orcamento/ciclo/{codigo}` — União: PPA, LDO e LOA como proposições PLN da Câmara (situação, lei,
  link do texto e da tramitação; cache 6 h). Verificado: o SICONFI (`extrato_entregas`) só tem DCA, MSC, RREO
  e RGF, então estados e municípios aparecem como lacuna; mostramos a dotação inicial (LOA) do RREO.
* Não vem da fonte: os valores da LOA federal por área/ação (SIOP exige o sistema próprio; Portal da
  Transparência exige chave para API) — a página aponta para eles.

### Dívida e títulos públicos (`financiamento.html`)

`GET /api/macro/financiamento`: resultado primário/nominal (BCB SGS 4649/4583/4616), emissões, resgates e
emissão líquida (Tesouro Transparente, XLSX), estoque da DPF e vencimentos (CSV), indexadores e prazo médio
(cálculo próprio a partir do estoque) e comparativos mensais/anuais. O custo médio da DPF não tem série
aberta e é declarado como indisponível (Relatório Mensal da Dívida). A NFSP é o resultado nominal
“abaixo da linha” do BCB (setor público consolidado, exceto Petrobras e Eletrobras).

### Ciclo econômico (`ciclo.html`)

`GET /api/macro/ciclo`: PIB trimestral (IBGE SIDRA 5932), IBC-Br dessazonalizado (BCB SGS 24364), PNAD (6381, 6390, 6320),
produção industrial (8888), vendas do varejo (8880), IPCA, e relação com contas públicas (Resultado do Tesouro
Nacional e séries fiscais do BCB). A leitura “expansão / desaceleração / possível contração” usa disseminação,
duração e intensidade com limiares explícitos, é interpretativa e não substitui a datação do CODACE/FGV IBRE.
