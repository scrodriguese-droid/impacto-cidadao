/* ====================================================================== *
 *  ImpactoCidadão — utilidades e layout compartilhados pelas páginas.
 *  Texto vindo do servidor deve SEMPRE passar por esc() antes de ir ao HTML.
 * ====================================================================== */
const API = location.protocol.startsWith('http') ? '' : 'http://localhost:8000';

/* ---------- utilidades ---------- */
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const urlSegura = (u) => (/^https?:\/\//i.test(u || '') ? u : '#');
const brl = (n) => Number(n || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
const fmtData = (iso) => { if (!iso) return ''; const d = new Date(iso.slice(0, 10) + 'T00:00:00'); return isNaN(d) ? '' : d.toLocaleDateString('pt-BR'); };
const fmtDataHora = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? '' : `${d.toLocaleDateString('pt-BR')} às ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
};
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const guardar = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* navegação privada */ } };
const ler = (k, padrao) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : padrao; } catch (_) { return padrao; } };

async function api(caminho, opcoes) {
    const resp = await fetch(API + caminho, opcoes);
    if (!resp.ok) {
        let detalhe = '';
        try { const j = await resp.json(); detalhe = typeof j.detail === 'string' ? j.detail : ''; } catch (_) { /* sem corpo */ }
        throw new Error(detalhe || `Erro ${resp.status}`);
    }
    return resp.json();
}
const mensagemErro = (e) => `<div class="p-4 rounded-xl border border-rose-300 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/40 text-rose-800 dark:text-rose-200 text-sm"><i class="fa-solid fa-circle-exclamation mr-2" aria-hidden="true"></i>${esc(e.message || e)} <span class="block text-xs mt-1 opacity-80">Verifique se o servidor está no ar (uvicorn) e tente novamente.</span></div>`;

/* ---------- preferências do usuário (perfil, cidade…), iguais em todas as páginas ---------- */
const lerPrefs = () => ler('ic_prefs', {});
function salvarPrefsParciais(parcial) { guardar('ic_prefs', { ...lerPrefs(), ...parcial }); }

/* ---------- tema claro/escuro (aplicado antes de desenhar, para não piscar) ---------- */
(function iniciarTema() {
    const salvo = ler('ic_tema', null);
    const escuro = salvo ? salvo === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.classList.toggle('dark', escuro);
})();

/* ---------- cabeçalho e rodapé ---------- */
const NAVEGACAO = [
    { id: 'inicio', paginas: ['inicio'], href: 'index.html', icone: 'fa-house', rotulo: 'Início' },
    { id: 'politicas', paginas: ['politicas'], href: 'politicas.html', icone: 'fa-landmark', rotulo: 'Leis e projetos' },
    { id: 'planos', paginas: ['planos'], href: 'planos.html', icone: 'fa-flag', rotulo: 'Eleições: planos de governo' },
    { id: 'orcamento', paginas: ['economia', 'fiscal', 'orcamento', 'tributos', 'simuladores', 'macro', 'financiamento', 'ciclo'], href: 'economia.html', icone: 'fa-coins', rotulo: 'Orçamento e contas públicas' },
    { id: 'bancadas', paginas: ['bancadas'], href: 'bancadas.html', icone: 'fa-people-group', rotulo: 'Bancadas' },
];
const NAV_GLOSSARIO = { id: 'glossario', paginas: ['glossario'], href: 'glossario.html', icone: 'fa-book', rotulo: 'Glossário' };
// Subnavegação: cada área do menu tem telas focadas, escolhidas por abas no topo da página.
const SUBNAVEGACAO = {
    orcamento: [
        { id: 'economia', href: 'economia.html', rotulo: 'Entrada e saída' },
        { id: 'orcamento', href: 'orcamento.html', rotulo: 'Leis do orçamento' },
        { id: 'fiscal', href: 'economia.html?aba=fiscal', rotulo: 'Saúde fiscal e dívida' },
    ],
};
// Telas de aprofundamento da mesma área: ficam numa linha secundária e acendem a aba a que pertencem.
const APROFUNDAR = {
    orcamento: [
        { id: 'tributos', pai: 'economia', href: 'tributos.html', rotulo: 'Inventário de tributos' },
        { id: 'simuladores', pai: 'economia', href: 'simuladores.html', rotulo: 'Simuladores de IR e MEI' },
        { id: 'macro', pai: 'fiscal', href: 'macro.html', rotulo: 'Indicadores' },
        { id: 'financiamento', pai: 'fiscal', href: 'financiamento.html', rotulo: 'Dívida e títulos' },
        { id: 'ciclo', pai: 'fiscal', href: 'ciclo.html', rotulo: 'Ciclo econômico' },
    ],
};

function renderLayout(paginaAtual) {
    if (!document.querySelector('link[rel="icon"]')) {
        const ic = document.createElement('link');
        ic.rel = 'icon'; ic.type = 'image/png'; ic.href = 'assets/favicon.png';
        document.head.appendChild(ic);
    }
    const cab = $('cabecalho');
    if (cab) {
        // Marca só tipográfica (sem imagem), no formato do cartaz: nome empilhado, régua e linha técnica.
        const marca = `<span class="marca-nome block min-w-0">
                    <span class="marca-titulo block">Impacto<br>Cidadão</span>
                    <span class="marca-regua block" aria-hidden="true"></span>
                    <span class="marca-linha block">Civic tech // Transparência acessível <span class="marca-ano">✦ 2026</span></span>
                </span>`;
        const marcaMovel = `<span class="marca-movel">Impacto<span>Cidadão</span></span>`;
        const atual = (n) => n.paginas.includes(paginaAtual);
        const linkMenu = (n) => `<a href="${n.href}" ${atual(n) ? 'aria-current="page"' : ''}
                    class="flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium hover:bg-slate-100 dark:hover:bg-slate-800 ${atual(n) ? 'bg-slate-100 dark:bg-slate-800 text-brand-700 dark:text-brand-400' : 'text-slate-700 dark:text-slate-300'}">
                    <i class="fa-solid ${n.icone} w-5 text-center ${atual(n) ? '' : 'text-slate-400'}" aria-hidden="true"></i><span>${n.rotulo}</span></a>`;
        const itens = NAVEGACAO.map(linkMenu).join('');
        cab.outerHTML = `<header class="lg:hidden sticky top-0 z-40 bg-white/90 dark:bg-slate-900/90 backdrop-blur-md border-b border-slate-200 dark:border-slate-800 shadow-sm">
    <div class="flex items-center justify-between h-16 px-4">
        <a href="index.html" class="flex items-center gap-3 min-w-0" aria-label="ImpactoCidadão — início">${marcaMovel}</a>
        <div class="flex items-center gap-1">
            <button id="btnTemaMovel" type="button" class="p-2 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-xl" aria-label="Alternar tema claro/escuro">
                <i class="fa-solid fa-moon dark:hidden text-lg" aria-hidden="true"></i><i class="fa-solid fa-sun hidden dark:block text-amber-400 text-lg" aria-hidden="true"></i></button>
            <button id="btnMenu" type="button" class="p-2 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-xl" aria-label="Abrir menu" aria-expanded="false" aria-controls="menuLateral">
                <i class="fa-solid fa-bars text-lg" aria-hidden="true"></i></button>
        </div>
    </div>
</header>
<div id="menuFundo" class="hidden lg:hidden fixed inset-0 z-40 bg-black/50" aria-hidden="true"></div>
<aside id="menuLateral" class="fixed inset-y-0 left-0 z-50 w-64 -translate-x-full lg:translate-x-0 transition-transform duration-200 flex flex-col bg-white dark:bg-slate-900 border-r border-slate-200 dark:border-slate-800 shadow-lg lg:shadow-none">
    <a href="index.html" class="marca-bloco hidden lg:block px-5 pt-5 pb-4 border-b border-slate-200 dark:border-slate-800 shrink-0" aria-label="ImpactoCidadão — início">${marca}</a>
    <div class="lg:hidden flex items-center justify-between px-4 h-16 border-b border-slate-200 dark:border-slate-800 shrink-0">
        <span class="font-bold">Menu</span>
        <button id="btnFecharMenu" type="button" class="p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-xl" aria-label="Fechar menu"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>
    </div>
    <nav class="flex-1 overflow-y-auto p-3 space-y-1" aria-label="Seções">${itens}</nav>
    <div class="p-3 border-t border-slate-200 dark:border-slate-800 shrink-0 space-y-1">
        ${linkMenu(NAV_GLOSSARIO)}
        <button id="btnTema" type="button" class="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="Alternar tema claro/escuro">
            <i class="fa-solid fa-moon dark:hidden w-5 text-center" aria-hidden="true"></i><i class="fa-solid fa-sun hidden dark:block text-amber-400 w-5 text-center" aria-hidden="true"></i>
            <span>Tema claro/escuro</span></button>
    </div>
</aside>`;
        document.body.classList.add('lg:pl-64');
        // em telas grandes não há cabeçalho fixo: as barras “grudadas” das páginas sobem para o topo
        if (!document.getElementById('estiloMenuLateral')) {
            const st = document.createElement('style');
            st.id = 'estiloMenuLateral';
            st.textContent = '@media (min-width:1024px){ .top-16.sticky, .sticky.top-16 { top: 0 !important; } }';
            document.head.appendChild(st);
        }
        const menu = $('menuLateral'), fundo = $('menuFundo'), abrir = $('btnMenu');
        const alternarMenu = (aberto) => {
            menu.classList.toggle('-translate-x-full', !aberto);
            fundo.classList.toggle('hidden', !aberto);
            abrir.setAttribute('aria-expanded', String(aberto));
        };
        abrir.addEventListener('click', () => alternarMenu(true));
        $('btnFecharMenu').addEventListener('click', () => alternarMenu(false));
        fundo.addEventListener('click', () => alternarMenu(false));
        menu.querySelectorAll('nav a').forEach((a) => a.addEventListener('click', () => alternarMenu(false)));
        document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') alternarMenu(false); });
        $('btnTemaMovel').addEventListener('click', () => $('btnTema').click());
        const grupo = NAVEGACAO.find((n) => n.paginas.includes(paginaAtual));
        const abas = grupo && SUBNAVEGACAO[grupo.id];
        const principal = document.querySelector('main');
        if (abas && principal) {
            const extra = APROFUNDAR[grupo.id] || [];
            const aqui = extra.find((t) => t.id === paginaAtual);
            const aba = (t) => { const on = t.id === paginaAtual || (aqui && aqui.pai === t.id);
                return `<a href="${t.href}" ${t.id === paginaAtual ? 'aria-current="page"' : ''}
                    class="px-3.5 py-2 rounded-xl text-sm font-semibold ${on ? 'bg-white dark:bg-slate-700 shadow text-brand-700 dark:text-brand-300' : 'text-slate-600 dark:text-slate-300 hover:bg-white/60 dark:hover:bg-slate-700/60'}">${t.rotulo}</a>`; };
            const chips = extra.map((t) => `<a href="${t.href}" ${t.id === paginaAtual ? 'aria-current="page"' : ''}
                    class="px-2.5 py-1 rounded-full border text-xs font-medium ${t.id === paginaAtual ? 'border-brand-600 text-brand-700 dark:text-brand-300 bg-white dark:bg-slate-800' : 'border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-white dark:hover:bg-slate-800'}">${t.rotulo}</a>`).join('');
            principal.insertAdjacentHTML('afterbegin', `<nav aria-label="Telas desta área" class="mb-5">
                <div class="flex flex-wrap gap-1 p-1 rounded-2xl bg-slate-200/70 dark:bg-slate-800 w-fit max-w-full">${abas.map(aba).join('')}</div>
                ${chips ? `<div class="mt-2 flex flex-wrap items-center gap-1.5"><span class="text-xs text-slate-500 mr-1">Aprofundar:</span>${chips}</div>` : ''}</nav>`);
        }
    }
    const rod = $('rodape');
    if (rod) {
        rod.outerHTML = `<footer class="bg-slate-900 text-slate-400 py-10 border-t border-slate-800 text-xs">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-8 mb-8">
            <div>
                <h4 class="text-white font-semibold mb-3 uppercase tracking-wider">Fontes oficiais</h4>
                <ul class="space-y-2">
                    <li><a class="hover:text-white" target="_blank" rel="noopener" href="https://divulgacandcontas.tse.jus.br/">TSE — DivulgaCandContas (planos de governo)</a></li>
                    <li><a class="hover:text-white" target="_blank" rel="noopener" href="https://dadosabertos.camara.leg.br/">Câmara dos Deputados — Dados Abertos</a></li>
                    <li><a class="hover:text-white" target="_blank" rel="noopener" href="https://www12.senado.leg.br/dados-abertos">Senado Federal — Dados Abertos</a></li>
                    <li><a class="hover:text-white" target="_blank" rel="noopener" href="https://www.in.gov.br/">Diário Oficial da União</a></li>
                    <li><a class="hover:text-white" target="_blank" rel="noopener" href="https://servicodados.ibge.gov.br/api/docs">IBGE — municípios e população</a></li>
                    <li><a class="hover:text-white" target="_blank" rel="noopener" href="https://dadosabertos.bcb.gov.br/">Banco Central — Dados Abertos (SGS)</a></li>
                    <li><a class="hover:text-white" target="_blank" rel="noopener" href="https://www.tesourotransparente.gov.br/">Tesouro Transparente (dívida pública)</a></li>
                    <li><a class="hover:text-white" target="_blank" rel="noopener" href="https://siconfi.tesouro.gov.br/">Tesouro Nacional — SICONFI (contas dos municípios)</a></li>
                </ul>
            </div>
            <div>
                <h4 class="text-white font-semibold mb-3 uppercase tracking-wider">Compromisso editorial</h4>
                <p class="leading-relaxed">Descrevemos, não avaliamos. Todos os candidatos passam pelo mesmo roteiro, em ordem alfabética, sem ranking. Cada proposta cita o texto original, e citações que não existem no PDF são descartadas automaticamente. Resumos gerados por IA podem conter erros: consulte o documento oficial. Onde ainda não há fonte de dados conectada, dizemos isso claramente.</p>
            </div>
            <div>
                <h4 class="text-white font-semibold mb-3 uppercase tracking-wider">Sobre as votações</h4>
                <p class="leading-relaxed">A consulta de opinião vale apenas para leis e medidas provisórias. É informal, anônima e <b>não é pesquisa eleitoral</b> nem representa a população. Não coletamos CPF, e-mail ou endereço IP para votar.</p>
            </div>
        </div>
        <p class="pt-6 border-t border-slate-800 text-slate-500">Projeto independente e sem vínculo com governo, partidos ou candidaturas.</p>
    </div>
</footer>`;
    }
    const btn = $('btnTema');
    if (btn) {
        btn.addEventListener('click', () => {
            const escuro = document.documentElement.classList.toggle('dark');
            guardar('ic_tema', escuro ? 'dark' : 'light');
        });
    }
}

/* ---------- barra de contexto compacta: “A mostrar dados para: [perfil] em [cidade]” ---------- */
async function montarBarraContexto(idDestino, aoMudar) {
    const destino = $(idDestino);
    if (!destino) return null;
    const p0 = lerPrefs();
    const estado = { perfil: p0.perfil && p0.perfil !== 'todos' ? p0.perfil : '', municipio: p0.municipio || null };
    let perfis = [];
    try { perfis = await api('/api/perfis'); } catch (_) { /* sem perfis, a barra mostra só a cidade */ }
    if (estado.perfil && !perfis.some((x) => x.id === estado.perfil)) estado.perfil = '';
    destino.innerHTML = `<div class="flex flex-wrap items-center gap-x-2 gap-y-2 rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-4 py-3 shadow-sm text-sm">
        <i class="fa-solid fa-location-dot text-rose-500" aria-hidden="true"></i>
        <span class="font-medium">A mostrar dados para:</span>
        <label class="relative inline-block"><span class="sr-only">Seu perfil</span>
            <select id="ctxPerfil" class="appearance-none font-bold bg-brand-50 dark:bg-brand-950 text-brand-800 dark:text-brand-300 border border-brand-200 dark:border-brand-800 rounded-full pl-3.5 pr-8 py-1.5 focus:ring-2 focus:ring-brand-500">
                <option value="">Todos os perfis</option>${perfis.map((x) => `<option value="${esc(x.id)}">${esc(x.nome)}</option>`).join('')}</select>
            <i class="fa-solid fa-chevron-down absolute right-3 top-2.5 text-[10px] text-brand-700 dark:text-brand-300 pointer-events-none" aria-hidden="true"></i></label>
        <span class="font-medium">em</span>
        <div class="relative">
            <button id="ctxCidade" type="button" class="inline-flex items-center gap-1.5 font-bold bg-teal-50 dark:bg-teal-950/50 text-teal-800 dark:text-teal-300 border border-teal-200 dark:border-teal-800 rounded-full px-3.5 py-1.5 hover:opacity-90" aria-expanded="false" aria-controls="ctxPop">
                <i class="fa-solid fa-city text-xs" aria-hidden="true"></i><span id="ctxCidadeRotulo">Escolher cidade</span></button>
            <div id="ctxPop" class="hidden absolute left-0 top-full mt-2 w-72 z-40 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-xl p-2">
                <label for="ctxBusca" class="sr-only">Buscar município</label>
                <input id="ctxBusca" type="text" autocomplete="off" placeholder="Digite o nome da cidade…" class="w-full px-3 py-2 rounded-lg bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-sm focus:ring-2 focus:ring-brand-500 focus:outline-none">
                <ul id="ctxLista" class="mt-1 max-h-60 overflow-auto text-sm" role="listbox"></ul>
                <button id="ctxSem" type="button" class="hidden w-full mt-1 text-left px-3 py-2 text-xs text-slate-500 hover:underline">Remover cidade</button></div></div></div>`;
    const sel = $('ctxPerfil'), pop = $('ctxPop');
    const desenhar = () => {
        sel.value = estado.perfil;
        $('ctxCidadeRotulo').textContent = estado.municipio ? `${estado.municipio.nome} – ${estado.municipio.uf}` : 'Escolher cidade';
        $('ctxSem').classList.toggle('hidden', !estado.municipio);
    };
    const mudou = () => { salvarPrefsParciais({ perfil: estado.perfil, municipio: estado.municipio }); desenhar(); if (aoMudar) aoMudar({ ...estado }); };
    const alternar = (abrir) => {
        const mostrar = abrir ?? pop.classList.contains('hidden');
        pop.classList.toggle('hidden', !mostrar);
        $('ctxCidade').setAttribute('aria-expanded', String(mostrar));
        if (mostrar) { $('ctxBusca').value = ''; $('ctxLista').innerHTML = ''; setTimeout(() => $('ctxBusca').focus(), 30); }
    };
    sel.addEventListener('change', () => { estado.perfil = sel.value; mudou(); });
    $('ctxCidade').addEventListener('click', () => alternar());
    document.addEventListener('click', (ev) => { if (!ev.target.closest('#ctxPop') && !ev.target.closest('#ctxCidade')) alternar(false); });
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') alternar(false); });
    $('ctxBusca').addEventListener('input', debounce(async () => {
        const q = $('ctxBusca').value.trim();
        if (q.length < 2) { $('ctxLista').innerHTML = ''; return; }
        try {
            const achados = await api('/api/municipios?q=' + encodeURIComponent(q));
            $('ctxLista').innerHTML = achados.length
                ? achados.map((m) => `<li><button type="button" role="option" data-cod="${esc(m.codigo)}" data-nome="${esc(m.nome)}" data-uf="${esc(m.uf)}" class="w-full text-left px-3 py-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700">${esc(m.nome)} <span class="text-slate-400">– ${esc(m.uf)}</span></button></li>`).join('')
                : '<li class="px-3 py-2 text-slate-500">Nenhum município encontrado.</li>';
        } catch (e) { $('ctxLista').innerHTML = `<li class="px-3 py-2 text-rose-600 text-xs">${esc(e.message)}</li>`; }
    }, 250));
    $('ctxLista').addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-cod]'); if (!b) return;
        estado.municipio = { codigo: Number(b.dataset.cod), nome: b.dataset.nome, uf: b.dataset.uf };
        alternar(false); mudou();
    });
    $('ctxSem').addEventListener('click', () => { estado.municipio = null; alternar(false); mudou(); });
    desenhar();
    return {
        get perfil() { return estado.perfil; }, get municipio() { return estado.municipio; },
        perfis,
        definirPerfil(id) { estado.perfil = id; mudou(); },
    };
}


/* Gráficos no estilo do site: Inter e linhas de grade em tinta suave. */
if (window.Chart) {
    Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.borderColor = 'rgba(17,17,17,.25)';
}
