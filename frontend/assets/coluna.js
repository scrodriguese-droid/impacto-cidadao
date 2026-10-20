/* Coluna de opinião: aba lateral expansível, em cada página, com comentários no estilo post-it.
 *
 * Os comentários ficam na mesma tabela e API dos post-its (/api/postits), numa "página" própria:
 * `coluna-<id da página>`. Assim herdam as regras que já existem: só quem tem conta de editor escreve; o
 * comentário nasce como rascunho (só o autor vê) e pode ser publicado para todos; visitantes só leem os publicados.
 * A lista aparece da data mais recente para a mais antiga.
 * Depende de comum.js (esc) e de window.__PAGINA (id da página, definido em renderLayout).
 */
(function () {
    const PAGINA = window.__PAGINA;
    if (!PAGINA || !/^[a-z0-9_-]{1,32}$/.test(PAGINA)) return;
    const CHAVE = 'coluna-' + PAGINA;
    const LIMITE = 2000;
    let ME = { logado: false };
    let ITENS = [];
    let aberta = false;

    const css = document.createElement('style');
    css.textContent = `
    body button.col-aba, body button.col-aba:hover { position: fixed; right: 0; top: 42%; z-index: 55; writing-mode: vertical-rl; transform: rotate(180deg); padding: 1rem .55rem;
        background: var(--fg) !important; color: var(--papel) !important; border: 0 !important; font: 800 .7rem/1 var(--fonte-texto); letter-spacing: .14em; text-transform: uppercase; cursor: pointer; }
    body button.col-aba * { color: inherit !important; }
    .col-aba span { background: var(--vermelho, #A92823); color: #fff; padding: .1rem .3rem; margin-bottom: .4rem; display: inline-block; transform: rotate(90deg); }
    .col-painel { position: fixed; top: 0; right: 0; bottom: 0; z-index: 66; width: min(100vw, 24rem); overflow-y: auto; padding: 1rem 1rem 6rem; /* folga embaixo: a barra de post-its do editor fica por cima */
        background: var(--papel) !important; color: var(--fg) !important; border-left: 2px solid var(--fg); box-shadow: -6px 0 24px rgba(0,0,0,.25); }
    .col-painel[hidden] { display: none; }
    .col-painel .col-titulo { font: 400 1.9rem/.95 var(--fonte-titulo); text-transform: uppercase; margin: 1.4rem 0 .3rem; }
    .col-painel .col-sub { font-size: .72rem; opacity: .75; margin-bottom: .8rem; }
    .col-fechar { float: right; background: none !important; border: 0 !important; font-weight: 800; text-decoration: underline; cursor: pointer; font-size: .75rem; }
    .col-form textarea { width: 100%; min-height: 6rem; padding: .6rem; background: var(--postit, #fde68a) !important; color: #111 !important; border: 1px solid #111; font: .85rem/1.4 var(--fonte-texto); resize: vertical; }
    .col-form .col-linha { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; margin-top: .4rem; }
    .col-form button, .col-nota button { padding: .35rem .6rem; border: 1px solid var(--fg) !important; background: transparent !important; color: var(--fg) !important; font: 800 .65rem/1 var(--fonte-texto); letter-spacing: .06em; text-transform: uppercase; cursor: pointer; }
    .col-form button.col-pub { background: var(--fg) !important; color: var(--papel) !important; }
    .col-contador { margin-left: auto; font-size: .65rem; opacity: .7; }
    .col-lista { list-style: none; margin: 1rem 0 0; padding: 0; display: grid; gap: .8rem; }
    .col-nota { background: var(--postit, #fde68a); color: #111; border: 1px solid #111; padding: .8rem .9rem; }
    .col-nota .col-cab { font-size: .68rem; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; }
    .col-nota .col-rasc { background: #111; color: #fff; padding: 0 .3rem; margin-left: .3rem; }
    .col-nota p { margin: .4rem 0 0; font-size: .85rem; line-height: 1.4; overflow-wrap: anywhere; }
    .col-nota .col-acoes { margin-top: .6rem; display: flex; gap: .4rem; }
    .col-nota button { border-color: #111 !important; color: #111 !important; }
    .col-vazio { font-size: .8rem; opacity: .75; }
    .col-aviso { font-size: .72rem; color: var(--vermelho, #A92823); margin-top: .4rem; }
    `;
    document.head.append(css);

    const aba = Object.assign(document.createElement('button'), { type: 'button', className: 'col-aba', title: 'Abrir a coluna de opinião' });
    aba.setAttribute('aria-expanded', 'false');
    aba.innerHTML = 'Coluna de opinião <span id="colN" hidden></span>';
    const painel = Object.assign(document.createElement('aside'), { className: 'col-painel', hidden: true });
    painel.setAttribute('aria-label', 'Coluna de opinião');
    document.body.append(aba, painel);

    async function chamar(metodo, caminho, corpo) {
        let r;
        try {
            r = await fetch('/api' + caminho, { method: metodo, headers: corpo ? { 'Content-Type': 'application/json' } : undefined, body: corpo ? JSON.stringify(corpo) : undefined, credentials: 'same-origin' });
        } catch (_) { throw new Error('Não consegui falar com o servidor. Tente de novo em instantes.'); }
        let j = null;
        try { j = await r.json(); } catch (_) { /* sem corpo */ }
        if (!r.ok) throw new Error(j && typeof j.detail === 'string' ? j.detail : r.status === 401 ? 'Sua sessão expirou. Entre de novo pela Área do editor.' : `Erro ${r.status}`);
        return j;
    }

    const quando = (d) => new Date(d).toLocaleString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    // da data mais recente para a mais antiga (empate: o que foi criado por último vem primeiro)
    const ordenados = () => [...ITENS].sort((a, b) => (a.criado_em < b.criado_em ? 1 : a.criado_em > b.criado_em ? -1 : b.id - a.id));
    const paragrafos = (t) => String(t || '').split(/\n+/).map((x) => x.trim()).filter(Boolean).map((x) => `<p>${esc(x)}</p>`).join('');

    function notaHtml(n) {
        return `<li class="col-nota" data-id="${n.id}">
            <div class="col-cab">${esc(n.autor)} · ${esc(quando(n.criado_em))}${n.publico ? '' : '<span class="col-rasc">rascunho</span>'}</div>
            ${paragrafos(n.texto)}
            ${n.minha ? `<div class="col-acoes"><button type="button" data-col-pub="${n.id}">${n.publico ? 'Tirar do ar' : 'Publicar'}</button><button type="button" data-col-del="${n.id}">Apagar</button></div>` : ''}</li>`;
    }

    function desenhar() {
        const n = ITENS.length;
        const cont = document.getElementById('colN');
        if (cont) { cont.hidden = !n; cont.textContent = n; }
        painel.innerHTML = `<button type="button" class="col-fechar" data-col-fechar>fechar</button>
            <p class="col-titulo" role="heading" aria-level="2">Coluna de opinião</p>
            <p class="col-sub">Comentários sobre esta página, do mais recente para o mais antigo. São opiniões de quem edita o site, não dados oficiais.</p>
            ${ME.logado ? `<form class="col-form" id="colForm">
                <label class="sr-only" for="colTexto">Novo comentário</label>
                <textarea id="colTexto" maxlength="${LIMITE}" placeholder="Escreva seu comentário…"></textarea>
                <div class="col-linha"><button type="submit" class="col-pub" data-publicar="1">Publicar</button><button type="submit" data-publicar="0">Guardar rascunho</button><span class="col-contador" id="colCont">0/${LIMITE}</span></div>
                <p class="col-aviso" id="colAviso" role="alert"></p></form>` : ''}
            <ul class="col-lista">${n ? ordenados().map(notaHtml).join('') : '<li class="col-vazio">Ainda não há comentários publicados nesta página.</li>'}</ul>`;
    }

    async function carregar() {
        try { ME = await chamar('GET', '/editor/eu'); } catch (_) { ME = { logado: false }; }
        try { ITENS = (await chamar('GET', '/postits?pagina=' + encodeURIComponent(CHAVE))).itens; } catch (_) { ITENS = []; }
        desenhar();
    }

    function alternar(abrir) {
        aberta = abrir;
        painel.hidden = !abrir;
        aba.hidden = abrir;
        aba.setAttribute('aria-expanded', String(abrir));
        if (abrir) (painel.querySelector('textarea') || painel.querySelector('[data-col-fechar]')).focus();
    }

    aba.addEventListener('click', () => alternar(true));
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && aberta) alternar(false); });
    painel.addEventListener('input', (ev) => { if (ev.target.id === 'colTexto') document.getElementById('colCont').textContent = `${ev.target.value.length}/${LIMITE}`; });
    painel.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const texto = document.getElementById('colTexto').value.trim();
        const publico = ev.submitter && ev.submitter.dataset.publicar === '1';
        const aviso = document.getElementById('colAviso');
        aviso.textContent = '';
        if (!texto) { aviso.textContent = 'Escreva alguma coisa antes de salvar.'; return; }
        try {
            await chamar('POST', '/postits', { pagina: CHAVE, texto, publico, rotulo: 'Coluna de opinião', ancora: '' });
            await carregar();
            alternar(true);
        } catch (e) { aviso.textContent = e.message; }
    });
    painel.addEventListener('click', async (ev) => {
        if (ev.target.closest('[data-col-fechar]')) { alternar(false); return; }
        const pub = ev.target.closest('[data-col-pub]'), del = ev.target.closest('[data-col-del]');
        try {
            if (pub) {
                const n = ITENS.find((x) => x.id === Number(pub.dataset.colPub));
                await chamar('PUT', '/postits/' + n.id, { pagina: CHAVE, texto: n.texto, publico: !n.publico, rotulo: n.rotulo, ancora: n.ancora });
            } else if (del) {
                if (!confirm('Apagar este comentário?')) return;
                await chamar('DELETE', '/postits/' + del.dataset.colDel);
            } else return;
            await carregar();
            alternar(true);
        } catch (e) { alert(e.message); }
    });

    carregar();
})();
