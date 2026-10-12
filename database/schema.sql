-- =============================================================================
-- ImpactoCidadão — esquema PostgreSQL (local, Docker ou Supabase)
--
-- * Idempotente: pode ser executado mais de uma vez sem erro.
-- * O vocabulário de `perfis` e `categorias` precisa ficar em sincronia com
--   backend/schemas.py (Literal PerfilId / CategoriaId).
-- * Privacidade (LGPD): NÃO armazenamos CPF, título de eleitor, data de
--   nascimento nem bens de candidatos — apenas o necessário para exibir o plano.
-- =============================================================================

-- Requer PostgreSQL 13+ (gen_random_uuid() nativo; nenhuma extensão necessária).

-- -----------------------------------------------------------------------------
-- 0. Função utilitária: mantém `atualizado_em` em dia
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_set_atualizado_em() RETURNS trigger AS $$
BEGIN
    NEW.atualizado_em := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------------------------------
-- 1. Tabelas de referência
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS perfis (
    id         text PRIMARY KEY CHECK (id ~ '^[a-z_]+$'),
    nome       text NOT NULL,
    descricao  text NOT NULL DEFAULT '',
    icone      text NOT NULL DEFAULT 'fa-user',
    ordem      smallint NOT NULL DEFAULT 0
);

-- Mesmo vocabulário para leis (categorias) e para planos de governo (temas).
CREATE TABLE IF NOT EXISTS categorias (
    id      text PRIMARY KEY CHECK (id ~ '^[a-z_]+$'),
    nome    text NOT NULL,
    icone   text NOT NULL DEFAULT 'fa-layer-group',
    ordem   smallint NOT NULL DEFAULT 0
);

INSERT INTO perfis (id, nome, descricao, icone, ordem) VALUES
    ('clt',                'Trabalhador CLT',     'Empregado com carteira assinada',                       'fa-user-tie',       1),
    ('mei',                'MEI / Autônomo',      'Microempreendedor individual e trabalhador por conta',  'fa-store',          2),
    ('estudante',          'Estudante',           'Ensino básico, técnico ou superior',                    'fa-graduation-cap', 3),
    ('aposentado',         'Aposentado / Idoso',  'Aposentados, pensionistas e pessoas idosas',            'fa-heart-pulse',    4),
    ('servidor',           'Servidor Público',    'Servidores e empregados públicos',                      'fa-landmark',       5),
    ('produtor_rural',     'Produtor Rural',      'Agricultores e pecuaristas, familiares ou empresariais','fa-tractor',        6),
    ('desempregado',       'Em busca de emprego', 'Quem está sem trabalho ou em transição',                'fa-magnifying-glass', 7),
    ('beneficiario_social','Beneficiário social', 'Famílias em programas de transferência de renda',       'fa-hand-holding-heart', 8)
ON CONFLICT (id) DO UPDATE
    SET nome = EXCLUDED.nome, descricao = EXCLUDED.descricao,
        icone = EXCLUDED.icone, ordem = EXCLUDED.ordem;

INSERT INTO categorias (id, nome, icone, ordem) VALUES
    ('impostos',      'Impostos e bolso',            'fa-wallet',          1),
    ('economia',      'Economia e crédito',          'fa-chart-line',      2),
    ('emprego',       'Emprego e renda',             'fa-briefcase',       3),
    ('previdencia',   'Previdência e aposentadoria', 'fa-person-cane',     4),
    ('assistencia',   'Assistência social',          'fa-hands-holding-child', 5),
    ('saude',         'Saúde',                       'fa-notes-medical',   6),
    ('educacao',      'Educação',                    'fa-book-open',       7),
    ('seguranca',     'Segurança pública',           'fa-shield-halved',   8),
    ('habitacao',     'Moradia e saneamento',        'fa-house',           9),
    ('transporte',    'Transporte e infraestrutura', 'fa-bus',            10),
    ('meio_ambiente', 'Meio ambiente e clima',       'fa-leaf',           11),
    ('agro',          'Campo e agropecuária',        'fa-wheat-awn',      12),
    ('outros',        'Outros temas',                'fa-ellipsis',       13)
ON CONFLICT (id) DO UPDATE
    SET nome = EXCLUDED.nome, icone = EXCLUDED.icone, ordem = EXCLUDED.ordem;

-- -----------------------------------------------------------------------------
-- 2. Usuários
--    `auth_uid` liga ao Supabase Auth (auth.users.id) quando usado; em ambiente
--    local pode ficar nulo. Sem senha aqui: autenticação fica a cargo do Auth.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usuarios (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_uid         uuid UNIQUE,
    email            text UNIQUE,
    perfil_id        text REFERENCES perfis(id) ON DELETE SET NULL,
    municipio_ibge   integer,
    uf               char(2),
    notificar_email  boolean NOT NULL DEFAULT false,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    atualizado_em    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_usuarios_perfil ON usuarios (perfil_id);

DROP TRIGGER IF EXISTS trg_usuarios_atualizado ON usuarios;
CREATE TRIGGER trg_usuarios_atualizado BEFORE UPDATE ON usuarios
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

-- -----------------------------------------------------------------------------
-- 3. Proposições (MPs, PLs, PECs...) já traduzidas pela IA
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proposicoes_analisadas (
    id                 bigserial PRIMARY KEY,
    fonte              text NOT NULL CHECK (fonte IN ('camara', 'senado')),
    id_externo         text NOT NULL,
    sigla_tipo         text NOT NULL,
    numero             integer,
    ano                integer,
    identificacao      text NOT NULL,                         -- ex.: "MPV 1394/2026"
    ementa_oficial     text NOT NULL DEFAULT '',
    origem             text NOT NULL DEFAULT 'executivo'
                       CHECK (origem IN ('executivo', 'alto_impacto')),
    url_fonte          text,                                  -- página oficial da proposição
    url_inteiro_teor   text,
    data_apresentacao  date,
    situacao_atual     text,                                  -- texto bruto da fonte
    linha_do_tempo     jsonb NOT NULL DEFAULT '[]'::jsonb,    -- eventos reais de tramitação

    -- Saída estruturada da IA (ver backend/prompts.py)
    titulo_simples     text,
    resumo_cidadao     text,
    perfis_impactados  text[] NOT NULL DEFAULT '{}',
    categorias         text[] NOT NULL DEFAULT '{}',
    impacto_pratico    text,
    ponto_positivo     text,
    ponto_atencao      text,
    status_tramitacao  text,
    relevancia_bolso   text CHECK (relevancia_bolso IN ('alta', 'media', 'baixa', 'nenhuma')),
    analise_bruta      jsonb,

    -- Controle do pipeline
    analise_status     text NOT NULL DEFAULT 'pendente'
                       CHECK (analise_status IN ('pendente', 'ok', 'falha', 'sem_texto', 'texto_longo')),
    analise_erro       text,
    texto_fonte        text CHECK (texto_fonte IN ('inteiro_teor', 'ementa')),
    texto_hash         text,
    modelo_llm         text,
    analisado_em       timestamptz,
    criado_em          timestamptz NOT NULL DEFAULT now(),
    atualizado_em      timestamptz NOT NULL DEFAULT now(),

    -- Busca textual em português (arrays ficam de fora: array_to_string não é IMMUTABLE)
    busca tsvector GENERATED ALWAYS AS (
        to_tsvector('portuguese',
            coalesce(identificacao, '')     || ' ' ||
            coalesce(titulo_simples, '')    || ' ' ||
            coalesce(resumo_cidadao, '')    || ' ' ||
            coalesce(impacto_pratico, '')   || ' ' ||
            coalesce(ementa_oficial, ''))
    ) STORED,

    CONSTRAINT uq_proposicao_fonte UNIQUE (fonte, id_externo)
);

CREATE INDEX IF NOT EXISTS ix_prop_busca      ON proposicoes_analisadas USING gin (busca);
CREATE INDEX IF NOT EXISTS ix_prop_perfis     ON proposicoes_analisadas USING gin (perfis_impactados);
CREATE INDEX IF NOT EXISTS ix_prop_categorias ON proposicoes_analisadas USING gin (categorias);
CREATE INDEX IF NOT EXISTS ix_prop_status     ON proposicoes_analisadas (analise_status, status_tramitacao);
CREATE INDEX IF NOT EXISTS ix_prop_data       ON proposicoes_analisadas (data_apresentacao DESC NULLS LAST);

DROP TRIGGER IF EXISTS trg_prop_atualizado ON proposicoes_analisadas;
CREATE TRIGGER trg_prop_atualizado BEFORE UPDATE ON proposicoes_analisadas
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

-- -----------------------------------------------------------------------------
-- 4. Votos da comunidade (apenas sobre leis/MPs — NÃO sobre candidatos)
--    Um voto por usuário OU por token anônimo (hash com sal; sem IP).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS votos_comunitarios (
    id             bigserial PRIMARY KEY,
    proposicao_id  bigint NOT NULL REFERENCES proposicoes_analisadas(id) ON DELETE CASCADE,
    usuario_id     uuid REFERENCES usuarios(id) ON DELETE CASCADE,
    anon_hash      text,
    voto           text NOT NULL CHECK (voto IN ('concordo', 'concordo_com_ajustes', 'discordo')),
    perfil_id      text REFERENCES perfis(id) ON DELETE SET NULL,
    criado_em      timestamptz NOT NULL DEFAULT now(),
    atualizado_em  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_voto_identidade CHECK (usuario_id IS NOT NULL OR anon_hash IS NOT NULL),
    CONSTRAINT uq_voto_usuario UNIQUE (proposicao_id, usuario_id),
    CONSTRAINT uq_voto_anonimo UNIQUE (proposicao_id, anon_hash)
);
CREATE INDEX IF NOT EXISTS ix_votos_proposicao ON votos_comunitarios (proposicao_id);

DROP TRIGGER IF EXISTS trg_votos_atualizado ON votos_comunitarios;
CREATE TRIGGER trg_votos_atualizado BEFORE UPDATE ON votos_comunitarios
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

CREATE OR REPLACE VIEW vw_placar_votos AS
SELECT proposicao_id,
       count(*) FILTER (WHERE voto = 'concordo')              AS concordo,
       count(*) FILTER (WHERE voto = 'concordo_com_ajustes')  AS concordo_com_ajustes,
       count(*) FILTER (WHERE voto = 'discordo')              AS discordo,
       count(*)                                               AS total
  FROM votos_comunitarios
 GROUP BY proposicao_id;

-- -----------------------------------------------------------------------------
-- 5. Notificações (geradas por trigger quando uma proposição é analisada ou
--    muda de status, para quem tem um dos perfis impactados)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notificacoes (
    id             bigserial PRIMARY KEY,
    usuario_id     uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    proposicao_id  bigint REFERENCES proposicoes_analisadas(id) ON DELETE CASCADE,
    tipo           text NOT NULL CHECK (tipo IN ('nova_proposicao', 'mudanca_status')),
    titulo         text NOT NULL,
    mensagem       text NOT NULL DEFAULT '',
    lida           boolean NOT NULL DEFAULT false,
    criado_em      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_notif_usuario ON notificacoes (usuario_id, lida, criado_em DESC);

CREATE OR REPLACE FUNCTION fn_notificar_proposicao() RETURNS trigger AS $$
DECLARE
    v_tipo text;
    v_msg  text;
BEGIN
    IF NEW.analise_status <> 'ok' THEN
        RETURN NEW;
    END IF;

    IF TG_OP = 'INSERT' THEN
        v_tipo := 'nova_proposicao';
        v_msg  := coalesce(NEW.resumo_cidadao, '');
    ELSIF OLD.analise_status IS DISTINCT FROM 'ok' THEN
        v_tipo := 'nova_proposicao';
        v_msg  := coalesce(NEW.resumo_cidadao, '');
    ELSIF NEW.status_tramitacao IS DISTINCT FROM OLD.status_tramitacao THEN
        v_tipo := 'mudanca_status';
        v_msg  := 'Novo status: ' || coalesce(NEW.status_tramitacao, '');
    ELSE
        RETURN NEW;
    END IF;

    INSERT INTO notificacoes (usuario_id, proposicao_id, tipo, titulo, mensagem)
    SELECT u.id, NEW.id, v_tipo, coalesce(NEW.titulo_simples, NEW.identificacao), v_msg
      FROM usuarios u
     WHERE u.perfil_id = ANY (NEW.perfis_impactados)
        OR 'todos' = ANY (NEW.perfis_impactados);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prop_notificar ON proposicoes_analisadas;
CREATE TRIGGER trg_prop_notificar
    AFTER INSERT OR UPDATE OF analise_status, status_tramitacao ON proposicoes_analisadas
    FOR EACH ROW EXECUTE FUNCTION fn_notificar_proposicao();

-- -----------------------------------------------------------------------------
-- 6. Candidatos e planos de governo (fonte: TSE / DivulgaCandContas)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidatos (
    sq_candidato          bigint PRIMARY KEY,                -- id oficial do TSE
    ano_eleicao           integer NOT NULL,
    cargo                 text NOT NULL,
    nome_urna             text NOT NULL,
    numero                integer,
    sigla_partido         text,
    situacao_candidatura  text,                              -- ex.: Deferido, Indeferido, Renúncia
    totalizacao           text,                              -- ex.: 2º turno, Não eleito, Eleito
    vice                  text,
    atualizado_em         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cand_cargo ON candidatos (ano_eleicao, cargo);

DROP TRIGGER IF EXISTS trg_cand_atualizado ON candidatos;
CREATE TRIGGER trg_cand_atualizado BEFORE UPDATE ON candidatos
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

CREATE TABLE IF NOT EXISTS planos_governo (
    id                     bigserial PRIMARY KEY,
    sq_candidato           bigint NOT NULL REFERENCES candidatos(sq_candidato) ON DELETE CASCADE,
    arquivo_id             bigint NOT NULL UNIQUE,           -- idArquivo do TSE
    nome_arquivo           text,
    url_pdf                text NOT NULL,                    -- link oficial para o PDF original
    paginas                integer,
    caracteres             integer,
    pdf_sha256             text,
    status                 text NOT NULL DEFAULT 'pendente'
                           CHECK (status IN ('pendente', 'aguardando_pdf', 'sem_texto',
                                             'texto_longo', 'ok', 'falha')),
    resumo_geral           text,
    analise_bruta          jsonb,
    propostas_descartadas  integer NOT NULL DEFAULT 0,       -- citações que não bateram com o PDF
    modelo_llm             text,
    erro                   text,
    analisado_em           timestamptz,
    criado_em              timestamptz NOT NULL DEFAULT now(),
    atualizado_em          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_planos_candidato ON planos_governo (sq_candidato, status);

DROP TRIGGER IF EXISTS trg_planos_atualizado ON planos_governo;
CREATE TRIGGER trg_planos_atualizado BEFORE UPDATE ON planos_governo
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

-- Um registro por (plano, tema): inclui os temas que o plano NÃO aborda.
CREATE TABLE IF NOT EXISTS temas_candidato (
    plano_id       bigint NOT NULL REFERENCES planos_governo(id) ON DELETE CASCADE,
    categoria_id   text   NOT NULL REFERENCES categorias(id),
    mencionado     boolean NOT NULL,
    resumo_neutro  text NOT NULL DEFAULT '',
    PRIMARY KEY (plano_id, categoria_id)
);

CREATE TABLE IF NOT EXISTS propostas_candidato (
    id                   bigserial PRIMARY KEY,
    plano_id             bigint NOT NULL REFERENCES planos_governo(id) ON DELETE CASCADE,
    sq_candidato         bigint NOT NULL REFERENCES candidatos(sq_candidato) ON DELETE CASCADE,
    categoria_id         text   NOT NULL REFERENCES categorias(id),
    descricao            text   NOT NULL,
    trecho_literal       text   NOT NULL,                    -- verificado contra o texto do PDF
    pagina               integer,                            -- calculada pelo código, não pela IA
    tem_meta_numerica    boolean NOT NULL DEFAULT false,
    tem_prazo            boolean NOT NULL DEFAULT false,
    tem_fonte_recurso    boolean NOT NULL DEFAULT false,
    canal_execucao       text NOT NULL DEFAULT 'nao_especificado'
                         CHECK (canal_execucao IN ('beneficio_direto', 'programa_via_municipio',
                                                   'repasse_a_municipios', 'regulacao_nacional',
                                                   'investimento_federal_direto', 'nao_especificado')),
    perfis_beneficiados  text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_propcand_plano   ON propostas_candidato (plano_id, categoria_id);
CREATE INDEX IF NOT EXISTS ix_propcand_perfis  ON propostas_candidato USING gin (perfis_beneficiados);

-- -----------------------------------------------------------------------------
-- 7. Row Level Security
--    O backend conecta com o dono das tabelas (ignora RLS). Se as tabelas forem
--    expostas pelo PostgREST do Supabase, o papel `anon` só LÊ conteúdo público;
--    tabelas com dados de usuário ficam sem nenhuma política (acesso negado).
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['perfis', 'categorias', 'usuarios', 'proposicoes_analisadas',
                             'votos_comunitarios', 'notificacoes', 'candidatos',
                             'planos_governo', 'temas_candidato', 'propostas_candidato']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        FOREACH t IN ARRAY ARRAY['perfis', 'categorias', 'proposicoes_analisadas', 'candidatos',
                                 'planos_governo', 'temas_candidato', 'propostas_candidato']
        LOOP
            EXECUTE format('DROP POLICY IF EXISTS leitura_publica ON %I', t);
            EXECUTE format('CREATE POLICY leitura_publica ON %I FOR SELECT TO anon, authenticated USING (true)', t);
        END LOOP;
    END IF;
END $$;
