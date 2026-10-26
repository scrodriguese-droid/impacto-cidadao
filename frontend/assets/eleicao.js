/* Componente "Como este cargo é eleito?"
 *
 * Três níveis: resumo em uma frase → simulador com exemplo prático (os números são editáveis) → detalhamento
 * das regras (passo a passo, fórmulas e tabela comparativa) num card expansível.
 * Uso: comoEleito(elemento, 'presidente')   (cargo inicial; o leitor troca pelo seletor)
 * Depende de comum.js (esc). Regras gerais da legislação eleitoral; o TSE publica as regras de cada eleição.
 */
(function () {
    const CARGOS = {
        presidente: { rotulo: 'Presidente', sistema: 'majoritario', turnos: true },
        governador: { rotulo: 'Governador', sistema: 'majoritario', turnos: true },
        prefeito: { rotulo: 'Prefeito', sistema: 'majoritario', turnos: 'cidade' },
        senador: { rotulo: 'Senador', sistema: 'majoritario', turnos: false },
        federal: { rotulo: 'Deputado federal', sistema: 'proporcional' },
        estadual: { rotulo: 'Deputado estadual ou distrital', sistema: 'proporcional' },
        vereador: { rotulo: 'Vereador', sistema: 'proporcional' },
    };
    const RESUMO = {
        majoritario: 'Vence quem tem mais votos. Para Presidente, Governador e Prefeito de cidade grande, é preciso passar de 50% dos votos válidos, senão os dois primeiros disputam um 2º turno.',
        proporcional: 'Você vota numa pessoa ou no partido, mas o voto vai primeiro para o partido: o total de votos do partido define quantas cadeiras ele terá, e os mais votados dele ocupam as vagas.',
    };

    const css = document.createElement('style');
    css.textContent = `
    .ce { border: 2px solid var(--fg); padding: 1rem 1.1rem 1.2rem; }
    .ce-cab { font: 800 .7rem/1 var(--fonte-texto); letter-spacing: .1em; text-transform: uppercase; color: var(--vermelho, #A92823); }
    .ce h3 { font: 400 1.9rem/1 var(--fonte-titulo); text-transform: uppercase; margin: .3rem 0 .6rem; }
    .ce-cargos { display: flex; flex-wrap: wrap; gap: .3rem; margin: .6rem 0 1rem; }
    .ce-cargos button, .ce-btn { padding: .4rem .7rem; border: 1px solid var(--fg) !important; background: transparent !important; color: var(--fg) !important; font: 800 .68rem/1 var(--fonte-texto); letter-spacing: .06em; text-transform: uppercase; cursor: pointer; }
    .ce-cargos button[aria-checked="true"] { background: var(--fg) !important; color: var(--papel) !important; }
    .ce-sistema { font-weight: 800; }
    .ce-passo { margin: 1rem 0 .4rem; font: 800 .7rem/1 var(--fonte-texto); letter-spacing: .1em; text-transform: uppercase; }
    .ce table { width: 100%; border-collapse: collapse; font-size: .82rem; }
    .ce th, .ce td { border: 1px solid var(--line); padding: .35rem .5rem; text-align: left; vertical-align: top; }
    .ce td input { width: 6.5rem; padding: .2rem .3rem; background: transparent; border: 1px solid var(--fg); font: inherit; }
    .ce .ce-num { text-align: right; font-variant-numeric: tabular-nums; }
    .ce .ce-venc { font-weight: 800; }
    .ce details { margin-top: 1rem; border-top: 2px solid var(--fg); padding-top: .6rem; }
    .ce summary { cursor: pointer; font: 800 .75rem/1.2 var(--fonte-texto); letter-spacing: .08em; text-transform: uppercase; }
    .ce ol.ce-etapas { margin: .6rem 0 0; padding-left: 1.2rem; display: grid; gap: .7rem; font-size: .86rem; line-height: 1.45; }
    .ce code { font-family: ui-monospace, Consolas, monospace; background: rgba(0,0,0,.07); padding: 0 .25rem; }
    .ce .ce-nota { font-size: .72rem; opacity: .8; margin-top: .6rem; }
    `;
    document.head.append(css);

    const nf = (n) => Number(n).toLocaleString('pt-BR');
    const pct = (v) => v.toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + '%';

    // ---------- majoritário ----------
    function simMajoritario(cargo, st) {
        const c = CARGOS[cargo];
        const nomes = ['Candidato A', 'Candidato B', 'Candidato C'];
        const votos = st.maj;
        const total = votos.reduce((a, b) => a + b, 0);
        const ordem = votos.map((v, i) => ({ i, v })).sort((x, y) => y.v - x.v);
        const turnos = c.turnos === true || (c.turnos === 'cidade' && st.grande);
        let resultado;
        if (!total) resultado = 'Digite os votos para ver o resultado.';
        else if (!turnos) resultado = `<b class="ce-venc">${nomes[ordem[0].i]} vence no turno único</b>: tem a maior votação (${pct(100 * ordem[0].v / total)}), mesmo sem passar de 50%.`;
        else if (ordem[0].v * 2 > total) resultado = `<b class="ce-venc">${nomes[ordem[0].i]} vence no 1º turno</b>: passou de 50% dos votos válidos (${pct(100 * ordem[0].v / total)}).`;
        else resultado = `<b class="ce-venc">Vai para o 2º turno</b>: ninguém passou de 50% (o primeiro tem ${pct(100 * ordem[0].v / total)}). Disputam ${nomes[ordem[0].i]} e ${nomes[ordem[1].i]}.`;
        const linhas = votos.map((v, i) => `<tr><td>${nomes[i]}</td><td><input type="number" min="0" inputmode="numeric" data-maj="${i}" value="${v}" aria-label="Votos de ${nomes[i]}"></td><td class="ce-num">${total ? pct(100 * v / total) : '—'}</td></tr>`).join('');
        return `<table><thead><tr><th>Candidato</th><th>Votos válidos</th><th>% dos válidos</th></tr></thead><tbody>${linhas}</tbody></table>
            ${c.turnos === 'cidade' ? `<label class="block mt-2 text-sm"><input type="checkbox" data-grande ${st.grande ? 'checked' : ''}> A cidade tem mais de 200 mil eleitores</label>` : ''}
            <p class="mt-2 text-sm" aria-live="polite">${resultado}</p>
            <p class="ce-nota">Exemplo para você mexer: os números são inventados, só para mostrar a regra. Votos brancos e nulos não entram na conta.</p>`;
    }

    // ---------- proporcional ----------
    // Cálculo do exemplo: quociente eleitoral, quociente partidário e sobras pela regra da maior média.
    function calcularProporcional(cadeiras, partidos) {
        const total = partidos.reduce((a, p) => a + p.v, 0);
        const qeBruto = cadeiras ? total / cadeiras : 0;
        const qe = Math.max(1, qeBruto - Math.floor(qeBruto) > 0.5 ? Math.ceil(qeBruto) : Math.floor(qeBruto)); // fração igual ou menor que 0,5 é desprezada
        const linhas = partidos.map((p) => ({ ...p, qp: Math.floor(p.v / qe), sobras: 0 }));
        let restam = cadeiras - linhas.reduce((a, p) => a + p.qp, 0);
        while (restam > 0 && total) {
            let melhor = null;
            for (const p of linhas) {
                const media = p.v / (p.qp + p.sobras + 1);
                if (!melhor || media > melhor.media) melhor = { p, media };
            }
            melhor.p.sobras += 1; restam -= 1;
        }
        return { total, qe, linhas: linhas.map((p) => ({ ...p, cadeiras: p.qp + p.sobras })) };
    }
    function simProporcional(st) {
        const r = calcularProporcional(st.cadeiras, st.partidos);
        const linhas = r.linhas.map((p, i) => `<tr><td>${esc(p.nome)}</td><td><input type="number" min="0" inputmode="numeric" data-prop="${i}" value="${p.v}" aria-label="Votos do ${esc(p.nome)}"></td>
            <td class="ce-num">${p.qp}</td><td class="ce-num">${p.sobras}</td><td class="ce-num ce-venc">${p.cadeiras}</td></tr>`).join('');
        return `<label class="block text-sm mb-2">Cadeiras em disputa <input type="number" min="1" max="70" inputmode="numeric" data-cadeiras value="${st.cadeiras}" style="width:4.5rem;border:1px solid var(--fg);background:transparent;padding:.2rem .3rem"></label>
            <table><thead><tr><th>Partido</th><th>Votos válidos</th><th>Direto (QP)</th><th>Sobras</th><th>Cadeiras</th></tr></thead><tbody>${linhas}</tbody></table>
            <p class="mt-2 text-sm" aria-live="polite">Total de votos válidos: <b>${nf(r.total)}</b> · Quociente eleitoral (o “preço” de cada cadeira): <b>${nf(r.qe)}</b> votos.</p>
            <p class="ce-nota">Exemplo para você mexer: os números e os partidos são inventados. O exemplo mostra o cálculo das cadeiras de cada partido; quem ocupa essas cadeiras são os candidatos mais votados dele, e as regras mudam um pouco entre as eleições (veja o detalhamento).</p>`;
    }

    // ---------- detalhamento ----------
    const DETALHE_MAJ = `<ol class="ce-etapas">
        <li><b>Presidente, Governador e Prefeito de cidade com mais de 200 mil eleitores:</b> vence no 1º turno quem tiver mais de 50% dos votos válidos (votos em candidatos; brancos e nulos não contam). Se ninguém passar, os dois mais votados disputam o 2º turno, e ganha quem tiver mais votos.</li>
        <li><b>Prefeito de cidade menor:</b> turno único. Ganha quem tiver mais votos, mesmo com menos de 50%.</li>
        <li><b>Senador:</b> turno único, vence quem tem mais votos. O mandato é de 8 anos e a renovação é alternada: numa eleição disputa-se 1 vaga por estado, na outra 2 vagas (e cada eleitor vota duas vezes).</li></ol>`;
    const DETALHE_PROP = `<ol class="ce-etapas">
        <li><b>Quociente eleitoral (QE): o “preço” de cada cadeira.</b><br><code>QE = votos válidos ÷ número de cadeiras</code><br>Votos brancos e nulos são jogados fora.</li>
        <li><b>Quociente partidário (QP): quantas vagas o partido ganhou direto.</b><br><code>QP = votos do partido ÷ QE</code> (só o número inteiro)<br>Ex.: se der 3,8, o partido garante 3 cadeiras de saída.</li>
        <li><b>Quem assume?</b> O partido preenche suas vagas com os candidatos mais votados dele. A lei exige que o candidato tenha um mínimo de votos individuais (hoje, 20% do quociente eleitoral).</li>
        <li><b>Sobras:</b> as vagas que restaram são distribuídas pela regra da maior média.<br><code>média = votos do partido ÷ (cadeiras já obtidas + 1)</code><br>Ganha a vaga o partido com a maior média, e o cálculo se repete até acabarem as vagas. As regras dessa fase mudaram em 2021 (exigência de 80% do QE para o partido) e foram alteradas por decisão do STF em 2024: confira a regra da eleição em que você está interessado no site do TSE.</li></ol>
        <p class="ce-nota">Coligações não valem nas eleições proporcionais desde 2020: cada partido disputa sozinho.</p>`;
    const TABELA = `<table class="mt-3"><thead><tr><th></th><th>Majoritário</th><th>Proporcional</th></tr></thead><tbody>
        <tr><td>Cargos</td><td>Presidente, governador, prefeito, senador</td><td>Deputado federal, deputado estadual ou distrital, vereador</td></tr>
        <tr><td>Quem ganha</td><td>O candidato mais votado</td><td>O partido ganha cadeiras pelo total de votos; ocupam os mais votados dele</td></tr>
        <tr><td>Maioria exigida</td><td>Simples; absoluta (mais de 50%) com 2º turno para presidente, governador e prefeito de cidade grande</td><td>Não há maioria: vale a proporção de votos de cada partido</td></tr>
        <tr><td>Vantagem</td><td>Regra simples e fácil de entender</td><td>Dá voz a minorias e a diferentes ideias</td></tr>
        <tr><td>Risco</td><td>Quem fica em 2º lugar não tem representação</td><td>Seu voto pode ajudar a eleger um colega de partido de quem você discorda</td></tr></tbody></table>`;

    // ---------- montagem ----------
    window.comoEleito = function (el, inicial) {
        if (!el) return;
        const st = { cargo: CARGOS[inicial] ? inicial : 'presidente', maj: [48000, 32000, 20000], grande: true, cadeiras: 10,
            partidos: [{ nome: 'Partido X', v: 40000 }, { nome: 'Partido Y', v: 31000 }, { nome: 'Partido Z', v: 17000 }, { nome: 'Partido W', v: 12000 }] };
        const desenhar = () => {
            const c = CARGOS[st.cargo], maj = c.sistema === 'majoritario';
            el.innerHTML = `<div class="ce" role="group" aria-label="Como este cargo é eleito">
                <p class="ce-cab">Como este cargo é eleito?</p>
                <div class="ce-cargos" role="radiogroup" aria-label="Cargo">${Object.entries(CARGOS).map(([k, x]) => `<button type="button" role="radio" aria-checked="${k === st.cargo}" data-cargo="${k}">${esc(x.rotulo)}</button>`).join('')}</div>
                <h3>${esc(c.rotulo)}: sistema ${maj ? 'majoritário' : 'proporcional'}</h3>
                <p class="ce-sistema">${maj ? '“Ganha quem tem mais votos”' : '“O voto vai para o partido”'}</p>
                <p class="mt-1">${esc(RESUMO[c.sistema])}</p>
                <p class="ce-passo">Exemplo prático</p>
                ${maj ? simMajoritario(st.cargo, st) : simProporcional(st)}
                <details data-det ${st.aberto ? 'open' : ''}><summary>Ver a matemática: ${maj ? 'turnos e maioria' : 'quociente eleitoral, quociente partidário e sobras'}</summary>
                    ${maj ? DETALHE_MAJ : DETALHE_PROP}${TABELA}</details></div>`;
        };
        // os campos numéricos reagem a cada digitação, sem refazer a tela (para o cursor não sair do campo)
        el.addEventListener('click', (ev) => {
            const b = ev.target.closest('[data-cargo]');
            if (b) { st.cargo = b.dataset.cargo; desenhar(); }
        });
        el.addEventListener('toggle', (ev) => { if (ev.target.matches('[data-det]')) st.aberto = ev.target.open; }, true);
        el.addEventListener('change', (ev) => {
            const t = ev.target;
            if (t.matches('[data-grande]')) st.grande = t.checked;
            else if (t.matches('[data-maj]')) st.maj[Number(t.dataset.maj)] = Math.max(0, Number(t.value) || 0);
            else if (t.matches('[data-prop]')) st.partidos[Number(t.dataset.prop)].v = Math.max(0, Number(t.value) || 0);
            else if (t.matches('[data-cadeiras]')) st.cadeiras = Math.min(70, Math.max(1, Math.round(Number(t.value)) || 1));
            else return;
            desenhar();
        });
        desenhar();
    };
})();
