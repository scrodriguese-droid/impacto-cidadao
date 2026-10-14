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
    { id: 'planos', href: 'index.html#candidatos', icone: 'fa-flag', rotulo: 'Planos de governo' },
    { id: 'politicas', href: 'politicas.html', icone: 'fa-landmark', rotulo: 'Políticas públicas' },
    { id: 'simuladores', href: 'index.html#simuladores', icone: 'fa-calculator', rotulo: 'Simuladores' },
];

function renderLayout(paginaAtual) {
    const cab = $('cabecalho');
    if (cab) {
        cab.outerHTML = `<header class="sticky top-0 z-40 bg-white/90 dark:bg-slate-900/90 backdrop-blur-md border-b border-slate-200 dark:border-slate-800 shadow-sm">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="flex items-center justify-between h-16">
            <a href="index.html" class="flex items-center space-x-3" aria-label="ImpactoCidadão — início">
                <div class="bg-gradient-to-tr from-brand-700 to-teal-500 text-white p-2.5 rounded-xl shadow-md">
                    <i class="fa-solid fa-scale-balanced text-xl" aria-hidden="true"></i>
                </div>
                <div>
                    <span class="font-extrabold text-xl tracking-tight bg-gradient-to-r from-brand-700 via-teal-600 to-sky-600 bg-clip-text text-transparent">ImpactoCidadão</span>
                    <p class="text-xs text-slate-500 dark:text-slate-400 hidden sm:block">O que muda na sua vida, em linguagem simples</p>
                </div>
            </a>
            <nav class="flex items-center gap-1 font-medium text-sm" aria-label="Seções">
                ${NAVEGACAO.map((n) => `<a href="${n.href}" ${n.id === paginaAtual ? 'aria-current="page"' : ''}
                    class="px-2.5 sm:px-3 py-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 ${n.id === paginaAtual ? 'bg-slate-100 dark:bg-slate-800 text-brand-700 dark:text-brand-400' : ''}">
                    <i class="fa-solid ${n.icone} sm:mr-1.5 text-slate-400" aria-hidden="true"></i><span class="hidden sm:inline">${n.rotulo}</span></a>`).join('')}
                <button id="btnTema" type="button" class="p-2 ml-1 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-xl" aria-label="Alternar tema claro/escuro">
                    <i class="fa-solid fa-moon dark:hidden text-lg" aria-hidden="true"></i>
                    <i class="fa-solid fa-sun hidden dark:block text-amber-400 text-lg" aria-hidden="true"></i>
                </button>
            </nav>
        </div>
    </div>
</header>`;
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
