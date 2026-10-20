/* Termos do glossário no texto corrido: ficam em negrito e, ao passar o cursor (ou focar com o teclado, ou tocar), mostram a
   explicação "descomplicando". Os termos e as formas de escrevê-los vêm de assets/glossario.json (campo `aliases`).
   Regras para não poluir a página:
     - só a primeira ocorrência de cada termo em cada parágrafo/item;
     - nada em títulos, links, botões, campos, tabelas de cabeçalho, gráficos, código nem em blocos com data-sem-gl;
     - a página do próprio glossário não é marcada. */
(function () {
    if (window.__PAGINA === 'glossario' || window.__glossarioAuto) return;
    window.__glossarioAuto = true;

    const IGNORAR_TAGS = new Set(['A', 'BUTTON', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'SUMMARY', 'LABEL', 'SELECT', 'OPTION', 'TEXTAREA', 'INPUT',
        'SVG', 'SCRIPT', 'STYLE', 'NOSCRIPT', 'CODE', 'PRE', 'TH', 'THEAD', 'NAV', 'HEADER', 'FOOTER', 'CANVAS', 'DIALOG', 'ASIDE', 'TITLE']);
    const IGNORAR_SEL = '[data-sem-gl], .gl-termo, .gl-dica, .font-mono, .norma-tag, .marca-nome, [contenteditable], [class*="postit"], [class*="coluna"]';
    const BLOCO = 'p, li, dd, td, figcaption, blockquote, div, section, article';

    let indice = null;   // { regex, porTexto: Map(texto minúsculo -> sigla), verbetes: { sigla: {...} } }

    const escRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const ehSigla = (s) => s === s.toUpperCase() && /[A-ZÀ-Ý]/.test(s);   // IPCA, PEC, LRF: só vale escrita igual (não "pec" nem "loa" solto)

    function montarIndice(dados) {
        const porTexto = new Map(), verbetes = {}, frases = [];
        for (const secao of Object.values(dados)) {
            for (const [sigla, v] of Object.entries(secao)) {
                if (!Array.isArray(v.aliases) || !v.aliases.length) continue;
                verbetes[sigla] = v;
                for (const a of v.aliases) { porTexto.set(a.toLowerCase(), { sigla, exato: ehSigla(a), escrita: a }); frases.push(a); }
            }
        }
        frases.sort((a, b) => b.length - a.length);   // "dívida bruta do governo geral" antes de "dívida bruta"
        const regex = new RegExp(`(?<![\\p{L}\\p{N}_-])(${frases.map(escRegex).join('|')})(?![\\p{L}\\p{N}_-])`, 'giu');
        return { regex, porTexto, verbetes };
    }

    // o elemento ou algum ancestral dele está numa zona que não se marca?
    const elementoIgnorado = (el) => {
        for (let e = el; e; e = e.parentElement) {
            if (IGNORAR_TAGS.has(e.tagName.toUpperCase()) || e.matches(IGNORAR_SEL)) return true;
            if (e.tagName === 'BODY') return false;
        }
        return true;
    };
    const ignorado = (no) => elementoIgnorado(no.parentElement);

    function marcar(raiz) {
        if (!indice || !raiz || raiz.nodeType !== 1 || elementoIgnorado(raiz)) return;
        const andador = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT, {
            acceptNode: (t) => (t.nodeValue.trim().length > 2 && !ignorado(t) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
        });
        const nos = [];
        for (let t = andador.nextNode(); t; t = andador.nextNode()) nos.push(t);
        const usados = new WeakMap();   // bloco -> Set de siglas já marcadas nele
        for (const no of nos) {
            const bloco = no.parentElement.closest(BLOCO) || no.parentElement;
            let set = usados.get(bloco);
            if (!set) { set = new Set([...bloco.querySelectorAll('.gl-termo')].map((e) => e.dataset.gl)); usados.set(bloco, set); }
            const texto = no.nodeValue;
            indice.regex.lastIndex = 0;
            let ultimo = 0, achou = false;
            const frag = document.createDocumentFragment();
            for (let m = indice.regex.exec(texto); m; m = indice.regex.exec(texto)) {
                const info = indice.porTexto.get(m[1].toLowerCase());
                if (!info || (info.exato && m[1] !== info.escrita) || set.has(info.sigla)) continue;
                set.add(info.sigla); achou = true;
                frag.append(texto.slice(ultimo, m.index));
                const el = document.createElement('strong');
                el.className = 'gl-termo'; el.dataset.gl = info.sigla; el.tabIndex = 0; el.textContent = m[1];
                frag.append(el);
                ultimo = m.index + m[1].length;
            }
            if (!achou) continue;
            frag.append(texto.slice(ultimo));
            no.replaceWith(frag);
        }
    }

    /* ---------- a dica (uma só, reaproveitada) ---------- */
    let dica = null, alvo = null, fechar = 0;
    function criarDica() {
        dica = document.createElement('div');
        dica.id = 'glDica'; dica.className = 'gl-dica'; dica.setAttribute('role', 'tooltip'); dica.hidden = true;
        document.body.appendChild(dica);
        dica.addEventListener('mouseenter', () => clearTimeout(fechar));
        dica.addEventListener('mouseleave', () => agendarFechar());
    }
    function abrir(el) {
        const v = indice && indice.verbetes[el.dataset.gl];
        if (!v) return;
        if (!dica) criarDica();
        clearTimeout(fechar);
        if (alvo && alvo !== el) alvo.removeAttribute('aria-describedby');
        alvo = el; el.setAttribute('aria-describedby', 'glDica');
        const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
        dica.innerHTML = `<p class="gl-dica-rot">Descomplicando</p><p class="gl-dica-tit">${esc(v.nome_cidadao)}</p>
            <p>${esc(v.explicacao_simples)}</p><p class="gl-dica-tec">${esc(v.termo_tecnico)}</p>
            <a href="glossario.html#${encodeURIComponent(el.dataset.gl)}">Ver no glossário</a>`;
        dica.hidden = false;
        posicionar(el);
    }
    // a dica acompanha o termo: abaixo dele se couber, senão acima; nunca fora da tela
    function posicionar(el) {
        const r = el.getBoundingClientRect(), w = Math.min(dica.offsetWidth, innerWidth - 16), h = dica.offsetHeight;
        const esquerda = Math.max(8, Math.min(r.left, innerWidth - w - 8));
        const abaixo = r.bottom + 8 + h <= innerHeight || r.top - 8 - h < 0;
        dica.style.left = `${esquerda}px`;
        dica.style.top = `${abaixo ? r.bottom + 8 : r.top - 8 - h}px`;
    }
    let quadro = 0;
    function acompanhar() {   // rolar ou redimensionar: reposiciona; se o termo saiu da tela, fecha
        if (!alvo || !dica || dica.hidden || quadro) return;
        quadro = requestAnimationFrame(() => {
            quadro = 0;
            if (!alvo) return;
            const r = alvo.getBoundingClientRect();
            if (r.bottom < 0 || r.top > innerHeight) fecharAgora(); else posicionar(alvo);
        });
    }
    function fecharAgora() { if (dica) dica.hidden = true; if (alvo) { alvo.removeAttribute('aria-describedby'); alvo = null; } }
    function agendarFechar() { clearTimeout(fechar); fechar = setTimeout(fecharAgora, 180); }

    document.addEventListener('mouseover', (ev) => { const t = ev.target.closest && ev.target.closest('.gl-termo'); if (t) abrir(t); });
    document.addEventListener('mouseout', (ev) => { if (ev.target.closest && ev.target.closest('.gl-termo')) agendarFechar(); });
    document.addEventListener('focusin', (ev) => { const t = ev.target.closest && ev.target.closest('.gl-termo'); if (t) abrir(t); });
    document.addEventListener('focusout', (ev) => { if (ev.target.closest && ev.target.closest('.gl-termo')) agendarFechar(); });
    document.addEventListener('click', (ev) => {   // toque no celular: abre e fecha
        const t = ev.target.closest && ev.target.closest('.gl-termo');
        if (t) { if (alvo === t && dica && !dica.hidden) fecharAgora(); else abrir(t); }
        else if (!ev.target.closest || !ev.target.closest('.gl-dica')) fecharAgora();
    });
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') fecharAgora(); });
    window.addEventListener('scroll', acompanhar, { passive: true });
    window.addEventListener('resize', acompanhar);

    /* ---------- partida: carrega o glossário, marca a página e acompanha o que o JavaScript desenhar depois ---------- */
    (async function iniciar() {
        try {
            const resp = await fetch('assets/glossario.json');
            if (!resp.ok) return;
            indice = montarIndice(await resp.json());
        } catch (_) { return; }
        const principal = () => document.querySelector('main') || document.body;
        marcar(principal());
        let pendentes = new Set(), timer = 0;
        new MutationObserver((registros) => {
            for (const r of registros) for (const n of r.addedNodes) if (n.nodeType === 1 && !n.matches('.gl-termo, .gl-dica') && !n.closest('.gl-dica')) pendentes.add(n);
            if (!pendentes.size || timer) return;
            timer = setTimeout(() => {
                const lote = [...pendentes]; pendentes = new Set(); timer = 0;
                for (const n of lote) if (n.isConnected) marcar(n);
            }, 120);
        }).observe(document.body, { childList: true, subtree: true });
    })();
})();
