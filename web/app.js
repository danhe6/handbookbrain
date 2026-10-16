/* HandbookBrain web UI (no build step). Views: overview, demo, explore, review, configure. Data: hb/api.py. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const view = $('#view');
  const S = { ov: null, trust: null, demoId: null, askResult: null, asking: false, reviewTab: 'all' };

  // ------------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const toDate = (ts) => { if (!ts) return null; const d = new Date(String(ts).replace(' ', 'T')); return isNaN(d) ? null : d; };
  const day = (ts) => { const d = toDate(ts); return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }) : (ts ? String(ts).slice(0, 10) : ''); };
  const pct = (x) => (x == null ? '–' : Math.round(x * 100) + '%');
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}, ...opts });
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) { /* */ } throw new Error(m); }
    return r.json();
  }
  const loading = (msg = 'Loading…') => `<div class="thinking"><div class="spinner"></div>${esc(msg)}</div>`;
  const convName = (s) => ({ zoom: 'Zoom', zoom_captions: 'Zoom', teams: 'Teams meeting', meet: 'Google Meet', slack: 'Slack', teams_chat: 'Teams chat',
    google_chat: 'Google Chat', gchat: 'Google Chat', whatsapp: 'WhatsApp', discord: 'Discord', email: 'Email', jira: 'Jira', zendesk: 'Zendesk', notes: 'Notes' }[s] || s || 'Conversation');
  const srcBadge = (src, kind, up) => !src ? '' : `<span class="badge ${kind === 'static' ? 'amber' : 'blue'}">${esc(src)} · ${kind === 'static' ? 'static' + (up ? ' · uploaded ' + esc(day(up)) : '') : 'live'}</span>`;
  const ownerBadge = (os, owners) => {
    const who = (owners || []).filter(Boolean).map((o) => String(o).split('|').pop()).join(', ');
    if (os === 'explicit') return `<span class="badge green">Owner · ${esc(who)}</span>`;
    if (os === 'inferred') return `<span class="badge amber" title="Only the file author is known">Owner inferred · ${esc(who)}</span>`;
    return '<span class="badge red">No owner</span>';
  };
  const ICON = {
    ask: '<path d="M4 5h16v10H8l-4 4z"/>', lineage: '<circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 12h5l5-6M12 12l5 6"/>',
    stale: '<path d="M12 7v5l3 2"/><circle cx="12" cy="12" r="8"/>', conflict: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17v.5"/>',
    decision: '<path d="M8 4h8v6l-4 3-4-3z"/><path d="M12 13v7"/>', gap: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  };
  const ico = (k) => `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[k]}</svg>`;

  function renderAnswer(text, numOf) {
    let html = esc(text).replace(/\[([^\[\]]+#[^\[\]]+)\]/g, (_, g) => g.split(/[,;]\s*/).filter((x) => x.includes('#'))
      .map((k) => { const n = numOf(k.trim()); return `<button class="cite" data-cite="${esc(k.trim())}" title="${esc(k.trim())}">${n}</button>`; }).join(''));
    html = html.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    return html.split(/\n{2,}/).map((b) => {
      const lines = b.split('\n');
      if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) return '<ul>' + lines.map((l) => `<li>${l.replace(/^\s*([-*]|\d+\.)\s+/, '')}</li>`).join('') + '</ul>';
      return `<p>${lines.join('<br>')}</p>`;
    }).join('');
  }

  // ------------------------------------------------------------------ header
  async function loadHeader() {
    try {
      const [ov, cs] = await Promise.all([api('/api/overview'), api('/api/companies')]);
      S.ov = ov;
      document.title = `HandbookBrain · ${ov.name}`;
      $('#company').innerHTML = cs.companies.map((c) => `<option value="${esc(c.id)}" ${c.id === cs.current ? 'selected' : ''}>${esc(c.name)}</option>`).join('');
      const n = Object.values(ov.review || {}).reduce((a, b) => a + b, 0);
      $('#nav-review').hidden = !n; $('#nav-review').textContent = n;
      paintSync(ov.sync);
    } catch (e) { $('#sync-text').textContent = 'API offline'; }
  }
  function paintSync(sync) {
    $('#sync-pill').classList.toggle('busy', !!(sync && sync.running));
    $('#sync-text').textContent = sync && sync.running ? 'Syncing…' : `${S.ov?.name || ''} · docs as of ${day(S.ov?.as_of)}`;
  }
  $('#company').addEventListener('change', async (e) => {
    const name = e.target.value;
    view.innerHTML = loading(`Switching to ${e.target.selectedOptions[0].text}…`);
    try { await api('/api/switch', { method: 'POST', body: JSON.stringify({ name }) }); } catch (err) { /* restarting */ }
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 500));
      try { const c = await api('/api/companies'); if (c.current === name) { location.reload(); return; } } catch (err) { /* */ }
    }
    view.innerHTML = `<div class="empty">The server did not come back. Run <code>python -m hb --company ${esc(name)} serve</code>.</div>`;
  });

  // ------------------------------------------------------------------ overview (product)
  async function vOverview() {
    view.innerHTML = loading();
    const [ov, trust, schema] = await Promise.all([api('/api/overview'), api('/api/trust').catch(() => ({})), api('/api/graph/schema').catch(() => ({ nodes: [], edges: [] }))]);
    S.ov = ov; S.trust = trust;
    const c = ov.counts || {};
    const sc = trust.source_conflicts;
    view.innerHTML = `
      <section class="hero">
        <span class="eyebrow">Company brain · Graph RAG on FalkorDB</span>
        <h1 class="display">Business decisions change every week. Know what each one affects, and who has to sign off.</h1>
        <p class="lede">HandbookBrain connects the decisions made in meetings and chat, the business and technical documents that encode them, and the systems, fields, metrics and dashboards they touch, in one versioned graph. Ask why something works the way it does and get the trail back to the meeting. When something changes, it finds every answer, document and dashboard affected, drafts the update and routes it to the owner for approval.</p>
        <div class="actions"><a class="btn primary" href="#/demo">See the demo</a><a class="btn" href="#/explore">Explore the graph</a></div>
      </section>

      <section class="section">
        <h3 class="label">The closed loop</h3>
        <div class="loop">
          ${[['01', 'Ingest', 'Business and technical docs (synced daily or uploaded), meetings and chat in their native export formats.'],
             ['02', 'Version + link', 'Docs become versioned sections, linked to the decisions behind them and to the systems, fields, metrics and dashboards they mention.'],
             ['03', 'Answer', 'An agent walks the graph with Cypher tools; every claim cites a section version; a second model reviews.'],
             ['04', 'Detect impact', 'A decision or a change reaches every answer, document, field and dashboard that relies on what changed.'],
             ['05', 'Draft + route', 'The agent drafts the doc update and sends it to the owner who must approve it. People approve, not edit.'],
             ['06', 'Refresh', 'Once merged, stale answers are re-asked against the new version and SUPERSEDE the old ones.']]
            .map(([n, t, d], i) => `<div class="step ${i === 3 ? 'on' : ''}"><span class="n">${n}</span><b>${t}</b><p>${d}</p></div>`).join('')}
        </div>
        <div class="loopback"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/></svg>Every sync goes around the loop again. Answers are never "done"; they are watched.</div>
      </section>

      <section class="section">
        <div style="display:flex;flex-direction:column;gap:6px"><h3 class="label">What is in the graph right now</h3>
          <h2 class="title">${esc(ov.name)}: ${(c.pages ?? 0).toLocaleString()} documents, ${(c.sections ?? 0).toLocaleString()} sections, ${(c.versions ?? 0).toLocaleString()} older versions kept, ${c.conversations ?? 0} conversations, ${c.decisions ?? 0} decisions.</h2></div>
        <div class="graphbox"><div id="g-schema"></div><span class="hint">Live data model of this graph, read from FalkorDB: from the meeting where something was decided, to the change, the section version, the document, the answer and the question · drag nodes</span></div>
        <div class="legend">${HBGraph.legend([...new Set(schema.nodes.map((n) => n.type))].filter((t) => HBGraph.TYPES[t]))}</div>
      </section>

      <section class="section">
        <h3 class="label">What it can do</h3>
        <div class="caps">
          ${[['ask', 'Ask, with receipts', 'Plain-language questions answered only from your documents. Each sentence cites a section version; an independent model checks the draft.'],
             ['lineage', 'Decision lineage', 'From the meeting where it was decided, to the change that wrote it down, to the answer that uses it: who, when, where, verbatim quote.'],
             ['stale', 'Answers that go stale', 'When a cited section changes, a judge reads the diff and decides if the answer is still right. Wording changes do not raise alarms.'],
             ['conflict', 'Sources that disagree', 'A PDF on the shared drive still says $10,000 while the live policy says $7,500? Flagged, the live source wins, the file owner is told.'],
             ['decision', 'Decided, drafted, approved', 'A decision made in a meeting that no document reflects after three weeks gets a drafted update, routed to the page owner for one-click approval.'],
             ['gap', 'Change impact', 'One changed rule, every document, Salesforce field, metric and dashboard that relies on it, and the people who own them.']]
            .map(([k, t, d]) => `<div class="cap"><span class="ico">${ico(k)}</span><b>${t}</b><p>${d}</p></div>`).join('')}
        </div>
      </section>

      ${trust.available ? `<section class="section">
        <h3 class="label">How well it works · ${esc(ov.name)} benchmark</h3>
        <div class="numbers">
          <div class="num"><b>${pct(trust.answer_correct_or_partial)}</b><span>answers correct or partially correct (${trust.questions} questions)</span></div>
          <div class="num"><b>${trust.staleness.tp}/${trust.staleness.tp + trust.staleness.fn}</b><span>stale answers caught, ${trust.staleness.fp} false alarm${trust.staleness.fp === 1 ? '' : 's'}</span></div>
          ${sc ? `<div class="num"><b>${esc(sc.found)}</b><span>live vs static conflicts found, ${esc(sc.false_alarms_on_consistent_sections)} false alarms</span></div>` : ''}
          <div class="num"><b>${esc(trust.conversations?.decision_to_change_recall || '–')}</b><span>meeting decisions linked to the change that implemented them</span></div>
        </div>
        <a class="small" href="#/configure">Method and all numbers</a>
      </section>` : ''}`;
    if (schema.nodes.length) HBGraph.mount($('#g-schema'), schema, { layout: 'layered', height: 380, labels: 'all', label: 'Graph data model' });
  }

  // ------------------------------------------------------------------ demo
  async function vDemo() {
    view.innerHTML = loading();
    const a = await api('/api/answers?limit=40');
    const sug = a.suggested || [];
    view.innerHTML = `
      <section class="hero" style="padding-top:0;gap:10px">
        <span class="eyebrow">Demo · ${esc(S.ov?.name || '')}</span>
        <h1 class="display" style="font-size:42px">Ask a question. See the decision trail behind the answer.</h1>
      </section>
      <div class="demo">
        <aside class="qlist">
          <form class="askbox" id="askf"><label class="sr" for="q">Question</label><input id="q" autocomplete="off" placeholder="Ask anything…"><button class="btn primary" type="submit">Ask</button></form>
          ${sug.length ? `<h3 class="label" style="margin-top:10px">Try</h3>${sug.map((q) => `<button class="qbtn" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}` : ''}
          <h3 class="label" style="margin-top:10px">Answered and watched (${a.answers.length})</h3>
          ${a.answers.map((x) => `<button class="qbtn" data-id="${esc(x.id)}" aria-current="${x.id === S.demoId}">${esc(x.question)}
            <span class="meta">${x.status === 'stale' ? '<span class="badge red">stale</span>' : '<span class="badge green">fresh</span>'}${x.checks ? `<span>re-checked ${plural(x.checks, 'time')}</span>` : ''}</span></button>`).join('') || '<div class="muted small">No answers yet.</div>'}
        </aside>
        <section id="story" class="col">${S.demoId ? loading() : `<div class="empty">Pick a question on the left, or ask your own. The answer comes with a timeline of the decisions and changes behind it, and the subgraph it was read from.</div>`}</section>
      </div>`;
    $('#askf').addEventListener('submit', (e) => { e.preventDefault(); ask($('#q').value); });
    view.querySelectorAll('[data-ask]').forEach((b) => b.addEventListener('click', () => { $('#q').value = b.dataset.ask; ask(b.dataset.ask); }));
    view.querySelectorAll('[data-id]').forEach((b) => b.addEventListener('click', () => openStory(b.dataset.id)));
    if (S.demoId) openStory(S.demoId);
  }
  async function ask(q) {
    q = (q || '').trim();
    if (!q || S.asking) return;
    S.asking = true;
    $('#story').innerHTML = `<div class="card">${loading('Searching the graph, checking every citation, and having a second model review the answer… (10–30 s)')}</div>`;
    try {
      const r = await api('/api/ask', { method: 'POST', body: JSON.stringify({ question: q }) });
      S.askResult = r;
      S.demoId = r.id;
      await openStory(r.id);
    } catch (e) { $('#story').innerHTML = `<div class="card warn">Could not answer: ${esc(e.message)}</div>`; } finally { S.asking = false; }
  }
  async function openStory(id) {
    S.demoId = id;
    view.querySelectorAll('.qbtn[data-id]').forEach((b) => b.setAttribute('aria-current', String(b.dataset.id === id)));
    const box = $('#story');
    box.innerHTML = `<div class="card">${loading('Walking the graph around this answer…')}</div>`;
    const st = await api('/api/story/' + encodeURIComponent(id));
    const a = st.answer;
    const nodes = st.graph.nodes;
    const citeNode = {};
    nodes.filter((n) => n.type === 'Section' && n.cite && n.hl).forEach((n) => { citeNode[n.cite] = n; });
    const order = [];
    const numOf = (k) => { if (!order.includes(k)) order.push(k); return order.indexOf(k) + 1; };
    const html = renderAnswer(a.text, numOf);
    const steps = S.askResult && S.askResult.id === id ? S.askResult.steps : null;
    const decisions = st.timeline.filter((e) => e.kind === 'decision');
    const last = decisions[decisions.length - 1];
    const headline = last ? `Decided ${day(last.ts)}${last.who ? ' by ' + esc(last.who) : ''} in ${esc(last.where || 'a meeting')}` : '';
    const pageOf = (n) => nodes.find((p) => p.type === 'Page' && st.graph.edges.some((e) => e.source === p.id && e.target === n.id)) || {};
    box.innerHTML = `
      <div class="card" style="gap:14px">
        <div class="meta">${a.status === 'stale' ? '<span class="badge red">Stale: the source changed since</span>' : '<span class="badge green">Fresh · watched for changes</span>'}
          <span>answered ${esc(day(a.asked_at || a.created))}</span>${headline ? `<span class="badge accent">${headline}</span>` : ''}</div>
        <h2 class="title" style="font-size:26px">${esc(a.question)}</h2>
        <div class="answer">${html}</div>
        ${a.stale_reason ? `<div class="small" style="color:var(--red)">Why stale: ${esc(a.stale_reason)}</div>` : ''}
      </div>
      <div class="graphbox"><div id="g-story"></div><span class="hint">Highlighted: the path from the question to its sources and the decisions behind them · click a node · drag to rearrange</span></div>
      <div class="legend">${HBGraph.legend([...new Set(nodes.map((n) => n.type))])}</div>
      <div class="story">
        <div class="card"><h3 class="label">Timeline</h3>
          <ol class="tl">${st.timeline.map((e, i) => evHtml(e, i)).join('') || '<li class="muted small">No dated events.</li>'}</ol></div>
        <div class="col" style="gap:12px">
          <div class="card" style="padding:0"><div class="nodecard" id="nodecard" style="border-top:0"><span class="muted">Click a node in the graph or an event in the timeline to see its details here.</span></div></div>
          ${order.length ? `<details class="more" open><summary>Sources (${order.length})</summary><div class="srcs" style="margin-top:8px">${order.map((k, i) => {
            const n = citeNode[k] || nodes.find((x) => x.cite === k) || {};
            const page = pageOf(n);
            const owners = nodes.filter((o) => (o.type === 'Person' || o.type === 'Team') && st.graph.edges.some((e) => e.source === page.id && e.target === o.id && e.type === 'OWNED_BY')).map((o) => o.label);
            return `<div class="src"><div class="src-h"><span class="src-n">${i + 1}</span><b>${esc(page.label || k)}</b><span class="muted">› ${esc(n.label || '')}</span></div>
              <div class="meta">${srcBadge(page.source, page.kind)}${page.owner_source ? ownerBadge(page.owner_source, owners) : ''}
              ${n.since ? `<span>version since ${esc(day(n.since))}</span>` : ''}${page.path ? `<button class="linkbtn small" data-page="${esc(page.path)}">Open document</button>` : ''}</div></div>`;
          }).join('')}</div></details>` : ''}
          ${steps ? `<details class="more"><summary>How the agent got here (${steps.length} tool calls)</summary><ol class="steps">${steps.map((s, i) => `<li><span class="stepn">${i + 1}</span><div><b>${esc(s.tool)}</b> <code>${esc(Object.values(s.args || {}).join(' · '))}</code></div></li>`).join('')}</ol></details>` : ''}
        </div>
      </div>
      </div>`;
    const gapi = HBGraph.mount($('#g-story'), st.graph, { layout: 'layered', height: 420, onSelect: (n) => { showNode(n); markEvent(n.id); } });
    box.querySelectorAll('[data-impact]').forEach((b) => b.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      const holder = b.nextElementSibling;
      holder.innerHTML = loading('Walking the graph from this change…');
      try { holder.innerHTML = impactHtml(await api('/api/impact/' + encodeURIComponent(b.dataset.impact))); bindPageLinks(holder); } catch (e) { holder.textContent = e.message; }
    }));
    box.querySelectorAll('.ev').forEach((li) => li.addEventListener('click', () => {
      const e = st.timeline[+li.dataset.i];
      if (e.node) { gapi.select(e.node); const n = nodes.find((x) => x.id === e.node); if (n) showNode(n); }
      box.querySelectorAll('.ev').forEach((x) => x.classList.toggle('on', x === li));
    }));
    box.querySelectorAll('.cite').forEach((b) => b.addEventListener('click', () => {
      const n = citeNode[b.dataset.cite] || nodes.find((x) => x.cite === b.dataset.cite);
      if (n) { gapi.select(n.id); showNode(n); $('#g-story').scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    }));
    bindPageLinks(box);
    function markEvent(nid) { box.querySelectorAll('.ev').forEach((li) => li.classList.toggle('on', st.timeline[+li.dataset.i].node === nid)); }
  }
  function impactHtml(im) {
    const ents = im.entities || [];
    const groups = {};
    ents.forEach((x) => { (groups[x.label] = groups[x.label] || []).push(x); });
    return `<div class="card" style="padding:12px 14px;margin-top:8px;gap:8px">
      ${ents.length ? `<div class="small"><b>Touches</b> ${Object.entries(groups).map(([l, xs]) => xs.map((x) => `<span class="badge ${l === 'Dashboard' ? 'violet' : l === 'Metric' ? 'green' : l === 'Field' ? '' : 'blue'}" title="${esc(l)}">${esc(l)}: ${esc(String(x.name).slice(0, 48))}${x.other_docs ? ' · ' + x.other_docs + ' other docs' : ''}</span>`).join(' ')).join(' ')}</div>` : '<div class="small muted">No systems, fields, metrics or dashboards mentioned in the changed sections.</div>'}
      ${(im.sections || []).some((t) => t.added.length || t.removed.length) ? `<div class="small">${im.sections.map((t) => [...t.added.map((a) => `<span class="badge green">+ ${esc(a[1])}</span>`), ...t.removed.map((a) => `<span class="badge red">− ${esc(a[1])}</span>`)].join(' ')).join(' ')} <span class="muted">references added / removed by this change</span></div>` : ''}
      ${(im.other_docs || []).length ? `<div class="small"><b>Other documents relying on the same things</b><ul style="margin:4px 0 0;padding-left:18px">${im.other_docs.slice(0, 8).map((d) => `<li><button class="linkbtn" data-page="${esc(d.path)}">${esc(d.page)}</button> <span class="muted">via ${esc(d.label)} ${esc(String(d.name).slice(0, 40))}${d.owners.length ? ' · owner ' + esc(d.owners.join(', ')) : ''}</span></li>`).join('')}</ul></div>` : ''}
      ${(im.answers || []).length ? `<div class="small"><b>Answers that relied on the old version</b> ${im.answers.map((a) => `<span class="badge ${a.status === 'stale' ? 'red' : 'green'}">${esc(a.question.slice(0, 60))}</span>`).join(' ')}</div>` : ''}
      ${(im.owners || []).length ? `<div class="small muted">Owners notified: ${esc(im.owners.map((o) => o.name).join(', '))}</div>` : ''}</div>`;
  }
  function evHtml(e, i) {
    const diff = (d) => `<div class="diff">${d.split('\n').map((l) => l.startsWith('REMOVED') ? `<div class="d">− ${esc(l.replace(/^REMOVED \(old text\):\s*/, ''))}</div>` : `<div class="a">+ ${esc(l.replace(/^ADDED \(new text\):\s*/, ''))}</div>`).join('')}</div>`;
    const t = {
      decision: () => `<div class="what">${esc(e.title)}</div><div class="det">Decided${e.who ? ' by <b>' + esc(e.who) + '</b>' : ''} in ${esc(convName(e.src))} · “${esc(e.where)}”${e.status === 'implemented' ? ` · written into the docs${e.lag != null ? ' ' + e.lag + ' days later' : ''}` : ' · <span style="color:var(--red)">not documented yet</span>'}</div>${e.quote ? `<div class="quote">“${esc(e.quote)}”</div>` : ''}`,
      change: () => `<div class="what">${esc(e.title)}</div><div class="det">${esc(e.detail)}${e.mr ? ` · <a href="${esc(e.url)}" target="_blank" rel="noopener">MR !${esc(e.mr)} ↗</a>` : ''}${e.who ? ' · merged by ' + esc(e.who) : ''}${e.author ? ' · author ' + esc(e.author) : ''}</div>${e.diff ? diff(e.diff) : ''}${e.sha ? `<div><button class="linkbtn small" data-impact="${esc(e.sha)}">What did this change affect?</button><div class="impact"></div></div>` : ''}`,
      conflict: () => `<div class="what">Sources disagree: ${esc(e.title)}</div><div class="det">${esc(e.detail)}</div>`,
      claim: () => `<div class="what">“${esc(e.title)}”</div><div class="det">${esc(e.who || '')} in ${esc(convName(e.src))} · contradicts the doc: ${esc(e.detail)}</div>`,
      answer: () => '<div class="what">Answer given</div><div class="det">Cites exact section versions; watched from here on.</div>',
      stale: () => `<div class="what">Answer invalidated</div><div class="det">${esc(e.detail)}${e.status === 'source_conflict' ? ' (sources disagree)' : ''}</div>`,
      revalidated: () => `<div class="what">Re-checked, still valid</div><div class="det">${esc(e.title)}: ${esc(e.detail)}</div>`,
      earlier_answer: () => `<div class="what">Earlier answer, replaced</div><div class="det">${esc(e.detail || '')}</div>`,
    }[e.kind];
    return `<li class="ev ${esc(e.kind)}" data-i="${i}"><span class="pin"></span><div class="when">${esc(day(e.ts))}</div>${t ? t() : esc(e.title)}</li>`;
  }
  function showNode(n) {
    const card = $('#nodecard');
    if (!card || !n) return;
    const T = HBGraph.typeOf(n.type).name;
    const bits = [];
    if (n.cite) bits.push(`<code>${esc(n.cite)}</code>`);
    if (n.type === 'Section') bits.push(n.current === false ? '<span class="badge">superseded version</span>' : '<span class="badge blue">current version</span>');
    if (n.outdated_copy) bits.push('<span class="badge red">outdated copy</span>');
    if (n.since) bits.push(`<span>valid from ${esc(day(n.since))}${n.until ? ' to ' + esc(day(n.until)) : ''}</span>`);
    if (n.ts) bits.push(`<span>${esc(day(n.ts))}</span>`);
    if (n.at) bits.push(`<span>${esc(day(n.at))}</span>`);
    if (n.src) bits.push(`<span class="badge amber">${esc(convName(n.src))}</span>`);
    if (n.kind) bits.push(srcBadge(n.source || n.label, n.kind));
    if (n.status) bits.push(`<span class="badge">${esc(n.status)}</span>`);
    card.innerHTML = `<div class="meta"><b style="color:var(--ink)">${esc(T)}</b>${bits.join('')}</div><div style="font-weight:600">${esc(n.label)}</div>
      ${n.quote ? `<div class="quote">“${esc(n.quote)}”</div>` : ''}${n.text ? `<div class="quote" style="max-height:140px;overflow:auto">${esc(n.text)}</div>` : ''}
      ${n.url && /^https?:/.test(n.url) ? `<a class="small" href="${esc(n.url)}" target="_blank" rel="noopener">Open source ↗</a>` : ''}
      ${n.type === 'Page' && n.path ? `<div><button class="linkbtn small" data-page="${esc(n.path)}">Open document and its history</button></div>` : ''}`;
    bindPageLinks(card);
  }

  // ------------------------------------------------------------------ explore
  async function vExplore(focusPath) {
    view.innerHTML = loading('Loading the graph…');
    const [g0, up] = await Promise.all([focusPath ? api('/api/graph/page?path=' + encodeURIComponent(focusPath)) : api('/api/graph/explore'), api('/api/updates?limit=200')]);
    // Sources are hubs that would pull every page into one ball: shown as the page's ring colour instead
    const g = focusPath ? g0 : { nodes: g0.nodes.filter((n) => n.type !== 'Source'), edges: g0.edges.filter((e) => e.type !== 'PROVIDES') };
    const types = [...new Set(g.nodes.map((n) => n.type))];
    const hidden = new Set();
    view.innerHTML = `
      <section class="hero" style="padding-top:0;gap:10px">
        <span class="eyebrow">Explore · ${esc(S.ov?.name || '')}</span>
        <h1 class="display" style="font-size:42px">${focusPath ? 'One document, and everything connected to it' : 'The company, as a graph'}</h1>
        <p class="lede">${focusPath ? '<button class="linkbtn" id="back">← Company graph</button> · sections (current and superseded), the changes that made them, decisions about them.' : 'Documents clustered by the team that owns them, and the decisions from meetings and chat that changed them. Amber rings: static files (uploaded PDFs, DOCX). Red dashed lines: sources that disagree. Click a document to drill in.'}</p>
      </section>
      <div class="row" style="align-items:center;gap:10px">
        <div class="filters">${types.map((t) => { const x = HBGraph.typeOf(t); return `<button class="fchip" aria-pressed="true" data-t="${esc(t)}"><i style="background:${x.c}"></i>${esc(x.name)} · ${g.nodes.filter((n) => n.type === t).length}</button>`; }).join('')}</div>
        <div class="grow"></div><input class="search" id="find" placeholder="Find a node…" aria-label="Find a node">
      </div>
      <div class="graphbox"><div id="g-explore"></div><div class="tools"><button class="btn" id="reset" style="height:32px">Reset</button></div>
        <div class="nodecard" id="nodecard"><span class="muted">Click a node to see what it is and how it connects. Drag to rearrange · ⌘/Ctrl + scroll to zoom.</span></div></div>
      <section class="section"><h3 class="label">Changes over time</h3><div class="card lanes" id="lanes"></div><div id="lanecard"></div></section>`;
    const gapi = HBGraph.mount($('#g-explore'), g, { height: 620, label: 'Company graph', onSelect: (n) => {
      showNode(n);
      if (n.type === 'Page' && !focusPath) {
        $('#nodecard').insertAdjacentHTML('beforeend', '<div><button class="btn" id="drill" style="height:32px">Show this document\'s graph</button></div>');
        $('#drill').addEventListener('click', () => vExplore(n.path));
      }
    } });
    view.querySelectorAll('.fchip').forEach((b) => b.addEventListener('click', () => {
      const t = b.dataset.t; if (hidden.has(t)) hidden.delete(t); else hidden.add(t);
      b.setAttribute('aria-pressed', String(!hidden.has(t))); gapi.filter(hidden);
    }));
    $('#find').addEventListener('input', (e) => gapi.find(e.target.value));
    $('#reset').addEventListener('click', () => gapi.reset());
    const back = $('#back'); if (back) back.addEventListener('click', () => vExplore());
    lanes(up.changes);
  }
  function lanes(changes) {
    const box = $('#lanes');
    const pts = changes.map((c) => ({ ...c, d: toDate(c.ts), lane: c.dept || c.source || 'docs' })).filter((c) => c.d);
    if (!pts.length) { box.innerHTML = '<div class="muted small">No changes since the graph was built.</div>'; return; }
    const laneNames = [...new Set(pts.map((p) => p.lane))].slice(0, 12);
    const W = Math.max(box.clientWidth - 40, 640), L = 150, R = 20, rowH = 40, top = 28;
    const t0 = Math.min(...pts.map((p) => +p.d)), t1 = Math.max(...pts.map((p) => +p.d)) || t0 + 1;
    const x = (d) => L + ((+d - t0) / Math.max(t1 - t0, 1)) * (W - L - R);
    const H = top + laneNames.length * rowH + 10;
    const months = [];
    const m = new Date(t0); m.setUTCDate(1);
    while (+m <= t1) { if (+m >= t0) months.push(new Date(m)); m.setUTCMonth(m.getUTCMonth() + 1); }
    let svg = `<svg width="${W}" height="${H}" role="img" aria-label="Changes by area over time">`;
    laneNames.forEach((ln, i) => {
      const y = top + i * rowH;
      if (i % 2 === 0) svg += `<rect x="0" y="${y}" width="${W}" height="${rowH}" fill="#FAF9F6"/>`;
      svg += `<text x="10" y="${y + rowH / 2 + 4}" font-size="12.5" font-weight="600" fill="#3D3B38">${esc(String(ln).slice(0, 20))}</text>`;
    });
    months.forEach((d) => { const xx = x(d); svg += `<line x1="${xx}" x2="${xx}" y1="${top - 6}" y2="${H}" stroke="#E8E5DE"/><text x="${xx + 4}" y="${top - 10}" font-size="11" fill="#9A968F">${d.toLocaleDateString(undefined, { month: 'short', timeZone: 'UTC' })}</text>`; });
    pts.forEach((p, i) => {
      const li = laneNames.indexOf(p.lane); if (li < 0) return;
      const y = top + li * rowH + rowH / 2, dec = p.decisions && p.decisions.length;
      svg += `<g class="lp" data-i="${i}" style="cursor:pointer"><title>${esc(day(p.ts))}: ${esc(p.title)}</title>${dec ? `<circle cx="${x(p.d)}" cy="${y}" r="10" fill="#C96442" opacity=".18"/>` : ''}<circle cx="${x(p.d)}" cy="${y}" r="${dec ? 6 : 4.5}" fill="${dec ? '#C96442' : p.kind === 'static' ? '#B07A12' : '#7C5CD6'}"/></g>`;
    });
    box.innerHTML = svg + '</svg><div class="legend" style="margin-top:8px"><span class="lg"><i style="background:#7C5CD6"></i>document change</span><span class="lg"><i style="background:#C96442"></i>change that implemented a meeting/chat decision</span><span class="lg"><i style="background:#B07A12"></i>static file uploaded</span></div>';
    box.querySelectorAll('.lp').forEach((g) => g.addEventListener('click', () => {
      const p = pts[+g.dataset.i];
      $('#lanecard').innerHTML = `<div class="card"><div class="meta"><span>${esc(day(p.ts))}</span>${srcBadge(p.source, p.kind)}${p.mr ? `<a href="${esc(p.mr_url)}" target="_blank" rel="noopener">MR !${esc(p.mr)} ↗</a>` : ''}</div>
        <b>${esc(p.title)}</b><ul class="small" style="margin:0;padding-left:18px">${p.sections.map((s) => `<li><button class="linkbtn" data-page="${esc(s.path)}">${esc(s.page)}</button> › ${esc(s.heading)} <span class="badge">${esc(s.kind)}</span></li>`).join('')}</ul>
        ${(p.decisions || []).map((d) => `<div class="small"><span class="badge accent">${esc(convName(d.src))}</span> decided ${esc(day(d.at))} in “${esc(d.where)}”: ${esc(d.statement)}</div>`).join('')}</div>`;
      bindPageLinks($('#lanecard'));
    }));
  }

  // ------------------------------------------------------------------ review
  async function vReview() {
    view.innerHTML = loading();
    const [r, pr] = await Promise.all([api('/api/review'), api('/api/proposals').catch(() => ({ proposals: [] }))]);
    r.proposals = pr.proposals;
    S.props = Object.fromEntries(pr.proposals.map((p) => [p.id, p]));
    const groups = [
      ['stale_answers', 'Answers that went stale', 'A document an earlier answer relied on changed, or a more authoritative source contradicts it. Owners were notified; refresh re-answers from the current version.',
        (a) => `<td><b>${esc(a.question)}</b><div class="small muted">${esc(a.reason)}</div></td><td class="small">${a.kind === 'source_conflict' ? '<span class="badge amber">sources disagree</span>' : '<span class="badge">doc changed</span>'}<div class="muted">${esc(day(a.since))}</div></td><td class="small muted">${esc((a.notified || []).join(', '))}</td><td><button class="linkbtn small" data-story="${esc(a.id)}">Trail</button></td>`],
      ['source_conflicts', 'Sources that disagree', 'A static file (e.g. an old PDF on the shared drive) says something different from the live, more authoritative page.',
        (c) => `<td><b>${esc(c.topic)}</b><div class="small muted">${esc(c.reason)}</div></td><td class="small"><div>outdated <code>${esc(c.outdated)}</code></div><div>wins <code>${esc(c.authoritative)}</code></div></td><td class="small muted">${esc((c.notify || []).join(', ') || 'no owner')}</td><td></td>`],
      ['decided_not_documented', 'Decided, but not documented', 'Decisions made in a meeting or chat that no document change implemented after three weeks.',
        (d) => `<td><b>${esc(d.statement)}</b><div class="small muted">${esc(convName(d.source))} · ${esc(d.meeting)} · ${esc(day(d.decided_at))}</div></td><td class="small"><span class="badge accent">${Math.round(d.days_open)} days open</span><div class="muted"><code>${esc(d.section || '')}</code></div></td><td class="small muted">${esc([...(d.page_owners || []), ...(d.assignees || [])].join(', '))}</td><td>${d.section ? `<button class="btn" style="height:32px" data-draft="${esc(d.decision)}">Draft update</button>` : ''}</td>`],
      ['proposals', 'Drafted updates', 'Updates the agent drafted from decisions, waiting for (or decided by) the owners who must approve them.',
        (p) => `<td><b>${esc(p.summary || p.decision)}</b><div class="small muted">${esc(p.page)} › ${esc(p.heading)} · from “${esc(p.where || '')}”</div></td><td class="small"><span class="badge ${p.status === 'approved' ? 'green' : p.status === 'rejected' ? 'red' : 'accent'}">${esc(p.status)}</span>${p.decided_by ? `<div class="muted">by ${esc(p.decided_by)}</div>` : ''}</td><td class="small muted">approver: ${esc((p.approvers || []).join(', ') || 'no owner')}</td><td><button class="linkbtn small" data-prop="${esc(p.id)}">Open</button></td>`],
      ['chat_vs_docs', 'Chat or meeting contradicts the docs', 'Someone stated a rule in a conversation that the current document says differently.',
        (c) => `<td><b>“${esc(c.statement)}”</b><div class="small muted">${esc(c.speaker || '')} · ${esc(convName(c.source))} · ${esc(c.where)} · ${esc(day(c.at))}</div></td><td class="small">${esc(c.reason)}<div class="muted"><code>${esc(c.cite)}</code></div></td><td class="small muted">${esc((c.owners || []).join(', '))}</td><td></td>`],
      ['gaps', 'Knowledge gaps', 'Questions people asked that no document answers. "Tribal": a colleague answered it in chat, but it was never written down.',
        (g) => `<td><b>${esc(g.question)}</b>${g.chat_answer ? `<div class="small muted">Chat answer: ${esc(g.chat_answer)}</div>` : ''}</td><td class="small">${g.tribal ? '<span class="badge amber">tribal</span>' : '<span class="badge">gap</span>'}<div class="muted">asked ${g.asked}×</div></td><td class="small muted">${esc(g.where || '')}</td><td></td>`],
    ];
    const total = groups.reduce((s, [k]) => s + r[k].length, 0);
    view.innerHTML = `
      <section class="hero" style="padding-top:0;gap:10px"><span class="eyebrow">Review · ${esc(S.ov?.name || '')}</span>
        <h1 class="display" style="font-size:42px">${total} things need a human</h1>
        <p class="lede">Nothing in your documents changes automatically. HandbookBrain finds what drifted and tells the people who own it.</p></section>
      <div class="tabs" role="tablist"><button role="tab" data-g="all" aria-selected="${S.reviewTab === 'all'}">All</button>${groups.map(([k, t]) => `<button role="tab" data-g="${k}" aria-selected="${S.reviewTab === k}">${t} <span class="count">${r[k].length}</span></button>`).join('')}</div>
      ${groups.filter(([k]) => S.reviewTab === 'all' || S.reviewTab === k).map(([k, t, d, row]) => `
        <section class="section" style="gap:8px"><div class="row" style="align-items:center;gap:10px"><h2 class="title" style="font-size:24px">${t}</h2><span class="badge ${r[k].length ? 'accent' : 'green'}">${r[k].length}</span><div class="grow"></div>${k === 'stale_answers' && r[k].length ? '<button class="btn" id="refresh">Refresh stale answers</button>' : ''}</div>
          <p class="muted small" style="margin:0">${d}</p>
          ${r[k].length ? `<div class="card tbl-box" style="padding:6px 12px"><table class="tbl"><tbody>${r[k].map((x) => `<tr>${row(x)}</tr>`).join('')}</tbody></table></div>` : '<div class="muted small">Nothing here.</div>'}</section>`).join('')}`;
    view.querySelectorAll('[data-g]').forEach((b) => b.addEventListener('click', () => { S.reviewTab = b.dataset.g; vReview(); }));
    view.querySelectorAll('[data-story]').forEach((b) => b.addEventListener('click', () => { S.demoId = b.dataset.story; location.hash = '#/demo'; }));
    view.querySelectorAll('[data-draft]').forEach((b) => b.addEventListener('click', async () => {
      b.disabled = true; b.innerHTML = '<span class="spinner"></span> Drafting…';
      try { const p = await api('/api/proposals/draft', { method: 'POST', body: JSON.stringify({ decision_id: b.dataset.draft }) }); openProposal(p, true); } catch (e) { alert(e.message); }
      b.disabled = false; b.textContent = 'Draft update';
    }));
    view.querySelectorAll('[data-prop]').forEach((b) => b.addEventListener('click', () => openProposal(S.props[b.dataset.prop], false)));
    const rb = $('#refresh');
    if (rb) rb.addEventListener('click', async () => { rb.disabled = true; rb.innerHTML = '<span class="spinner"></span> Re-answering…'; try { await api('/api/refresh', { method: 'POST' }); } catch (e) { /* */ } await loadHeader(); vReview(); });
  }

  function openProposal(p, fresh) {
    const dr = $('#drawer'); dr.hidden = false;
    const im = p.impact || { entities: [], answers: [] };
    const sec = p.section || { page: p.page, heading: p.heading, cite: p.cite, path: p.path };
    const dec = p.decision && typeof p.decision === 'object' ? p.decision : { statement: p.decision, where: p.where };
    const approvers = (p.approvers || []).map((a) => (typeof a === 'string' ? a : a.name));
    $('#drawer-body').innerHTML = `
      <span class="eyebrow">Drafted update · ${esc(p.status || 'pending')}</span>
      <h2 id="drawer-title" class="title">${esc(p.summary || dec.statement)}</h2>
      <div class="card" style="gap:6px"><h3 class="label">Decision</h3><div><b>${esc(dec.statement)}</b></div>
        <div class="small muted">${dec.by ? esc(dec.by) + ' · ' : ''}${esc(convName(dec.src))} · ${esc(dec.where || '')}${dec.at ? ' · ' + esc(day(dec.at)) : ''}</div>${dec.quote ? `<div class="quote">“${esc(dec.quote)}”</div>` : ''}</div>
      <div class="card" style="gap:6px"><h3 class="label">Proposed change to ${esc(sec.page)} › ${esc(sec.heading)}</h3>
        ${p.diff ? `<div class="diff">${p.diff.split('\n').map((l) => l.startsWith('REMOVED') ? `<div class="d">− ${esc(l.replace(/^REMOVED \(old text\):\s*/, ''))}</div>` : `<div class="a">+ ${esc(l.replace(/^ADDED \(new text\):\s*/, ''))}</div>`).join('')}</div>` : '<div class="muted small">No change needed: the section already reflects the decision.</div>'}
        ${sec.path ? `<div><button class="linkbtn small" data-page="${esc(sec.path)}">Open the current document</button></div>` : ''}</div>
      <div class="card" style="gap:6px"><h3 class="label">What it affects</h3>
        ${im.entities.length ? `<div class="small">${im.entities.map((e) => `<span class="badge ${e.type === 'Dashboard' ? 'violet' : e.type === 'Metric' ? 'green' : 'blue'}">${esc(e.type)}: ${esc(String(e.name).slice(0, 44))}${e.other_docs ? ' · used in ' + e.other_docs + ' other docs' : ''}</span>`).join(' ')}</div>` : '<div class="small muted">No systems, fields, metrics or dashboards referenced here.</div>'}
        ${im.answers.length ? `<div class="small">Answers that will be re-checked when this merges: ${im.answers.map((a) => `<span class="badge">${esc(a.question.slice(0, 60))}</span>`).join(' ')}</div>` : ''}</div>
      <div class="card" style="gap:10px"><h3 class="label">Approval</h3>
        <div class="small">Needs approval from <b>${esc(approvers.join(', ') || 'no owner on record (route to the team lead)')}</b> (page owners in CODEOWNERS / the document's owner field).</div>
        ${(p.status || 'pending') === 'pending' ? `<div style="display:flex;gap:8px;flex-wrap:wrap"><input class="search" id="approver" placeholder="Your name" style="min-width:160px">
          <button class="btn primary" id="approve">Approve</button><button class="btn" id="reject">Reject</button></div>
          <div class="small muted">Approving records who signed off and produces the patch to merge; the document itself changes in its own system, and the next sync closes the loop.</div>` :
          `<div class="small"><span class="badge ${p.status === 'approved' ? 'green' : 'red'}">${esc(p.status)}</span> by ${esc(p.decided_by || '')} ${esc(day(p.decided_at))}</div>`}</div>`;
    bindPageLinks($('#drawer-body'));
    const act = async (status) => {
      try { const r = await api('/api/proposals/' + encodeURIComponent(p.id), { method: 'POST', body: JSON.stringify({ status, by: ($('#approver') || {}).value || '' }) }); openProposal({ ...p, ...r }, false); if (location.hash.includes('review')) vReview(); } catch (e) { alert(e.message); }
    };
    if ($('#approve')) { $('#approve').addEventListener('click', () => act('approved')); $('#reject').addEventListener('click', () => act('rejected')); }
  }

  // ------------------------------------------------------------------ configure / capabilities
  async function vConfigure() {
    view.innerHTML = loading();
    const [cfg, ov, t] = await Promise.all([api('/api/config'), api('/api/overview'), api('/api/trust').catch(() => ({}))]);
    S.ov = ov;
    const hasUploads = ov.sources.some((s) => s.type === 'uploads');
    const frac = (s) => { const [a, b] = String(s).split('/').map(Number); return b ? a / b : 0; };
    const barRow = (label, val, note = '') => `<tr><td>${esc(label)}</td><td style="width:30%"><div class="bar"><i style="width:${Math.round(frac(val) * 100)}%"></i></div></td><td class="mono">${esc(val)}</td><td class="small muted">${esc(note)}</td></tr>`;
    view.innerHTML = `
      <section class="hero" style="padding-top:0;gap:10px"><span class="eyebrow">Capabilities and configuration</span>
        <h1 class="display" style="font-size:42px">One config file per company. No code changes.</h1>
        <p class="lede">Point it at where your knowledge lives. Live sources are re-synced daily; static files are uploaded once and checked against the live ones. Connect directly or upload exports, whichever your security team prefers.</p></section>

      <section class="section"><h3 class="label">Sources for ${esc(ov.name)}</h3>
        <div class="row">${ov.sources.map((s) => `<div class="card" style="flex:1 1 260px"><div class="meta"><span class="badge ${s.kind === 'static' ? 'amber' : 'blue'}">${esc(s.kind)}</span><span>${esc(s.type)}</span><span>authority ${esc(s.authority)}</span></div>
          <b style="font-size:16px">${esc(s.label || s.name)}</b><span class="small muted">${s.pages} documents · ${s.kind === 'static' ? 'changes only when a new file is uploaded' : 're-synced daily'}${s.last_synced ? ' · last sync ' + esc(day(s.last_synced)) : ''}</span></div>`).join('')}
          <div class="card" style="flex:1 1 260px"><b>Daily sync</b><span class="small muted">Pull live sources, apply every change since the last sync, re-check stored answers and conflicts, refresh what went stale. Runs from cron; or now:</span><div><button class="btn primary" id="syncbtn">Sync now</button></div><div id="synclog"></div></div></div>
        ${hasUploads ? '<div class="drop" id="drop"><b style="color:var(--ink)">Upload documents</b><span class="small">PDF, DOCX, HTML, Markdown, TXT or CSV, stored as a static source and checked against the live docs on the next sync.</span><label class="btn"><input type="file" id="files" multiple hidden accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm,.csv">Choose files</label><div id="upmsg" class="small"></div></div>' : ''}
      </section>

      <section class="row">
        <div class="col"><h3 class="label">companies/${esc(cfg.company)}.yaml</h3><pre class="yaml">${esc(cfg.yaml || '# built-in defaults (GitLab handbook)')}</pre></div>
        <div class="col">
          <h3 class="label">Connect or upload</h3>
          <div class="card"><dl class="kv">
            <dt>Documents</dt><dd>git repositories, dated export folders (Confluence / SharePoint / Drive), uploads · md, html, pdf, docx, txt, csv</dd>
            <dt>Meetings</dt><dd>Zoom (vtt, captions), Teams (vtt), Google Meet transcripts, meeting notes (human or AI-written, labelled as such)</dd>
            <dt>Chat</dt><dd>Slack export, Teams chat, Google Chat, WhatsApp, Discord</dd>
            <dt>Tickets, email</dt><dd>Jira CSV, Zendesk JSON, eml, mbox</dd>
            <dt>Provenance</dt><dd>every document carries an owner source (explicit / inferred / none) and an origin (human-authored / AI summary / AI-extracted / conversation)</dd></dl></div>
          <h3 class="label">Models and cost controls</h3>
          <div class="card"><dl class="kv">
            <dt>Agent</dt><dd><code>${esc(cfg.models.agent)}</code></dd><dt>Reviewer</dt><dd><code>${esc(cfg.models.critic)}</code> (different model, sees only the evidence)</dd>
            <dt>Conflict judge</dt><dd><code>${esc(cfg.models.conflict_judge)}</code></dd><dt>Embeddings</dt><dd><code>${esc(cfg.models.embeddings)}</code></dd>
            <dt>LLM cache</dt><dd>${cfg.cost_controls.llm_cache ? 'on: identical requests are never paid twice' : 'off'}</dd><dt>Graph</dt><dd><code>${esc(cfg.graph)}</code> on FalkorDB · built at ${esc(cfg.t0)}</dd></dl></div>
        </div>
      </section>

      ${t.available ? `<section class="section"><h3 class="label">How well it works</h3>
        <p class="muted small" style="margin:0">End-to-end benchmark on ${esc(ov.name)}: build the graph at the start date, ask questions, replay every document change and conversation in date order, score against ground truth the agent never sees. Last run ${esc(t.ran_at)} · ${esc(t.usage)}. Ground truth is synthetic; one run; LLM grading adds a few points of noise.</p>
        <div class="row"><div class="col card"><h3 class="label">Answers by question type</h3><table class="tbl"><tbody>${Object.entries(t.by_type).map(([k, x]) => barRow(k.replace(/_/g, ' '), `${x.correct || 0}/${x.n}`, `${x.partial || 0} partial`)).join('')}</tbody></table></div>
          <div class="col card"><h3 class="label">Watching and linking</h3><table class="tbl"><tbody>
            ${barRow('stale answers caught', `${t.staleness.tp}/${t.staleness.tp + t.staleness.fn}`, `${t.staleness.fp} false alarm`)}
            ${t.source_conflicts ? barRow('live vs static conflicts', t.source_conflicts.found, `${t.source_conflicts.false_alarms_on_consistent_sections} false alarms`) : ''}
            ${Object.entries(t.conversations).map(([k, v]) => barRow(k.replace(/_/g, ' '), v)).join('')}</tbody></table></div></div></section>` : ''}

      <section class="section"><h3 class="label">Next (step 2)</h3><div class="card"><ul class="roadmap" style="margin:0;padding-left:18px">
        <li>Direct OAuth connectors for Slack, Zoom and Google Drive (today: their native exports, or a folder they sync to)</li>
        <li>Company playbook generated from the company's own process docs, so the agent searches like a senior colleague</li>
        <li>One-click "draft the fix": a proposed doc change for each stale or undocumented decision, approved by the owner</li>
        <li>Weekly digest per person for the areas they follow (email / Slack)</li>
        <li>Audio and video meetings via speech-to-text</li>
        <li>Per-user permissions mirrored from the source systems; hosted multi-tenant deploy</li></ul></div></section>`;
    $('#syncbtn').addEventListener('click', async () => { await api('/api/sync', { method: 'POST' }); pollSync(); });
    if (ov.sync?.running) pollSync();
    const drop = $('#drop');
    if (drop) {
      const send = async (files) => {
        const fd = new FormData(); [...files].forEach((f) => fd.append('files', f));
        $('#upmsg').textContent = 'Uploading…';
        try { const r = await api('/api/upload', { method: 'POST', body: fd }); $('#upmsg').innerHTML = `Saved ${r.saved.map(esc).join(', ')}. Run a sync to add them.`; } catch (e) { $('#upmsg').textContent = e.message; }
      };
      $('#files').addEventListener('change', (e) => send(e.target.files));
      drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
      drop.addEventListener('dragleave', () => drop.classList.remove('over'));
      drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); send(e.dataTransfer.files); });
    }
  }
  async function pollSync() {
    for (;;) {
      const s = await api('/api/sync');
      paintSync(s);
      const log = $('#synclog'), btn = $('#syncbtn');
      if (log) log.innerHTML = s.log.length ? `<div class="log">${esc(s.log.join('\n'))}</div>` : '';
      if (btn) { btn.disabled = s.running; btn.textContent = s.running ? 'Syncing…' : 'Sync now'; }
      if (!s.running) { if (s.error && log) log.insertAdjacentHTML('afterbegin', `<span class="badge red">${esc(s.error)}</span>`); await loadHeader(); return; }
      await new Promise((r) => setTimeout(r, 1500));
    }
  }

  // ------------------------------------------------------------------ document drawer
  function bindPageLinks(root) { root.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => openPage(b.dataset.page))); }
  async function openPage(path) {
    const dr = $('#drawer'); dr.hidden = false;
    $('#drawer-body').innerHTML = loading(); $('#drawer-close').focus();
    try {
      const [d, g] = await Promise.all([api('/api/page?path=' + encodeURIComponent(path)), api('/api/graph/page?path=' + encodeURIComponent(path))]);
      const det = d.details || {};
      $('#drawer-body').innerHTML = `
        <h2 id="drawer-title" class="title">${esc(det.title || path)}</h2>
        <div class="meta"><code>${esc(path)}</code>${det.url && /^https?:/.test(det.url) ? `<a href="${esc(det.url)}" target="_blank" rel="noopener">Open original ↗</a>` : ''}</div>
        <div class="meta">${(det.owners || []).map((o) => `<span class="badge green">${esc(String(o).split('|').pop())}</span>`).join('') || '<span class="badge red">No owner</span>'}</div>
        <div class="graphbox"><div id="g-page"></div></div>
        ${d.sections.map((s) => `<details class="card" ${s.outdated_by.length || s.versions ? 'open' : ''}>
          <summary style="cursor:pointer;font-weight:600">${esc(s.heading)} ${s.versions ? `<span class="badge blue">${s.versions + 1} versions</span>` : ''}${s.outdated_by.length ? ' <span class="badge red">outdated copy</span>' : ''}${s.older_copies.length ? ' <span class="badge amber">older copies disagree</span>' : ''}</summary>
          <div class="small muted">this version since ${esc(day(s.since))} · <code>${esc(s.cite)}</code></div>
          <div class="quote">${esc(s.text)}</div>
          ${(s.history || []).filter((h) => h.diff && !h.diff.startsWith('(first')).map((h) => `<div class="small"><b>${esc(day(h.since))}</b> ${esc(h.change || '')} ${h.mr_url ? `<a href="${esc(h.mr_url)}" target="_blank" rel="noopener">MR !${esc(h.mr)} ↗</a>` : ''}
            <div class="diff">${h.diff.split('\n').map((l) => l.startsWith('REMOVED') ? `<div class="d">− ${esc(l.replace(/^REMOVED \(old text\):\s*/, ''))}</div>` : `<div class="a">+ ${esc(l.replace(/^ADDED \(new text\):\s*/, ''))}</div>`).join('')}</div></div>`).join('')}
        </details>`).join('')}`;
      if (g.nodes.length) HBGraph.mount($('#g-page'), g, { layout: 'layered', height: 300, label: 'Document graph' });
    } catch (e) { $('#drawer-body').innerHTML = `<div class="card warn">${esc(e.message)}</div>`; }
  }
  const closeDrawer = () => { $('#drawer').hidden = true; };
  $('#drawer-close').addEventListener('click', closeDrawer);
  $('#drawer').addEventListener('click', (e) => { if (e.target.id === 'drawer') closeDrawer(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#drawer').hidden) closeDrawer(); });

  // ------------------------------------------------------------------ router
  async function route() {
    const r = (location.hash.replace(/^#\/?/, '') || 'overview').split('?')[0];
    document.querySelectorAll('[data-nav]').forEach((a) => { if (a.dataset.nav === r) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    try {
      if (r === 'demo') await vDemo();
      else if (r === 'explore') await vExplore();
      else if (r === 'review') await vReview();
      else if (r === 'configure') await vConfigure();
      else await vOverview();
    } catch (e) { view.insertAdjacentHTML('afterbegin', `<div class="card warn">Something went wrong: ${esc(e.message)}</div>`); }
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', route);
  loadHeader().then(route);
})();
