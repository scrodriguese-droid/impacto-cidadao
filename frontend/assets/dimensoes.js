/* Análise das dimensões da economia (aba “Saúde fiscal”): o ciclo, de curto e médio prazo, e a tendência estrutural, de longo prazo.
 * Os dados vêm de /api/macro/dimensoes; as regras que transformam números em “fase” ou “compatível” estão no backend (analise_dimensoes.py). */
const DIM = { dados: null, grafico: null };
const COR_FASE = { expansao: '#0f766e', pico: '#A92823', desaceleracao: '#b45309', recessao: '#111111', null: '#9a9a92' };
const MESES_DIM = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
const rotMesDim = (m) => `${MESES_DIM[Number(m.slice(5, 7)) - 1]}/${m.slice(0, 4)}`;

function dimCiclo(c) {
    const at = c.atual;
    const fase = at && c.fases.find((f) => f.id === at.fase);
    const passos = c.fases.map((f, i) => `<li class="dim-passo ${at && at.fase === f.id ? 'dim-atual' : ''}" style="--cor:${COR_FASE[f.id]}">
        <span class="dim-num">${i + 1}</span><b>${esc(f.rotulo)}</b><span class="dim-txt">${esc(f.texto)}</span>
        ${at && at.fase === f.id ? '<span class="dim-selo">Mais compatível hoje</span>' : ''}</li>`).join('');
    let onde = '<p class="text-sm">Sem dados suficientes para situar o ciclo agora.</p>';
    if (at) {
        const ordem = ['recessao', 'pico', 'desaceleracao', 'expansao'];
        const teste = (t) => `<li class="flex gap-2"><span aria-hidden="true">${t.ok === true ? '✔' : t.ok === false ? '✘' : '?'}</span><span><span class="sr-only">${t.ok === true ? 'Cumprido: ' : t.ok === false ? 'Não cumprido: ' : 'Sem dado: '}</span>${esc(t.texto)} <span class="text-slate-500">(${esc(t.valor)})</span></span></li>`;
        const quadro = ordem.map((id) => { const f = c.fases.find((x) => x.id === id);
            return `<div><p class="text-xs font-bold uppercase tracking-wide" style="color:${COR_FASE[id]}">${esc(f.rotulo)}${at.fase === id ? ' · escolhida' : ''}</p><ul class="text-sm mt-1 space-y-1">${at.testes[id].map(teste).join('')}</ul></div>`; }).join('');
        onde = `<p class="text-lg font-extrabold">${fase ? `Os dados de hoje são mais compatíveis com: ${esc(fase.rotulo.toLowerCase())}.` : 'Os sinais estão mistos: nenhuma fase fecha todas as regras.'}</p>
            <p class="text-sm mt-1" style="max-width:75ch">Último PIB trimestral: ${esc(rotMesDim(at.trimestre))} · desemprego: ${esc(rotMesDim(at.desemprego_mes))} · Selic e inflação: ${esc(rotMesDim(at.mes))}.</p>
            <details class="sanfona mt-3"><summary>Por que esta leitura? Veja os testes de cada fase</summary><div class="grid grid-cols-1 md:grid-cols-2 gap-4 mt-3">${quadro}</div></details>`;
    }
    const tl = c.historico.map((h) => `<span class="dim-q" style="background:${COR_FASE[h.fase]}" title="${esc(rotMesDim(h.mes))}: ${esc(h.fase ? c.fases.find((f) => f.id === h.fase).rotulo : 'sinais mistos')}"></span>`).join('');
    const leg = [...c.fases.map((f) => [f.rotulo, COR_FASE[f.id]]), ['Sinais mistos', COR_FASE.null]].map(([r, cor]) => `<span class="inline-flex items-center gap-1.5"><span class="inline-block w-3 h-3" style="background:${cor}"></span>${esc(r)}</span>`).join('');
    return `<div class="dim-bloco"><h3 class="trib-h">Ciclo econômico: o sobe e desce de curto e médio prazo</h3>
        <p class="text-sm" style="max-width:75ch">A economia alterna entre expansão e contração, em fases que se repetem. Elas duram de alguns trimestres a poucos anos.</p>
        <ol class="dim-passos mt-4">${passos}</ol>
        <div class="dim-onde mt-4">${onde}</div>
        <p class="text-xs font-bold uppercase tracking-wide mt-5">Como o ciclo foi lido desde 2016 (um quadrado por trimestre)</p>
        <div class="dim-tl mt-2" role="img" aria-label="Linha do tempo das fases do ciclo por trimestre">${tl}</div>
        <p class="text-xs mt-2 flex flex-wrap gap-x-4 gap-y-1">${leg}</p>
        <p class="ficha-nota mt-3" style="max-width:80ch">${esc(c.criterio)}</p></div>`;
}

function dimEstrutura(e) {
    const cards = e.dimensoes.map((d) => `<article class="dim-card ${d.compativel ? 'dim-sim' : ''}">
        <p class="dim-status">${d.compativel ? 'Dados compatíveis' : 'Dados não indicam'}</p>
        <h4 class="font-extrabold text-lg mt-1">${esc(d.rotulo)}</h4>
        <p class="text-sm mt-2">${esc(d.texto)}</p>
        <ul class="mt-3 text-sm space-y-1">${d.evidencias.map((x) => `<li><b>${esc(x.nome)}:</b> ${esc(x.valor)}${x.nota ? ` <span class="text-slate-500">(${esc(x.nota)})</span>` : ''}</li>`).join('')}</ul>
        <p class="ficha-nota mt-3"><b>Regra usada:</b> ${esc(d.regra)}</p></article>`).join('');
    return `<div class="dim-bloco mt-10"><h3 class="trib-h">Tendência estrutural: o rumo de longo prazo</h3>
        <p class="text-sm" style="max-width:75ch">Crescimento é o aumento quantitativo do PIB. Desenvolvimento é o aumento qualitativo: produtividade, renda per capita, indústria complexa, saúde e educação. O que importa aqui é por onde o país caminha em 10, 20 ou 30 anos, e não o trimestre.</p>
        <div class="grafico mt-4" style="position:relative;height:300px"><canvas id="graficoEstrutura" role="img" aria-label="Renda per capita de Brasil, Argentina, Chile e Coreia do Sul em relação à dos Estados Unidos, desde 1990"></canvas></div>
        <p class="text-xs text-slate-500 mt-1">${esc(e.grafico.titulo)}, em ${esc(e.grafico.unidade)}.</p>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mt-5">${cards}</div>
        <ul class="ficha-nota mt-4 space-y-1 list-disc pl-5" style="max-width:80ch">${e.notas.map((n) => `<li>${esc(n)}</li>`).join('')}</ul></div>`;
}

function dimGrafico(e) {
    if (typeof Chart === 'undefined' || !document.getElementById('graficoEstrutura')) return;
    const escuro = document.documentElement.classList.contains('dark');
    const txt = escuro ? '#cbd5e1' : '#33332F', grade = escuro ? 'rgba(148,163,184,.2)' : 'rgba(0,0,0,.12)';
    const cores = { 'Brasil': escuro ? '#EDEBE6' : '#000000', 'Argentina': '#6baed6', 'Chile': '#b45309', 'Coreia do Sul': '#0f766e' };
    if (DIM.grafico) DIM.grafico.destroy();
    DIM.grafico = new Chart(document.getElementById('graficoEstrutura'), {
        type: 'line',
        data: { datasets: Object.entries(e.grafico.series).map(([nome, pts]) => ({ label: nome, data: pts.map((p) => ({ x: p.ano, y: p.valor })), parsing: false,
            borderColor: cores[nome] || '#888', backgroundColor: cores[nome] || '#888', borderWidth: nome === 'Brasil' ? 4 : 2, pointRadius: 0, pointHitRadius: 6 })) },
        options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'nearest', axis: 'x', intersect: false },
            scales: { x: { type: 'linear', ticks: { color: txt, stepSize: 5, callback: (v) => String(v) }, grid: { color: grade } },
                y: { ticks: { color: txt, callback: (v) => `${v}%` }, grid: { color: grade }, title: { display: true, text: '% da renda dos EUA', color: txt } } },
            plugins: { legend: { labels: { color: txt } }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%` } } } },
    });
}

async function montarDimensoes(idDestino) {
    const alvo = document.getElementById(idDestino);
    if (!alvo) return;
    alvo.innerHTML = '<p class="text-sm text-slate-500" aria-live="polite"><i class="fa-solid fa-spinner fa-spin mr-1.5" aria-hidden="true"></i>Consultando o IBGE, o Banco Central e o Banco Mundial…</p>';
    try {
        DIM.dados = await api('/api/macro/dimensoes');
        const d = DIM.dados;
        alvo.innerHTML = (d.ciclo ? dimCiclo(d.ciclo) : '') + (d.estrutura ? dimEstrutura(d.estrutura) : '')
            + (d.erros.length ? `<ul class="ficha-nota mt-4">${d.erros.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '')
            + `<p class="text-[11px] text-slate-400 mt-6">Fontes: ${[d.ciclo && d.ciclo.fonte, d.estrutura && d.estrutura.fonte].filter(Boolean).map((f) => `<a class="underline" target="_blank" rel="noopener" href="${esc(urlSegura(f.url))}">${esc(f.nome)}</a>`).join(' · ')}. Consultado em ${esc(fmtDataHora(d.consultado_em))}.</p>`;
        if (d.estrutura) dimGrafico(d.estrutura);
    } catch (e) { alvo.innerHTML = mensagemErro(e); }
}
