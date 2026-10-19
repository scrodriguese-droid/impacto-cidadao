# Como publicar o ImpactoCidadão de graça (Render + Supabase)

O site é um único servidor (FastAPI) que entrega as páginas e a API. Na hospedagem gratuita:

* **Render** roda o servidor. O plano gratuito "dorme" após ~15 minutos sem visitas (a primeira visita seguinte
  demora cerca de um minuto) e **não guarda arquivos entre reinícios**.
* **Supabase** guarda o banco PostgreSQL (leis, candidatos, contas de editor e post-its). O plano gratuito pausa o
  projeto após cerca de uma semana sem uso; é só reativar no painel.

Os planos gratuitos mudam: confira as regras de cada serviço antes de começar.

## 1. Banco no Supabase

1. Crie uma conta em <https://supabase.com> e um projeto novo. Anote a senha do banco que você definir.
2. **SQL Editor** → cole o conteúdo de `database/schema.sql` → **Run**. Isso cria as tabelas.
3. **Project Settings → Database → Connection string**: copie a do **pooler** (modo *Transaction*, porta 6543) e
   troque `[YOUR-PASSWORD]` pela senha. Ela tem este formato:
   `postgresql://postgres.<ref>:<SENHA>@aws-0-<regiao>.pooler.supabase.com:6543/postgres?sslmode=require`

   Esta linha é um segredo: não a coloque no GitHub nem em conversas.

## 2. Levar os dados que você já coletou para o Supabase

Opção A (mantém as traduções por IA já feitas): com o Docker ligado, exporte o banco local e importe no Supabase.

```powershell
docker compose up -d
docker exec impacto-db pg_dump -U impacto -d impacto --data-only --inserts --no-owner > dados\dump.sql
docker exec -i impacto-db psql "postgresql://postgres.<ref>:<SENHA>@aws-0-<regiao>.pooler.supabase.com:6543/postgres?sslmode=require" < dados\dump.sql
Remove-Item dados\dump.sql
```

Opção B (do zero): aponte `DATABASE_URL` do seu `.env` para o Supabase e rode os coletores
(`python backend\coletor.py --dias 30` e `python backend\coletor_candidatos.py`). Sem chave de IA, os atos entram
como "pendentes".

Atualizações futuras: rode os coletores no seu PC, com `DATABASE_URL` apontando para o Supabase.

## 3. Servidor no Render

1. Suba o código para o GitHub (já está em `scrodriguese-droid`, ramo `feat/pagina-economia`; faça o merge na `main`).
2. Em <https://render.com>: **New → Blueprint** → escolha o repositório. O arquivo `render.yaml` já traz o comando
   de instalação e de início.
3. Preencha as variáveis que o painel pedir:
   * `DATABASE_URL`: a connection string do passo 1;
   * `CORS_ORIGINS`: o endereço que o Render der ao site, por exemplo `https://impacto-cidadao.onrender.com`;
   * `CONTATO_EMAIL` (opcional).
4. Aguarde o *deploy*. O site abre no endereço do Render.

## 4. Conta de editor e post-its

As contas ficam no mesmo Postgres (`EDITOR_BANCO=postgres`, já no `render.yaml`). Para a primeira conta, gere um
convite com o terminal, apontando para o Supabase:

```powershell
$env:EDITOR_BANCO = "postgres"; $env:DATABASE_URL = "<a connection string>"
python backend\editor.py convite
```

Abra o site → **Área do editor** → **Tenho um convite**. O site publicado usa HTTPS, necessário para o cookie de
login.

## 5. Dados que o servidor gratuito não coleta sozinho

`dados/orcamento/`, `dados/autoria/` e `dados/votacoes/` agora vão para o repositório (o servidor gratuito não
tem disco permanente e não aguenta os coletores pesados). Quando recoletar no seu PC
(`python backend\coletor_orgaos.py`, `coletor_autoria.py`, `coletor_votacoes.py`), faça commit dos arquivos novos
e o Render publica de novo sozinho.

## O que esperar

* Primeira visita depois de um tempo parado: ~1 minuto para acordar.
* Banco Central (BCB) pode continuar recusando consultas: os indicadores afetados mostram "Fonte indisponível".
* `ANTHROPIC_API_KEY` só é necessária para traduzir leis novas; não é preciso para o site funcionar.
