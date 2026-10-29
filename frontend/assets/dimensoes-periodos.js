/* Radiografia por período (blocos clicáveis) da análise das dimensões. Depende de dimensoes.js (DIM, COR_FASE, rotMesDim). */
const nf = (v, d = 1) => Number(v).toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d });
const sinal = (v, d = 1, un = 'p.p.') => `${v > 0 ? '+' : v < 0 ? '−' : ''}${nf(Math.abs(v), d)}${un.startsWith('%') ? '' : ' '}${un}`;
const primarioTxt = (v) => (v == null ? '—' : v > 0 ? `déficit de ${nf(v, 2)}% do PIB` : `superávit de ${nf(-v, 2)}% do PIB`);

function dimPeriodoCard(p) {
    const c = p.ciclo, e = p.estrutura, f = p.fiscal, m = p.monetaria;
    const fase = c.recessao_tecnica ? 'Recessão técnica no período' : (c.dominante_rotulo || 'Sem dados');
    const linha = (rot, cor, corpo) => `<div class="dim-lin"><p class="dim-rot" style="--cor:${cor}">${esc(rot)}</p><div>${corpo}</div></div>`;
    const normas = (lista) => (lista.length ? `<ul class="mt-2 space-y-1.5 text-sm">${lista.map((n) => `<li><b>${n.url ? `<a class="underline" target="_blank" rel="noopener" href="${esc(urlSegura(n.url))}">${esc(n.norma)}</a>` : esc(n.norma)}:</b> ${esc(n.texto)}</li>`).join('')}</ul>` : '');
    const sitPrim = { piorou: 'O resultado primário piorou', melhorou: 'O resultado primário melhorou', estavel: 'O resultado primário ficou parecido' }[f.situacao] || '';
    return `<article class="dim-per-card" aria-live="polite">
        <p class="text-xs font-bold uppercase tracking-wide">${esc(rotMesDim(p.inicio))} a ${esc(rotMesDim(p.fim))}</p>
        <h4 class="text-xl font-extrabold">${esc(p.rotulo)}: ${esc(p.titulo)}</h4>
        ${linha('Ciclo econômico', '#0f766e', `<p class="font-bold">${esc(fase)}</p>
            <ul class="text-sm mt-1 space-y-1 list-disc pl-5">
              ${c.pib_acum4_fim != null ? `<li>PIB em 4 trimestres até ${esc(rotMesDim(c.pib_acum4_ref))}: <b>${nf(c.pib_acum4_fim)}%</b>; cada trimestre variou entre ${nf(c.pib_trimestre_menor)}% e ${nf(c.pib_trimestre_maior)}%</li>` : ''}
              ${c.desemprego_inicio != null && c.desemprego_fim != null ? `<li>Desemprego: <b>${nf(c.desemprego_inicio)}%</b> no início → <b>${nf(c.desemprego_fim)}%</b> no fim (entre ${nf(c.desemprego_minimo)}% e ${nf(c.desemprego_maximo)}% no período)</li>` : ''}
              ${Object.keys(c.contagem).length ? `<li>Trimestres lidos pelas regras do projeto: ${Object.entries(c.contagem).map(([k, v]) => `${esc(k.toLowerCase())} (${v})`).join(', ')}</li>` : ''}</ul>`)}
        ${linha('Tendência estrutural', '#7a7a72', `<ul class="text-sm space-y-1 list-disc pl-5">
              ${e.pib_per_capita_real_cresc_ano != null ? `<li>PIB per capita real, ${e.ano_ini} a ${e.ano_fim}: <b>${sinal(e.pib_per_capita_real_cresc_ano, 2, '% ao ano')}</b></li>` : ''}
              ${e.industria_inicio != null && e.industria_fim != null ? `<li>Indústria de transformação: <b>${nf(e.industria_inicio)}%</b> do PIB em ${e.ano_ini} → <b>${nf(e.industria_fim)}%</b> em ${e.ano_fim}</li>` : ''}
              <li class="text-slate-500">Dados anuais do Banco Mundial; o diagnóstico de longo prazo está na seção acima.</li></ul>`)}
        ${linha('Política fiscal (gastos)', '#A92823', `<p class="font-bold">${esc(sitPrim)}${f.variacao_pp != null ? ` (${sinal(f.variacao_pp, 1)} do PIB em 12 meses)` : ''}</p>
            <ul class="text-sm mt-1 space-y-1 list-disc pl-5"><li>Resultado primário: ${esc(primarioTxt(f.primario_inicio))} → ${esc(primarioTxt(f.primario_fim))}</li>
              ${f.divida_inicio != null && f.divida_fim != null ? `<li>Dívida bruta: <b>${nf(f.divida_inicio)}%</b> → <b>${nf(f.divida_fim)}%</b> do PIB (${sinal(f.variacao_divida_pp)})</li>` : ''}</ul>
            ${normas(f.normas)}<p class="ficha-nota mt-2">${esc(f.nota)}</p>`)}
        ${linha('Política monetária (juros)', '#b45309', `<p class="font-bold">${esc(m.postura_rotulo)}</p>
            <ul class="text-sm mt-1 space-y-1 list-disc pl-5"><li>Selic: <b>${nf(m.selic_inicio, 2)}%</b> no início → <b>${nf(m.selic_fim, 2)}%</b> no fim (entre ${nf(m.selic_minima, 2)}% e ${nf(m.selic_maxima, 2)}% no período)</li>
              ${m.ipca12_fim != null ? `<li>Inflação em 12 meses no fim: <b>${nf(m.ipca12_fim, 2)}%</b> (máxima no período: ${nf(m.ipca12_max, 2)}%)</li>` : ''}
              ${m.juro_real_fim != null ? `<li>Juro real no fim (Selic menos inflação): <b>${sinal(m.juro_real_fim, 1)}</b></li>` : ''}</ul>`)}
        ${linha('Política tributária', '#111111', normas(p.tributaria.normas) || '<p class="text-sm">Sem normas selecionadas.</p>')}
    </article>`;
}

function dimPeriodos(pr) {
    const idx = (m) => Number(m.slice(0, 4)) * 12 + Number(m.slice(5, 7));
    const meses = (p) => idx(p.fim) - idx(p.inicio) + 1;
    const blocos = pr.periodos.map((p) => `<button type="button" class="dim-per" data-per="${esc(p.id)}" aria-pressed="false" style="flex:${meses(p)} 1 0">
        <span class="dim-per-ano">${esc(p.rotulo)}</span><span class="dim-per-tit">${esc(p.titulo)}</span>
        <span class="dim-tl-mini">${p.ciclo.trimestres.map((q) => `<i style="background:${COR_FASE[q.fase]}" title="${esc(rotMesDim(q.mes))}"></i>`).join('')}</span></button>`).join('');
    return `<div class="dim-bloco mt-10"><h3 class="trib-h">Linha do tempo: o que mudou em cada fase</h3>
        <p class="text-sm" style="max-width:75ch">Clique num período para ver, lado a lado, o ciclo, a tendência estrutural e as três políticas (fiscal, monetária e tributária). Os quadrados coloridos mostram as fases do ciclo, um por trimestre, com as mesmas cores da seção acima.</p>
        <div class="dim-pers mt-4" role="group" aria-label="Períodos">${blocos}</div>
        <div id="dimPeriodoCard" class="mt-4"><p class="text-sm text-slate-500">Escolha um período acima.</p></div>
        <p class="ficha-nota mt-4" style="max-width:80ch">${esc(pr.aviso)}</p></div>`;
}

document.addEventListener('click', (ev) => {
    const b = ev.target.closest('.dim-per');
    if (!b || !DIM.dados || !DIM.dados.periodos) return;
    const p = DIM.dados.periodos.periodos.find((x) => x.id === b.dataset.per);
    if (!p) return;
    document.querySelectorAll('.dim-per').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    document.getElementById('dimPeriodoCard').innerHTML = dimPeriodoCard(p);
});
