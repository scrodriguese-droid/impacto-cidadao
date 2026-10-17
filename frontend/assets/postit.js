/* Post-its do editor.
 *
 * Quem entra com uma conta de editor (menu lateral, "Área do editor") cola post-its em qualquer trecho de qualquer
 * página: clica em "Novo post-it", clica no trecho e escreve. O post-it nasce como rascunho (só o autor vê) e pode
 * ser publicado para todos. Visitantes só veem os publicados, como marcadores numerados que abrem a nota.
 *
 * O trecho é guardado como seletor CSS. Se a página mudar e o trecho sumir, o post-it continua na lista do editor,
 * marcado como "sem trecho", e pode ser reancorado editando-o.
 * Depende de comum.js (esc, urlSegura, api) e de window.__PAGINA (id da página, definido em renderLayout).
 */
(function () {
    const PAGINA = window.__PAGINA;
    if (!PAGINA || !/^[a-z0-9_-]{1,40}$/.test(PAGINA)) return;

    let ME = { logado: false };
    let NOTAS = [];
    let criando = false;
    let aberta = null;           // id da nota com o post-it aberto
    let destaque = null;         // elemento sob o mouse no modo "escolher trecho"

    const $$ = (s, r = document) => [...r.querySelectorAll(s)];
    const camada = Object.assign(document.createElement('div'), { id: 'postitCamada', className: 'postit-camada' });
    document.body.append(camada);

    /* ---------- chamadas ---------- */
    async function chamar(metodo, caminho, corpo) {
        const r = await fetch('/api' + caminho, { method: metodo, headers: corpo ? { 'Content-Type': 'application/json' } : undefined, body: corpo ? JSON.stringify(corpo) : undefined, credentials: 'same-origin' });
        let j = null;
        try { j = await r.json(); } catch (_) { /* sem corpo */ }
        if (!r.ok) throw new Error((j && typeof j.detail === 'string' && j.detail) || (r.status === 422 ? 'Confira os campos.' : `Erro ${r.status}`));
        return j;
    }
    async function carregar() {
        try { ME = await chamar('GET', '/editor/eu'); } catch (_) { ME = { logado: false }; }
        try { NOTAS = (await chamar('GET', '/postits?pagina=' + encodeURIComponent(PAGINA))).itens; } catch (_) { NOTAS = []; }
        desenharTudo();
    }

    /* ---------- texto do post-it: negrito, itálico, link e quebra de parágrafo ---------- */
    function formatar(t) {
        const inline = (x) => esc(x)
            .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, rot, url) => `<a class="underline" target="_blank" rel="noopener" href="${esc(urlSegura(url))}">${rot}</a>`)
            .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*]+)\*/g, '$1<i>$2</i>');
        return String(t || '').split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean).map((p) => `<p>${inline(p)}</p>`).join('');
    }
    const quando = (d) => new Date(d).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' });

    /* ---------- seletor CSS de um elemento ---------- */
    function seletorDe(el) {
        if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
        const partes = [];
        for (let n = el; n && n !== document.body; n = n.parentElement) {
            if (n.id && document.querySelectorAll('#' + CSS.escape(n.id)).length === 1) { partes.unshift('#' + CSS.escape(n.id)); break; }
            const irmaos = [...n.parentElement.children].filter((x) => x.tagName === n.tagName);
            partes.unshift(n.tagName.toLowerCase() + (irmaos.length > 1 ? `:nth-of-type(${irmaos.indexOf(n) + 1})` : ''));
        }
        return partes.join(' > ');
    }
    const achar = (sel) => { try { return sel ? document.querySelector(sel) : null; } catch (_) { return null; } };
    const CANDIDATOS = 'h1,h2,h3,h4,p,li,tr,article,section,blockquote,figure,.cartaz,.corte,.ficha-linha,.estagio,.es-col,.area-btn,.matriz-col,.linha-partido,.portal,.trib-grupo';
    const dentroDaUI = (el) => el.closest('.postit-dock, .postit-camada, .postit, dialog, #menuLateral, header.lg\\:hidden, footer');
    const escolher = (el) => (el.closest('main') && el.closest(CANDIDATOS)) || null;

    /* ---------- marcadores e post-its abertos ---------- */
    function posicionar() {
        const larg = document.documentElement.clientWidth;
        $$('.postit-marca', camada).forEach((m) => {
            const alvo = achar(m.dataset.ancora);
            const r = alvo && alvo.getBoundingClientRect();
            if (!r || (!r.width && !r.height)) { m.hidden = true; return; }
            m.hidden = false;
            m.style.left = Math.max(4, Math.min(larg - 34, r.right + window.scrollX - 30)) + 'px';
            m.style.top = Math.max(4, r.top + window.scrollY - 14) + 'px';
        });
        const card = $$('.postit', camada)[0];
        if (card) {
            const m = camada.querySelector(`.postit-marca[data-id="${card.dataset.id}"]`);
            if (m && !m.hidden) {
                card.style.top = (parseFloat(m.style.top) + 34) + 'px';
                card.style.left = Math.max(8, Math.min(larg - card.offsetWidth - 8, parseFloat(m.style.left) - card.offsetWidth + 34)) + 'px';
            } else { card.style.top = (window.scrollY + 90) + 'px'; card.style.left = Math.max(8, larg - card.offsetWidth - 16) + 'px'; }
        }
    }

    function cartaoDe(n) {
        const dono = n.minha;
        return `<div class="postit" data-id="${n.id}" role="dialog" aria-label="Post-it de ${esc(n.autor)}">
            <p class="postit-cab"><b>${esc(n.autor)}</b> · ${esc(quando(n.atualizado_em))}${n.publico ? '' : ' · <span class="postit-rasc">rascunho</span>'}</p>
            ${n.rotulo ? `<p class="postit-trecho">Sobre: ${esc(n.rotulo)}</p>` : ''}
            <div class="postit-texto">${formatar(n.texto)}</div>
            <p class="postit-acoes">${dono ? `<button type="button" data-postit-editar="${n.id}">Editar</button><button type="button" data-postit-apagar="${n.id}">Apagar</button>` : ''}<button type="button" data-postit-fechar>Fechar</button></p>
        </div>`;
    }

    function desenharMarcadores() {
        camada.innerHTML = NOTAS.map((n, i) => `<button type="button" class="postit-marca${n.publico ? '' : ' rascunho'}" data-id="${n.id}" data-ancora="${esc(n.ancora)}" aria-label="Post-it ${i + 1} de ${esc(n.autor)}${n.publico ? '' : ' (rascunho)'}">${i + 1}</button>`).join('');
        if (aberta != null) {
            const n = NOTAS.find((x) => x.id === aberta);
            if (n) camada.insertAdjacentHTML('beforeend', cartaoDe(n)); else aberta = null;
        }
        posicionar();
    }

    /* ---------- barra do editor ---------- */
    function desenharDock() {
        $$('.postit-dock').forEach((d) => d.remove());
        if (!ME.logado) return;
        const meus = NOTAS.filter((n) => n.minha).length;
        const dock = Object.assign(document.createElement('div'), { className: 'postit-dock' });
        dock.setAttribute('role', 'toolbar'); dock.setAttribute('aria-label', 'Editor');
        dock.innerHTML = `<span class="postit-quem">Editor<b>${esc(ME.nome)}</b></span>
            <button type="button" data-dock="novo">${criando ? 'Cancelar' : 'Novo post-it'}</button>
            <button type="button" data-dock="lista">Meus post-its · ${meus}</button>
            <button type="button" data-dock="sair">Sair</button>`;
        document.body.append(dock);
    }

    function desenharTudo() { desenharMarcadores(); desenharDock(); ligarEntrada(); }

    /* ---------- entrar e sair ---------- */
    function ligarEntrada() {
        let b = document.getElementById('btnEditor');
        if (!b) {
            const tema = document.getElementById('btnTema');
            if (!tema || !tema.parentElement) return;
            b = Object.assign(document.createElement('button'), { id: 'btnEditor', type: 'button' });
            b.className = 'w-full flex items-center gap-3 px-3 py-2.5 text-sm font-medium';
            tema.parentElement.append(b);
            b.addEventListener('click', () => (ME.logado ? sair() : abrirLogin()));
        }
        b.innerHTML = ME.logado ? `<i class="fa-solid fa-user-pen w-5 text-center" aria-hidden="true"></i><span>Editor: ${esc(ME.nome)} (sair)</span>`
            : '<i class="fa-solid fa-user-pen w-5 text-center" aria-hidden="true"></i><span>Área do editor</span>';
    }
    function abrirLogin() {
        let d = document.getElementById('dlgEditor');
        if (!d) {
            d = document.createElement('dialog');
            d.id = 'dlgEditor'; d.className = 'w-[92vw] max-w-sm p-0';
            d.innerHTML = `<form method="dialog" class="postit-login" id="formEditor" autocomplete="on">
                <p class="postit-selo">Área do editor</p><h2>Entrar</h2>
                <p class="ficha-nota">Só para quem tem conta de editor. A conta é criada pela administração do site.</p>
                <label for="edEmail">E-mail</label><input id="edEmail" type="email" required autocomplete="username" maxlength="254">
                <label for="edSenha">Senha</label><input id="edSenha" type="password" required autocomplete="current-password" maxlength="200">
                <p id="edErro" class="postit-erro" role="alert"></p>
                <p class="postit-acoes"><button type="submit" id="edEnviar" class="bg-brand-600">Entrar</button><button type="button" id="edFechar">Cancelar</button></p></form>`;
            document.body.append(d);
            d.querySelector('#edFechar').addEventListener('click', () => d.close());
            d.querySelector('#formEditor').addEventListener('submit', async (ev) => {
                ev.preventDefault();
                const erro = d.querySelector('#edErro'), env = d.querySelector('#edEnviar');
                erro.textContent = ''; env.disabled = true;
                try {
                    await chamar('POST', '/editor/entrar', { email: d.querySelector('#edEmail').value, senha: d.querySelector('#edSenha').value });
                    d.querySelector('#edSenha').value = ''; d.close(); await carregar();
                } catch (e) { erro.textContent = e.message; } finally { env.disabled = false; }
            });
        }
        d.showModal(); d.querySelector('#edEmail').focus();
    }
    async function sair() {
        try { await chamar('POST', '/editor/sair'); } catch (_) { /* a sessão expira de qualquer jeito */ }
        criando = false; aberta = null; document.body.classList.remove('postit-escolhendo'); await carregar();
    }

    /* ---------- criar e editar ---------- */
    function formulario(n, alvoSel, rotulo) {
        $$('.postit-form').forEach((f) => f.remove());
        const f = Object.assign(document.createElement('div'), { className: 'postit postit-form' });
        f.setAttribute('role', 'dialog'); f.setAttribute('aria-label', n ? 'Editar post-it' : 'Novo post-it');
        f.innerHTML = `<p class="postit-cab"><b>${n ? 'Editar post-it' : 'Novo post-it'}</b></p>
            <p class="postit-trecho">${rotulo ? 'Sobre: ' + esc(rotulo) : 'Sem trecho da página.'}</p>
            <label class="sr-only" for="pitTexto">Texto</label>
            <textarea id="pitTexto" rows="6" maxlength="2000" placeholder="Escreva a sua nota. Parágrafo novo = linha em branco. **negrito**, *itálico*, [texto](https://link)."></textarea>
            <label class="postit-pub"><input type="checkbox" id="pitPublico"> Publicar para todos os visitantes <span>(desmarcado = rascunho, só você vê)</span></label>
            <p id="pitErro" class="postit-erro" role="alert"></p>
            <p class="postit-acoes"><button type="button" data-pit-salvar>Salvar</button><button type="button" data-pit-cancelar>Cancelar</button></p>`;
        document.body.append(f);
        f.style.position = 'fixed'; f.style.right = '1rem'; f.style.top = '5.5rem'; f.style.left = 'auto';
        const t = f.querySelector('#pitTexto'); t.value = n ? n.texto : ''; f.querySelector('#pitPublico').checked = n ? n.publico : false; t.focus();
        f.querySelector('[data-pit-cancelar]').addEventListener('click', () => f.remove());
        f.querySelector('[data-pit-salvar]').addEventListener('click', async () => {
            const erro = f.querySelector('#pitErro');
            const corpo = { pagina: PAGINA, ancora: n ? n.ancora : alvoSel, rotulo: n ? n.rotulo : rotulo, texto: t.value, publico: f.querySelector('#pitPublico').checked };
            try {
                const salvo = n ? await chamar('PUT', '/postits/' + n.id, corpo) : await chamar('POST', '/postits', corpo);
                f.remove(); aberta = salvo.id; await carregar();
            } catch (e) { erro.textContent = e.message; }
        });
    }

    function alternarCriar(on) {
        criando = on ?? !criando;
        document.body.classList.toggle('postit-escolhendo', criando);
        if (!criando && destaque) { destaque.classList.remove('postit-alvo'); destaque = null; }
        desenharDock();
        let aviso = document.getElementById('postitAviso');
        if (criando && !aviso) {
            aviso = Object.assign(document.createElement('p'), { id: 'postitAviso', className: 'postit-aviso' });
            aviso.textContent = 'Clique no trecho da página em que quer colar o post-it. Esc cancela.';
            document.body.append(aviso);
        }
        if (!criando && aviso) aviso.remove();
    }

    document.addEventListener('mouseover', (ev) => {
        if (!criando) return;
        const el = ev.target.nodeType === 1 && !dentroDaUI(ev.target) ? escolher(ev.target) : null;
        if (destaque && destaque !== el) destaque.classList.remove('postit-alvo');
        destaque = el; if (el) el.classList.add('postit-alvo');
    });
    document.addEventListener('click', (ev) => {
        const t = ev.target;
        if (criando && t.nodeType === 1 && !dentroDaUI(t)) {
            const el = escolher(t);
            if (el) {
                ev.preventDefault(); ev.stopPropagation();
                const rotulo = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80);
                const sel = seletorDe(el);
                alternarCriar(false); formulario(null, sel, rotulo);
                return;
            }
        }
        const marca = t.closest && t.closest('.postit-marca');
        if (marca) { aberta = aberta === Number(marca.dataset.id) ? null : Number(marca.dataset.id); desenharMarcadores(); return; }
        const dock = t.closest && t.closest('[data-dock]');
        if (dock) {
            if (dock.dataset.dock === 'novo') alternarCriar();
            else if (dock.dataset.dock === 'sair') sair();
            else if (dock.dataset.dock === 'lista') listar();
            return;
        }
        if (t.closest && t.closest('[data-postit-fechar]')) { aberta = null; desenharMarcadores(); return; }
        const ed = t.closest && t.closest('[data-postit-editar]');
        if (ed) { const n = NOTAS.find((x) => x.id === Number(ed.dataset.postitEditar)); aberta = null; desenharMarcadores(); if (n) formulario(n, n.ancora, n.rotulo); return; }
        const ap = t.closest && t.closest('[data-postit-apagar]');
        if (ap && confirm('Apagar este post-it? Não dá para desfazer.')) {
            chamar('DELETE', '/postits/' + Number(ap.dataset.postitApagar)).then(() => { aberta = null; return carregar(); }).catch((e) => alert(e.message));
        }
    }, true);
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { if (criando) alternarCriar(false); if (aberta != null) { aberta = null; desenharMarcadores(); } $$('.postit-form').forEach((f) => f.remove()); const l = document.getElementById('postitLista'); if (l) l.remove(); } });

    function listar() {
        let l = document.getElementById('postitLista');
        if (l) { l.remove(); return; }
        const meus = NOTAS.filter((n) => n.minha);
        l = Object.assign(document.createElement('div'), { id: 'postitLista', className: 'postit postit-lista' });
        l.innerHTML = `<p class="postit-cab"><b>Meus post-its nesta página</b></p>${meus.length ? `<ul>${meus.map((n) => `<li><button type="button" data-lista-abrir="${n.id}">
            <span class="postit-rasc">${n.publico ? 'publicado' : 'rascunho'}${achar(n.ancora) ? '' : ' · sem trecho'}</span>${esc(n.texto.slice(0, 90))}${n.texto.length > 90 ? '…' : ''}</button></li>`).join('')}</ul>` : '<p class="ficha-nota">Você ainda não colou nenhum post-it aqui.</p>'}
            <p class="postit-acoes"><button type="button" data-lista-fechar>Fechar</button></p>`;
        document.body.append(l);
        l.addEventListener('click', (ev) => {
            if (ev.target.closest('[data-lista-fechar]')) { l.remove(); return; }
            const b = ev.target.closest('[data-lista-abrir]');
            if (!b) return;
            const n = NOTAS.find((x) => x.id === Number(b.dataset.listaAbrir)); aberta = n.id; l.remove(); desenharMarcadores();
            const alvo = achar(n.ancora); if (alvo) alvo.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
    }

    /* ---------- a página muda depois de carregar (dados chegando, redimensionar): reposiciona ---------- */
    let tm = null;
    const agendar = () => { clearTimeout(tm); tm = setTimeout(posicionar, 120); };
    window.addEventListener('resize', agendar);
    if ('ResizeObserver' in window) new ResizeObserver(agendar).observe(document.body);
    const principal = document.querySelector('main');
    if (principal) new MutationObserver(agendar).observe(principal, { childList: true, subtree: true });
    window.addEventListener('hashchange', () => setTimeout(posicionar, 400));

    carregar();
})();
