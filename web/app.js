/* HandbookBrain web UI (no build step). Views: overview, ask, learn, explore, review, configure (setup). Data: hb/api.py. */
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
      const out = []; let ul = null, para = [];
      const flushP = () => { if (para.length) { out.push(`<p>${para.join('<br>')}</p>`); para = []; } };
      const flushU = () => { if (ul) { out.push('<ul>' + ul.join('') + '</ul>'); ul = null; } };
      b.split('\n').forEach((l) => {
        const m = /^\s*(?:[-*•]|\d+\.)\s+(.*)$/.exec(l);
        if (m) { flushP(); (ul = ul || []).push(`<li>${m[1]}</li>`); return; }
        flushU();
        if (/^#{1,4}\s/.test(l)) { flushP(); out.push(`<h4>${l.replace(/^#+\s/, '')}</h4>`); } else if (l.trim()) para.push(l);
      });
      flushU(); flushP();
      return out.join('');
    }).join('');
  }


  // Document text (markdown from handbooks / wikis) rendered for people: tables as tables, links as short links,
  // lists, headings and emphasis; HTML in the source is escaped first, so nothing from a document can run.
  function mdLite(text) {
    const inline = (t) => esc(t)
      .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
      .replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, (_, label, url) => {
        const l = label.replace(/[^\w\s.,&/()-]/g, '').trim();
        const host = url.replace(/^https?:\/\//, '').split(/[/?#]/)[0].split('.').slice(-2).join('.');
        const name = l || (url.startsWith('http') ? host + ' ↗' : url.split('/').filter(Boolean).pop() || 'link');
        return /^https?:/.test(url) ? `<a href="${url}" target="_blank" rel="noopener">${name}</a>` : `<span class="dlink">${name}</span>`;
      })
      .replace(/(^|[\s(])(https?:\/\/[^\s<)|]+)/g, (_, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noopener">${url.replace(/^https?:\/\//, '').split(/[/?#]/)[0].split('.').slice(-2).join('.')} ↗</a>`)
      .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
    const lines = String(text || '').replace(/\r/g, '').split('\n');
    const out = []; let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      if (/^\s*\|.*\|\s*$/.test(l)) {   // table
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(lines[i]); i++; }
        const cells = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r)).map((r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
        if (cells.length) out.push(`<div class="tblwrap"><table class="mdt"><thead><tr>${cells[0].map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${cells.slice(1).map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
        continue;
      }
      if (/^\s*([-*+]|\d+\.)\s+/.test(l)) {
        const items = [];
        while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, '')); i++; }
        out.push(`<ul>${items.map((x) => `<li>${inline(x)}</li>`).join('')}</ul>`);
        continue;
      }
      if (/^#{1,6}\s/.test(l)) { out.push(`<h5>${inline(l.replace(/^#+\s/, ''))}</h5>`); i++; continue; }
      if (!l.trim() || /^[{<]/.test(l.trim()) && /[}>]$/.test(l.trim())) { i++; continue; }   // blanks, shortcodes / raw tags
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*\|.*\|\s*$/.test(lines[i]) && !/^\s*([-*+]|\d+\.)\s+/.test(lines[i]) && !/^#{1,6}\s/.test(lines[i])) { para.push(lines[i]); i++; }
      out.push(`<p>${inline(para.join(' '))}</p>`);
    }
    return out.join('');
  }
  // long sections start collapsed; "Show the full section" expands in place
  const docBlock = (text, cls = '') => `<div class="docwrap ${cls}"><div class="doc">${mdLite(text)}</div><button class="linkbtn small docmore" hidden>Show the full section</button></div>`;
  function bindDocs(root) {
    root.querySelectorAll('.docwrap').forEach((w) => {
      const d = w.querySelector('.doc'), b = w.querySelector('.docmore');
      if (!w.classList.contains('clamped') && d.scrollHeight > (w.classList.contains('short') ? 170 : 260)) { w.classList.add('clamped'); b.hidden = false; }
      b.onclick = () => { const open = w.classList.toggle('open'); b.textContent = open ? 'Show less' : 'Show the full section'; };
    });
  }

  // ------------------------------------------------------------------ onboarding: glossary links, term popover, "ask about this"
  const COACHABLE = /^(D|C|S|P|A|E|T|V|O|U|cite):/;
  const askBtn = (node, label = 'Ask about this') => (node && COACHABLE.test(node) ? `<button class="askbtn" data-coach="${esc(node)}">${ico('ask')}${esc(label)}</button>` : '');
  async function loadTerms() {
    if (S.terms) return S.terms;
    try {
      const r = await api('/api/glossary');
      const reEsc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const cs = r.terms.filter((t) => /[A-Z0-9]/.test(t.name[0]) && !(t.type === 'Metric' && / /.test(t.name) && t.name !== t.name.toUpperCase()));
      const ci = r.terms.filter((t) => !cs.includes(t));
      const mk = (xs, f) => xs.length ? new RegExp('(?<![\\w-])(' + xs.map((t) => reEsc(t.name)).sort((a, b) => b.length - a.length).join('|') + ')(?![\\w-])', f) : null;
      S.terms = { list: r.terms, re: mk(cs, 'g'), reI: mk(ci, 'gi'), byLow: Object.fromEntries(r.terms.map((t) => [t.name.toLowerCase(), t])) };
    } catch (e) { S.terms = { list: [], re: null, reI: null, byLow: {} }; }
    return S.terms;
  }
  // Wrap the first use of each known term inside `root` in a clickable, dotted-underlined span.
  async function linkTerms(root) {
    if (!root) return;
    const T = await loadTerms();
    if (!T.re && !T.reI) return;
    const seen = new Set();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.parentElement.closest('button,a,code,.term,script,style,textarea,input,svg,.no-terms') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((n) => {
      const text = n.nodeValue; const hits = [];
      [T.re, T.reI].forEach((re) => { if (!re) return; re.lastIndex = 0; let m; while ((m = re.exec(text))) hits.push([m.index, m[0]]); });
      hits.sort((a, b) => a[0] - b[0]);
      let pos = 0; const frag = document.createDocumentFragment(); let changed = false;
      hits.forEach(([i, w]) => {
        const t = T.byLow[w.toLowerCase()]; if (!t || i < pos || seen.has(t.name)) return;
        seen.add(t.name); changed = true;
        frag.append(text.slice(pos, i));
        const sp = document.createElement('span'); sp.className = 'term'; sp.tabIndex = 0; sp.dataset.term = t.name; sp.textContent = w; sp.setAttribute('role', 'button');
        frag.append(sp); pos = i + w.length;
      });
      if (changed) { frag.append(text.slice(pos)); n.replaceWith(frag); }
    });
  }
  const defs = {};
  async function showTerm(el) {
    const pop = $('#termpop'); const name = el.dataset.term;
    const place = () => {
      const r = el.getBoundingClientRect(); const w = Math.min(360, window.innerWidth - 24);
      pop.style.width = w + 'px'; pop.style.left = Math.max(12, Math.min(r.left + window.scrollX, window.scrollX + window.innerWidth - w - 12)) + 'px';
      pop.style.top = (r.bottom + window.scrollY + 8) + 'px';
    };
    pop.hidden = false; pop.dataset.for = name; place();
    pop.innerHTML = `<b>${esc(name)}</b>${loading('Reading the documents…')}`;
    try {
      const d = defs[name] || (defs[name] = await api('/api/term?name=' + encodeURIComponent(name)));
      if (pop.dataset.for !== name) return;
      pop.innerHTML = `<div class="tp-h"><b>${esc(d.name)}</b>${d.expansion && d.expansion !== d.name ? `<span class="muted"> · ${esc(d.expansion)}</span>` : ''}</div>
        <p>${esc(d.definition)}</p>
        <div class="meta">${d.general ? '<span class="badge amber" title="The documents use this term without explaining it">General meaning, not defined in the docs</span>' : `<span class="badge green">From ${esc(d.page || 'the docs')}</span>`}<span>used in ${plural(d.docs || 0, 'document')}</span></div>
        ${(d.used_in || []).length ? `<div class="small muted">Read it in: ${d.used_in.slice(0, 3).map((u) => `<button class="linkbtn" data-page="${esc(u.path)}">${esc(u.page)}</button>`).join(' · ')}</div>` : ''}
        <div>${askBtn('T:' + d.name, 'Ask about ' + d.name)}</div>`;
      place();
    } catch (e) { pop.innerHTML = `<b>${esc(name)}</b><div class="small muted">${esc(e.message)}</div>`; }
  }
  document.addEventListener('click', (e) => {
    const t = e.target.closest('.term[data-term]'); const c = e.target.closest('[data-coach]'); const pg = e.target.closest('#termpop [data-page]');
    if (t) { e.preventDefault(); e.stopPropagation(); showTerm(t); return; }
    if (c) { e.preventDefault(); e.stopPropagation(); $('#termpop').hidden = true; openCoach(c.dataset.coach); return; }
    if (pg) { $('#termpop').hidden = true; openPage(pg.dataset.page); return; }
    if (!e.target.closest('#termpop')) $('#termpop').hidden = true;
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.classList && e.target.classList.contains('term') && e.target.dataset.term) showTerm(e.target);
    if (e.key === 'Escape') { $('#termpop').hidden = true; }
  });

  // "Ask about this": a side panel that stays open while you keep looking at the graph
  S.coach = null;
  async function openCoach(node) {
    const panel = $('#coach'); panel.hidden = false; document.body.classList.add('coach-open');
    S.coach = { node, history: [] };
    $('#coach-kind').textContent = 'Ask about this'; $('#coach-title').textContent = '…';
    $('#coach-body').innerHTML = loading('Reading this part of the graph…');
    try {
      const s = await api('/api/explain/suggest?node=' + encodeURIComponent(node));
      if (S.coach.node !== node) return;
      $('#coach-kind').textContent = 'Ask about this ' + s.label.toLowerCase();
      $('#coach-title').textContent = s.title || node;
      $('#coach-body').innerHTML = `<p class="small muted" style="margin:0">Answers come only from this item and what it is connected to in the graph, in plain words, with the jargon explained.</p>
        <div class="chips">${s.suggestions.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div><div id="coach-msgs" class="coach-msgs"></div>`;
      $('#coach-body').querySelectorAll('[data-q]').forEach((b) => b.addEventListener('click', () => coachAsk(b.dataset.q)));
    } catch (e) { $('#coach-body').innerHTML = `<div class="card warn">${esc(e.message)}</div>`; }
    $('#coach-q').focus();
  }
  async function coachAsk(question) {
    question = (question || '').trim(); const C = S.coach; if (!C || !question || C.busy) return;
    C.busy = true; $('#coach-q').value = '';
    const box = $('#coach-msgs');
    box.insertAdjacentHTML('beforeend', `<div class="msg me">${esc(question)}</div><div class="msg bot" id="coach-wait">${loading('Thinking…')}</div>`);
    box.lastElementChild.scrollIntoView({ block: 'end', behavior: 'smooth' });
    try {
      const r = await api('/api/explain', { method: 'POST', body: JSON.stringify({ node: C.node, question, history: C.history }) });
      C.history.push({ role: 'user', content: question }, { role: 'assistant', content: r.answer });
      const order = []; const html = renderAnswer(r.answer, (k) => { if (!order.includes(k)) order.push(k); return order.indexOf(k) + 1; });
      const w = $('#coach-wait'); w.removeAttribute('id');
      w.innerHTML = `<div class="answer">${html}</div>${order.length ? `<div class="small muted">Sources: ${order.map((k, i) => r.sources[k] ? `<button class="linkbtn" data-page="${esc(r.sources[k])}" data-focus="${esc(k)}">${i + 1}. ${esc(k)}</button>` : `${i + 1}. ${esc(k)}`).join(' · ')}</div>` : ''}`;
      w.querySelectorAll('.cite').forEach((b) => b.addEventListener('click', () => { const pth = r.sources[b.dataset.cite]; if (pth) openPage(pth, b.dataset.cite); }));
      bindPageLinks(w); linkTerms(w.querySelector('.answer'));
      w.scrollIntoView({ block: 'end', behavior: 'smooth' });
    } catch (e) { const w = $('#coach-wait'); if (w) { w.removeAttribute('id'); w.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; } }
    C.busy = false;
  }
  $('#coach-f').addEventListener('submit', (e) => { e.preventDefault(); coachAsk($('#coach-q').value); });
  $('#coach-q').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); coachAsk($('#coach-q').value); } });
  $('#coach-close').addEventListener('click', () => { $('#coach').hidden = true; document.body.classList.remove('coach-open'); S.coach = null; });

  // ------------------------------------------------------------------ header
  async function loadHeader() {
    try {
      const [ov, cs] = await Promise.all([api('/api/overview'), api('/api/companies')]);
      S.ov = ov;
      document.title = `HandbookBrain · ${ov.name}`;
      // one company in the UI (the demo dataset); other company configs stay usable from the CLI
      const n = Object.values(ov.review || {}).reduce((a, b) => a + b, 0);
      $('#nav-review').hidden = !n; $('#nav-review').textContent = n;
      paintSync(ov.sync);
    } catch (e) { $('#sync-text').textContent = 'API offline'; }
  }
  function paintSync(sync) {
    $('#sync-pill').classList.toggle('busy', !!(sync && sync.running));
    $('#sync-text').textContent = sync && sync.running ? 'Syncing…' : `${S.ov?.name || ''} · docs as of ${day(S.ov?.as_of)}`;
  }
  if ($('#company')) $('#company').addEventListener('change', async (e) => {
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
  const FEATURES = [
    { k: 'learn', eyebrow: 'For new hires', title: 'Learn an area in an afternoon, not a quarter.', featured: true,
      text: 'A new hire gets one link. They pick their area and ask what they need: the 2-minute overview, what changed recently and why, who to talk to, which tools and metrics they will use. Jargon explains itself where it appears, and anything on screen can be asked about in their own words.',
      points: ['Starts with a question, details only on demand', 'Every term defined from your own documents', '"Ask about this" on any decision, change or document'], go: ['Try onboarding', '#/learn'] },
    { k: 'ask', eyebrow: 'For everyone', title: 'Ask why. Get the trail back to the meeting.',
      text: 'Answers come only from your documents, each sentence cited to an exact section version and checked by a second model. Ask why a rule changed and you get who decided it, in which meeting, the quote, and the MR that wrote it down.',
      points: ['Decision → change → document → answer, as a timeline', 'Answers are watched: they go stale when their source changes', 'The subgraph each answer was read from'], go: ['Ask a question', '#/ask'] },
    { k: 'explore', eyebrow: 'For analysts and leads', title: 'One domain at a time.',
      text: 'The whole company as a graph: documents, owners, decisions, the meetings they came from, and the metrics, dashboards and fields they rely on. Focus on one domain and only its part of the graph is laid out.',
      points: ['Domain focus on every graph', 'Shared systems and metrics connect domains', 'Drill from a document into its versions and changes'], go: ['Explore the graph', '#/explore'] },
    { k: 'impact', eyebrow: 'For owners', title: 'Know what a change affects before it surprises anyone.',
      text: 'When a decision changes a rule, HandbookBrain follows the graph: every answer that relied on the old version, every other document, field, metric and dashboard that uses the same thing, and the people who own them.',
      points: ['Stale answers caught by reading the diff, not the timestamp', 'Live docs vs old PDFs on the drive: conflicts flagged', 'Owners notified with the exact change'], go: ['See a change trail', '#/ask'] },
    { k: 'review', eyebrow: 'For owners', title: 'The agent drafts the update. A person approves it.',
      text: 'A decision made in a meeting that no document reflects gets a drafted, minimal edit, routed to the page owner with its impact. People approve; they do not have to remember to write it down.',
      points: ['Draft, impact and approver in one view', 'Approver found from CODEOWNERS / document owners', 'The next sync closes the loop'], go: ['Open the review queue', '#/review'] },
  ];
  async function vOverview() {
    view.innerHTML = loading();
    const [ov, trust, schema] = await Promise.all([api('/api/overview'), api('/api/trust').catch(() => ({})), api('/api/graph/schema').catch(() => ({ nodes: [], edges: [] }))]);
    S.ov = ov; S.trust = trust;
    const c = ov.counts || {};
    const sc = trust.source_conflicts;
    view.innerHTML = `
      <section class="hero">
        <span class="eyebrow">Company brain · Graph RAG on FalkorDB</span>
        <h1 class="display">All of your company's knowledge in one place, and still true tomorrow.</h1>
        <p class="lede">The decision is in a Zoom call, the rule in the handbook, the old version in a PDF on the drive, the number in a dashboard. HandbookBrain connects them in one versioned graph. New hires learn an area by asking. Anyone can ask why something works the way it does. When something changes, the people who own it are asked to approve the update.</p>
        <div class="actions"><a class="btn primary" href="#/learn">Start as a new hire</a><a class="btn" href="#/ask">Ask a question</a><a class="btn" href="#/explore">Explore the graph</a></div>
      </section>

      <section class="oneplace" aria-label="One place">
        <div class="op-col"><h3 class="label">Where knowledge lives today</h3>
          <div class="chips">${['Meetings', 'Slack and Teams chat', 'Handbook and wikis', 'PDFs on the shared drive', 'CRM fields', 'Dashboards', 'Metrics definitions'].map((x) => `<span class="chip static">${x}</span>`).join('')}</div></div>
        <div class="op-arrow" aria-hidden="true">→</div>
        <div class="op-col op-mid"><h3 class="label">One graph</h3>
          <b class="op-big">${(c.pages ?? 0).toLocaleString()} documents, ${c.decisions ?? 0} decisions</b>
          <div class="op-stats small muted"><span>${(c.sections ?? 0).toLocaleString()} sections · ${(c.versions ?? 0).toLocaleString()} older versions kept</span><span>${c.conversations ?? 0} meetings and chats · synced daily</span></div></div>
        <div class="op-arrow" aria-hidden="true">→</div>
        <div class="op-col"><h3 class="label">Who it serves</h3>
          <ul class="op-who"><li><b>New hires</b> learn an area</li><li><b>Everyone</b> asks, with receipts</li><li><b>Owners</b> approve, not chase</li></ul></div>
      </section>

      ${FEATURES.map((f, i) => `
      <section class="feature ${i % 2 ? 'flip' : ''}">
        <div class="f-media" data-clip="${f.k}"><div class="clip-ph">${loading()}</div></div>
        <div class="f-text"><span class="eyebrow">${esc(f.eyebrow)}</span><h2 class="title">${esc(f.title)}</h2><p>${esc(f.text)}</p>
          <ul class="f-points">${f.points.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
          <div><a class="btn primary" href="${f.go[1]}">${esc(f.go[0])} →</a></div></div>
      </section>`).join('')}

      <section class="section">
        <h3 class="label">The closed loop</h3>
        <div class="loop">
          ${[['01', 'Ingest', 'Business and technical docs (synced daily or uploaded), meetings and chat in their native export formats.'],
             ['02', 'Version + link', 'Docs become versioned sections, linked to the decisions behind them and to the systems, fields, metrics and dashboards they mention.'],
             ['03', 'Answer + teach', 'An agent walks the graph with Cypher tools; every claim cites a section version. New hires learn the same way, by asking.'],
             ['04', 'Detect impact', 'A decision or a change reaches every answer, document, field and dashboard that relies on what changed.'],
             ['05', 'Draft + route', 'The agent drafts the doc update and sends it to the owner who must approve it.'],
             ['06', 'Refresh', 'Once merged, stale answers are re-asked against the new version and SUPERSEDE the old ones.']]
            .map(([n, t, d], i) => `<div class="step ${i === 3 ? 'on' : ''}"><span class="n">${n}</span><b>${t}</b><p>${d}</p></div>`).join('')}
        </div>
      </section>

      <section class="section">
        <div style="display:flex;flex-direction:column;gap:6px"><h3 class="label">The data model, live from FalkorDB</h3></div>
        <div class="graphbox"><div id="g-schema"></div><span class="hint">From the meeting where something was decided, to the change, the section version, the document, the answer and the question · drag nodes</span></div>
        <div class="legend">${HBGraph.legend([...new Set(schema.nodes.map((n) => n.type))].filter((t) => HBGraph.TYPES[t]))}</div>
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
    if (schema.nodes.length) HBGraph.mount($('#g-schema'), schema, { layout: 'layered', height: 360, labels: 'all', label: 'Graph data model' });
    // short clips recorded from the live app (scripts/record_demos.py); click to watch larger
    const clips = await api('/static/media/clips.json').catch(() => ({}));
    view.querySelectorAll('[data-clip]').forEach(async (box) => {
      const src = `/static/media/${box.dataset.clip}.webm`;
      const start = (clips[box.dataset.clip] || {}).start || 0;
      let ok = false; try { ok = (await fetch(src, { method: 'HEAD' })).ok; } catch (e) { /* */ }
      box.innerHTML = ok ? `<video src="${src}" autoplay muted loop playsinline preload="metadata" aria-label="Demo clip"></video><button class="clip-zoom" aria-label="Watch larger">⤢</button>`
        : `<div class="clip-ph"><span class="muted small">Clip not recorded yet · <code>python scripts/record_demos.py</code></span></div>`;
      const v = box.querySelector('video');
      if (v && start) {   // skip the page-load frames at the start of each recording, and loop from there
        v.loop = false;
        v.addEventListener('loadedmetadata', () => { v.currentTime = start; });
        v.addEventListener('ended', () => { v.currentTime = start; v.play(); });
      }
      const z = box.querySelector('.clip-zoom'); if (z) z.addEventListener('click', () => { $('#drawer').hidden = false; $('#drawer-body').innerHTML = `<video src="${src}" controls autoplay muted style="width:100%;border-radius:12px;border:1px solid var(--line)"></video>`; });
    });
  }

  // ------------------------------------------------------------------ learn (onboarding): question first, details on demand
  const PATHS = [
    ['overview', 'Give me the 2-minute overview', 'What this area does and how the work flows'],
    ['decisions', 'What changed recently, and why?', 'Decisions from meetings and chat, and the changes that made them official'],
    ['people', 'Who should I talk to?', 'Owners and the people who make the calls'],
    ['systems', 'Which tools and metrics will I use?', 'Systems, metrics, dashboards and fields'],
    ['reading', 'What should I read first?', 'The most central documents'],
    ['words', 'Words I will hear', 'Jargon, defined from the documents'],
    ['map', 'Show me the map', 'The area as a graph'],
  ];
  async function vLearn() {
    view.innerHTML = loading();
    const { areas } = await api('/api/learn/areas');
    if (!areas.length) { view.innerHTML = '<div class="empty">No documents in the graph yet.</div>'; return; }
    if (!S.area || !areas.some((a) => a.id === S.area)) S.area = areas[0].id;
    const cur = areas.find((a) => a.id === S.area);
    S.learnData = null;
    view.innerHTML = `
      <section class="learn-hero">
        <span class="eyebrow">Learn · ${esc(S.ov?.name || '')}</span>
        <h1 class="display" style="font-size:44px">New to <span class="area-pick"><select id="area" aria-label="Area">${areas.map((a) => `<option value="${esc(a.id)}" ${a.id === S.area ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select></span>?<br>What do you want to understand?</h1>
        <form class="learn-ask" id="learn-f"><label class="sr" for="learn-q">Your question</label>
          <input id="learn-q" autocomplete="off" placeholder="e.g. How does a trial sign-up become a lead for sales?"><button class="btn primary" type="submit">Ask</button></form>
        <div class="chips paths">${PATHS.map(([k, t]) => `<button class="chip" data-path="${k}">${esc(t)}</button>`).join('')}</div>
        <p class="small muted" style="margin:0">Answers come from ${plural(cur.pages, 'document')} in this area and the decisions behind them, kept current by the daily sync. Underlined terms explain themselves; <b>Ask about this</b> works on anything.</p>
      </section>
      <div id="feed" class="feed"></div>`;
    $('#area').addEventListener('change', (e) => { S.area = e.target.value; S.domain = e.target.value; vLearn(); });
    $('#learn-f').addEventListener('submit', (e) => { e.preventDefault(); learnAsk($('#learn-q').value); });
    view.querySelectorAll('[data-path]').forEach((b) => b.addEventListener('click', () => openPath(b.dataset.path)));
  }
  async function areaData() {
    if (!S.learnData) S.learnData = api('/api/learn/area?a=' + encodeURIComponent(S.area));
    return S.learnData;
  }
  // one card per thing the person asked for, newest on top; asking the same thing again just brings it to the top
  function feedCard(key, title) {
    const feed = $('#feed'); let c = feed.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (c) { feed.prepend(c); c.scrollIntoView({ behavior: 'smooth', block: 'start' }); return null; }
    c = document.createElement('section'); c.className = 'card fcard'; c.dataset.key = key;
    c.innerHTML = `<div class="fcard-h"><h3 class="ftitle">${esc(title)}</h3><button class="icon-btn sm" aria-label="Close">×</button></div><div class="fcard-b">${loading()}</div>`;
    c.querySelector('.icon-btn').addEventListener('click', () => c.remove());
    feed.prepend(c); c.scrollIntoView({ behavior: 'smooth', block: 'start' });
    view.querySelectorAll(`[data-path="${key}"]`).forEach((b) => b.classList.add('done'));
    return c.querySelector('.fcard-b');
  }
  // show the first n items; "show more" reveals the rest a few at a time
  function more(items, n, render, step = 5) {
    const id = 'm' + Math.random().toString(36).slice(2, 8);
    setTimeout(() => {
      const box = document.getElementById(id); if (!box) return;
      let shown = n;
      const btn = box.nextElementSibling;
      if (btn) btn.addEventListener('click', () => {
        const next = items.slice(shown, shown + step); shown += next.length;
        box.insertAdjacentHTML('beforeend', next.map(render).join('')); bindPageLinks(box); linkTerms(box);
        if (shown >= items.length) btn.remove(); else btn.textContent = `Show more (${items.length - shown})`;
      });
    });
    return `<div id="${id}" class="mlist">${items.slice(0, n).map(render).join('')}</div>${items.length > n ? `<button class="linkbtn small showmore">Show more (${items.length - n})</button>` : ''}`;
  }
  async function openPath(k) {
    const title = (PATHS.find((p) => p[0] === k) || [])[1];
    const body = feedCard(k, title); if (!body) return;
    try {
      if (k === 'overview') {
        const b = await api('/api/learn/brief?a=' + encodeURIComponent(S.area));
        const parts = b.text.split(/\n(?=\*\*)/).filter((x) => x.trim());
        body.innerHTML = `<div class="answer">${renderAnswer(parts.slice(0, 2).join('\n\n'), () => '')}</div>
          ${parts.length > 2 ? `<details class="more"><summary>Keep reading: recent decisions and your first week</summary><div class="answer">${renderAnswer(parts.slice(2).join('\n\n'), () => '')}</div></details>` : ''}
          <div class="small muted">AI-written from ${plural(b.from.documents, 'document')} and ${plural(b.from.decisions, 'decision')} · docs as of ${esc(day(b.as_of))}</div>`;
      } else {
        const A = await areaData();
        const tcol = { System: 'blue', Metric: 'green', Dashboard: 'violet', Field: '' };
        if (k === 'decisions') {
          await loadTriage();
          const rank = { high: 3, medium: 2, low: 1 };
          A.decisions = [...A.decisions].sort((a, b) => (rank[(S.triage[b.id] || {}).impact] || 0) - (rank[(S.triage[a.id] || {}).impact] || 0) || String(b.at).localeCompare(String(a.at)));
          body.innerHTML = A.decisions.length ? more(A.decisions, 3, (d) => `<details class="ditem"><summary><span class="when">${esc(day(d.at))}</span>${triBadges(d.id)}<span>${esc(d.statement)}</span>${d.status === 'implemented' ? '<span class="badge green">in the docs</span>' : '<span class="badge red">not documented yet</span>'}</summary>
            <div class="det small">${d.by && d.by !== 'unknown' ? 'Decided by <b>' + esc(d.by) + '</b> · ' : ''}${esc(convName(d.src))} · “${esc(d.where)}”${d.mr ? ` · written into the docs ${esc(day(d.implemented_at))} in <a href="${esc(d.mr_url)}" target="_blank" rel="noopener">MR !${esc(d.mr)} ↗</a>` : ''}</div>
            ${triWhy(d.id)}${d.quote ? `<div class="quote">“${esc(d.quote)}”</div>` : ''}
            <div class="evacts">${d.path ? `<button class="linkbtn small" data-page="${esc(d.path)}" ${d.cite ? `data-focus="${esc(d.cite)}"` : ''}>Open ${esc(d.page || 'the document')}</button>` : ''}${askBtn('D:' + d.id)}</div></details>`)
            : '<div class="muted small">No decisions linked to this area yet.</div>';
        } else if (k === 'people') {
          body.innerHTML = A.people.length ? `<ul class="people">${more(A.people, 4, (p) => `<li><span class="avatar">${esc((p.name || '?').replace(/^@/, '').slice(0, 2).toUpperCase())}</span><div style="min-width:0">
            <b>${esc(p.name)}</b>${p.team ? ' <span class="badge">team</span>' : ''}
            <div class="small muted">${p.decided.length ? `made ${plural(p.decided.length, 'decision')} here` : ''}${p.decided.length && p.owns.length ? ' · ' : ''}${p.owns.length ? 'owns ' + esc(p.owns.slice(0, 2).join(', ')) + (p.owns.length > 2 ? ` +${p.owns.length - 2}` : '') : ''}</div>
            ${askBtn((p.handle ? 'O:' + p.handle : 'U:' + p.name), 'What should I ask them about?')}</div></li>`)}</ul>` : '<div class="muted small">No owners on record.</div>';
        } else if (k === 'systems') {
          const ents = A.entities.filter((e) => e.type !== 'Field'); const fields = A.entities.filter((e) => e.type === 'Field');
          body.innerHTML = `<p class="small muted" style="margin:0">Most used first. Click one to ask how it is used here.</p>
            <div class="chips">${more(ents, 8, (e) => `<button class="chip ent" data-coach="${esc('E:' + e.type + ':' + e.name)}" title="${esc(e.type)} · in ${e.docs} documents"><i class="${tcol[e.type]}"></i>${esc(e.type === 'Dashboard' ? (e.label || 'dashboard') : e.name)}<span class="faint">${e.docs}</span></button>`, 8)}</div>
            ${fields.length ? `<details class="more"><summary>Data fields (${fields.length})</summary><div class="small">${fields.map((f) => `<code>${esc(f.name)}</code>`).join(' ')}</div></details>` : ''}`;
        } else if (k === 'reading') {
          body.innerHTML = `<ol class="reads">${more(A.pages, 3, (p) => `<li><div><button class="linkbtn" data-page="${esc(p.path)}"><b>${esc(p.title)}</b></button>
            <div class="meta">${p.inbound ? `<span>linked from ${plural(p.inbound, 'document')}</span>` : ''}${p.changes ? `<span class="badge accent">changed ${p.changes}× recently</span>` : ''}</div></div>${askBtn('P:' + p.path, 'What is this for?')}</li>`)}</ol>`;
        } else if (k === 'words') {
          const g = await api('/api/glossary?area=' + encodeURIComponent(S.area));
          body.innerHTML = `<div class="gloss">${more(g.terms, 6, (t) => `<div class="gterm"><div><span class="term" data-term="${esc(t.name)}" tabindex="0" role="button">${esc(t.name)}</span>${t.expansion && t.expansion !== t.name ? `<span class="muted small"> · ${esc(t.expansion)}</span>` : ''}</div>
            <div class="small ${t.definition ? '' : 'muted'}">${t.definition ? esc(t.definition) : 'Click the term to define it from the docs'}</div><div class="faint small">${esc(t.type.toLowerCase())} · ${t.uses} mentions</div></div>`, 6)}</div>`;
        } else if (k === 'map') {
          body.innerHTML = `<div class="graphbox"><div id="g-learn"></div><div class="nodecard" id="nodecard"><span class="muted">Central documents, what they rely on, who owns them, and the decisions that changed them. Click a node, then <b>Ask about this</b>.</span></div></div>
            <div class="legend">${HBGraph.legend([...new Set(A.graph.nodes.map((n) => n.type))])}</div>`;
          HBGraph.mount($('#g-learn'), A.graph, { height: 460, label: A.name + ' graph', onSelect: (n) => showNode(n) });
        }
      }
      bindPageLinks(body); linkTerms(body);
    } catch (e) { body.innerHTML = `<div class="small" style="color:var(--red)">${esc(e.message)}</div>`; }
  }
  // a free question: the full agent (graph search + decision trail), shown compactly, with the trail one click away
  async function learnAsk(q) {
    q = (q || '').trim(); if (!q || S.asking) return;
    S.asking = true; $('#learn-q').value = '';
    const body = feedCard('q:' + q, q);
    if (!body) { S.asking = false; return; }
    body.innerHTML = loading('Searching the graph and checking every citation… (10–30 s)');
    try {
      const r = await api('/api/ask', { method: 'POST', body: JSON.stringify({ question: q }) });
      S.askResult = r;
      const cites = Object.fromEntries((r.citations || []).map((c) => [c.cite, c]));
      const order = []; const html = renderAnswer(r.answer, (k) => { if (!order.includes(k)) order.push(k); return order.indexOf(k) + 1; });
      body.innerHTML = `<div class="answer">${html}</div>
        ${order.length ? `<details class="more"><summary>Sources (${order.length})</summary><div class="small">${order.map((k, i) => { const c = cites[k] || {}; return `<div>${i + 1}. ${c.path ? `<button class="linkbtn" data-page="${esc(c.path)}" data-focus="${esc(k)}">${esc(c.page || k)} › ${esc(c.heading || '')}</button>` : esc(k)}</div>`; }).join('')}</div></details>` : ''}
        <div class="evacts"><button class="linkbtn small" data-trail="${esc(r.id)}">See the decision trail and graph</button>${askBtn('A:' + r.id, 'Explain this like I am new here')}</div>`;
      body.querySelectorAll('.cite').forEach((b) => b.addEventListener('click', () => { const c = cites[b.dataset.cite]; if (c && c.path) openPage(c.path, b.dataset.cite); }));
      body.querySelector('[data-trail]').addEventListener('click', () => { S.demoId = r.id; location.hash = '#/ask'; });
      bindPageLinks(body); linkTerms(body.querySelector('.answer'));
    } catch (e) { body.innerHTML = `<div class="small" style="color:var(--red)">Could not answer: ${esc(e.message)}</div>`; }
    S.asking = false;
  }

  // ------------------------------------------------------------------ demo
  async function vDemo() {
    view.innerHTML = loading();
    const a = await api('/api/answers?limit=40');
    const sug = a.suggested || [];
    view.innerHTML = `
      <section class="hero" style="padding-top:0;gap:10px">
        <span class="eyebrow">Ask · ${esc(S.ov?.name || '')}</span>
        <h1 class="display" style="font-size:42px">Ask a question. See the decision trail behind the answer.</h1>
      </section>
      <div class="demo">
        <aside class="qlist">
          <form class="askbox" id="askf"><label class="sr" for="q">Question</label><input id="q" autocomplete="off" placeholder="Ask anything…"><button class="btn primary" type="submit">Ask</button></form>
          ${sug.length ? `<h3 class="label" style="margin-top:10px">Try</h3>${sug.slice(0, 3).map((q) => `<button class="qbtn" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}` : ''}
          ${a.answers.length ? `<h3 class="label" style="margin-top:14px">Watched answers</h3>
            ${more(a.answers, 4, (x) => `<button class="qbtn" data-id="${esc(x.id)}" aria-current="${x.id === S.demoId}">${esc(x.question)}
              <span class="meta">${x.status === 'stale' ? '<span class="badge red">stale</span>' : '<span class="badge green">fresh</span>'}${x.checks ? `<span>re-checked ${plural(x.checks, 'time')}</span>` : ''}</span></button>`, 6)}` : ''}
        </aside>
        <section id="story" class="col">${S.demoId ? loading() : `<div class="empty">Pick a question on the left, or ask your own. The answer comes with a timeline of the decisions and changes behind it, and the subgraph it was read from.</div>`}</section>
      </div>`;
    $('#askf').addEventListener('submit', (e) => { e.preventDefault(); ask($('#q').value); });
    view.querySelectorAll('[data-ask]').forEach((b) => b.addEventListener('click', () => { $('#q').value = b.dataset.ask; ask(b.dataset.ask); }));
    $('.qlist').addEventListener('click', (e) => { const b = e.target.closest('[data-id]'); if (b) openStory(b.dataset.id); });
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
        <div class="evacts">${askBtn('A:' + a.id, 'Explain this like I am new here')}</div>
        ${a.stale_reason ? `<div class="small" style="color:var(--red)">Why stale: ${esc(a.stale_reason)}</div>` : ''}
      </div>
      <div class="dbar" id="dbar-story"></div>
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
    domainBar($('#dbar-story'), st.graph, gapi);
    box.querySelectorAll('[data-impact]').forEach((b) => b.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      const holder = b.closest('.evacts').nextElementSibling;
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
    linkTerms(box.querySelector('.answer')); linkTerms(box.querySelector('.tl'));
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
      decision: () => `<div class="what">${esc(e.title)}</div><div class="det">Decided${e.who ? ' by <b>' + esc(e.who) + '</b>' : ''} in ${esc(convName(e.src))} · “${esc(e.where)}”${e.status === 'implemented' ? ` · written into the docs${e.lag != null ? ' ' + e.lag + ' days later' : ''}` : ' · <span style="color:var(--red)">not documented yet</span>'}</div>${e.quote ? `<div class="quote">“${esc(e.quote)}”</div>` : ''}<div>${askBtn(e.node)}</div>`,
      change: () => `<div class="what">${esc(e.title)}</div><div class="det">${esc(e.detail)}${e.mr ? ` · <a href="${esc(e.url)}" target="_blank" rel="noopener">MR !${esc(e.mr)} ↗</a>` : ''}${e.who ? ' · merged by ' + esc(e.who) : ''}${e.author ? ' · author ' + esc(e.author) : ''}</div>${e.diff ? diff(e.diff) : ''}${e.sha ? `<div class="evacts"><button class="linkbtn small" data-impact="${esc(e.sha)}">What did this change affect?</button>${askBtn(e.node)}</div><div class="impact"></div>` : ''}`,
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
      ${n.quote ? `<div class="quote">“${esc(n.quote)}”</div>` : ''}${n.text ? docBlock(n.text, 'short') : ''}
      ${n.url && /^https?:/.test(n.url) ? `<a class="small" href="${esc(n.url)}" target="_blank" rel="noopener">Open source ↗</a>` : ''}
      <div class="evacts">${n.type === 'Page' && n.path ? `<button class="linkbtn small" data-page="${esc(n.path)}">Open document and its history</button>` : ''}${askBtn(n.type === 'Page' && n.path ? 'P:' + n.path : n.id)}</div>`;
    bindPageLinks(card); linkTerms(card); bindDocs(card);
  }

  // domain focus: one business area highlighted on any graph (nodes carry `domains` from the API)
  const DCOL = ['#C96442', '#3E63DD', '#2E8B62', '#7C5CD6', '#B07A12', '#C2412D', '#0E7C86', '#6B6966'];
  function domainBar(holder, graph, gapi, note) {
    const ds = graph.domains || [];
    if (!holder) return;
    if (ds.length < 2) { holder.innerHTML = ''; return; }
    const paint = () => {
      holder.innerHTML = `<span class="dlabel">Focus on a domain</span><button class="dchip" aria-pressed="${!S.domain}" data-dom="">All</button>${ds.map((d, i) => `<button class="dchip" aria-pressed="${S.domain === d.id}" data-dom="${esc(d.id)}"><i style="background:${DCOL[i % DCOL.length]}"></i>${esc(d.name)}<span class="faint">${d.nodes}</span></button>`).join('')}`;
      holder.querySelectorAll('[data-dom]').forEach((b) => b.addEventListener('click', () => { S.domain = b.dataset.dom || null; paint(); apply(); }));
    };
    const apply = () => {
      if (S.domain && !ds.some((d) => d.id === S.domain)) S.domain = null;
      const n = gapi.focus(S.domain);
      if (note) note(S.domain ? { name: ds.find((d) => d.id === S.domain).name, n, nodes: graph.nodes.filter((x) => (x.domains || []).includes(S.domain)) } : null);
    };
    paint(); apply();
  }
  function domainSummary(f) {
    if (!f) return '';
    const by = {}; f.nodes.forEach((x) => { (by[x.type] = by[x.type] || 0); by[x.type]++; });
    const order = ['Page', 'Decision', 'Metric', 'Dashboard', 'System', 'Field', 'Person', 'Team', 'Conversation', 'Change', 'Section'];
    return `<b>${esc(f.name)}</b>: ` + order.filter((t) => by[t]).map((t) => `${by[t]} ${esc(HBGraph.typeOf(t).name.toLowerCase())}${by[t] === 1 ? '' : 's'}`).join(' · ');
  }


  // decision triage: kind (business rule / process / system & data) + impact computed from the graph
  const KCOL = { 'Business rule': 'accent', Process: 'blue', 'System & data': 'violet' };
  const ILAB = { high: 'High impact', medium: 'Medium impact', low: 'Low impact' };
  async function loadTriage() {
    if (!S.triageP) S.triageP = api('/api/decisions/triage').then((r) => (S.triage = r.triage)).catch(() => (S.triage = {}));
    return S.triageP;
  }
  const triBadges = (id) => { const t = (S.triage || {})[id]; return t ? `<span class="badge ${KCOL[t.kind] || ''}" title="${esc(t.kind_why)}">${esc(t.kind)}</span><span class="imp ${t.impact}" title="Impact score ${t.score}, computed from the graph"><i></i><i></i><i></i>${ILAB[t.impact]}</span>` : ''; };
  const triWhy = (id) => { const t = (S.triage || {})[id]; return t && t.why.length ? `<div class="why">Why: ${esc(t.why.join(' · '))}</div>` : ''; };
  // ------------------------------------------------------------------ explore
  async function vExplore(focusPath) {
    view.innerHTML = loading('Loading the graph…');
    const [g0, up] = await Promise.all([focusPath ? api('/api/graph/page?path=' + encodeURIComponent(focusPath)) : api('/api/graph/explore'), api('/api/updates?limit=200')]);
    // Sources are hubs that would pull every page into one ball: shown as the page's ring colour instead
    const g = focusPath ? g0 : { nodes: g0.nodes.filter((n) => n.type !== 'Source'), edges: g0.edges.filter((e) => e.type !== 'PROVIDES'), domains: g0.domains };
    const types = [...new Set(g.nodes.map((n) => n.type))];
    const hidden = new Set();
    view.innerHTML = `
      <section class="hero" style="padding-top:0;gap:10px">
        <span class="eyebrow">Explore · ${esc(S.ov?.name || '')}</span>
        <h1 class="display" style="font-size:42px">${focusPath ? 'One document, and everything connected to it' : 'The company, as a graph'}</h1>
        <p class="lede">${focusPath ? '<button class="linkbtn" id="back">← Company graph</button> · sections (current and superseded), the changes that made them, decisions about them.' : 'Documents clustered by the team that owns them, and the decisions from meetings and chat that changed them. Amber rings: static files (uploaded PDFs, DOCX). Red dashed lines: sources that disagree. Click a document to drill in.'}</p>
      </section>
      <div class="dbar" id="dbar-explore"></div>
      <div class="row" style="align-items:center;gap:10px">
        <div class="filters">${types.map((t) => { const x = HBGraph.typeOf(t); return `<button class="fchip" aria-pressed="true" data-t="${esc(t)}"><i style="background:${x.c}"></i>${esc(x.name)} · ${g.nodes.filter((n) => n.type === t).length}</button>`; }).join('')}</div>
        <div class="grow"></div><input class="search" id="find" placeholder="Find a node…" aria-label="Find a node">
      </div>
      <div class="graphbox"><div id="g-explore"></div><div class="tools"><button class="btn" id="reset" style="height:32px">Reset</button></div>
        <div class="nodecard" id="nodecard"><span class="muted">Click a node to see what it is and how it connects. Drag to rearrange · ⌘/Ctrl + scroll to zoom.</span></div></div>
      <section class="section"><div class="row" style="align-items:baseline;gap:10px"><h3 class="label">Decision timeline</h3><span class="muted small" id="dtl-note"></span></div>
        <p class="small muted" style="margin:0">The agent labels each decision by what it changes, and ranks its impact from the graph: the metrics, fields and dashboards it touches, how many other documents rely on them, the answers given from it, and the domains and owners involved.</p>
        <div class="dbar" id="dtl-filters"></div><div class="card"><ol class="tl" id="dtl"></ol></div></section>`;
    const gapi = HBGraph.mount($('#g-explore'), g, { height: 620, label: 'Company graph', onSelect: (n) => {
      showNode(n);
      document.querySelectorAll('#dtl .ev').forEach((x) => x.classList.toggle('on', x.dataset.node === n.id));
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
    $('#reset').addEventListener('click', () => { S.domain = null; gapi.reset(); domainBar($('#dbar-explore'), g0, gapi, focusNote); });
    const focusNote = (f) => { decisionTimeline(g); const c = $('#nodecard'); if (c) c.innerHTML = f ? `<span>${domainSummary(f)}</span><span class="muted small">Only this domain is laid out. Shared systems such as a CRM appear in every domain that uses them.</span>` : '<span class="muted">Click a node to see what it is and how it connects. Drag to rearrange · ⌘/Ctrl + scroll to zoom.</span>'; };
    domainBar($('#dbar-explore'), g0, gapi, focusNote);
    const back = $('#back'); if (back) back.addEventListener('click', () => vExplore());
    S.exploreSel = (id) => { gapi.select(id); const n = g.nodes.find((x) => x.id === id); if (n) showNode(n); };
    decisionTimeline(g);
  }
  // decisions only (what was decided, by whom, where, and the change that made it official), newest first,
  // following the domain focus; click one to find it in the graph above
  function decisionTimeline(g) {
    const box = $('#dtl'); if (!box) return;
    const F = S.dtl || (S.dtl = { kind: 'all', sort: 'impact' });
    const T = S.triage || {};
    const rank = { high: 3, medium: 2, low: 1 };
    let ds = g.nodes.filter((n) => n.type === 'Decision' && (!S.domain || (n.domains || []).includes(S.domain)));
    const all = ds.length;
    if (F.kind !== 'all') ds = ds.filter((d) => F.kind === 'high' ? (T[d.id.slice(2)] || {}).impact === 'high' : (T[d.id.slice(2)] || {}).kind === F.kind);
    ds.sort((a, b) => F.sort === 'impact' && S.triage ? (rank[(T[b.id.slice(2)] || {}).impact] || 0) - (rank[(T[a.id.slice(2)] || {}).impact] || 0) || ((T[b.id.slice(2)] || {}).score || 0) - ((T[a.id.slice(2)] || {}).score || 0) : String(b.at || '').localeCompare(String(a.at || '')));
    const dname = S.domain && (g.domains || []).find((d) => d.id === S.domain);
    const cnt = (f) => g.nodes.filter((n) => n.type === 'Decision' && (!S.domain || (n.domains || []).includes(S.domain)) && f(T[n.id.slice(2)] || {})).length;
    $('#dtl-note').textContent = `${plural(all, 'decision')}${dname ? ' in ' + dname.name : ''} · click one to find it in the graph`;
    $('#dtl-filters').innerHTML = !S.triage ? `<span class="small muted"><span class="spinner" style="display:inline-block;vertical-align:-3px"></span> Reading the graph to rank decisions by impact…</span>` :
      `${[['all', 'All'], ['high', 'High impact'], ...['Business rule', 'Process', 'System & data'].map((k) => [k, k])].map(([k, l]) => `<button class="dchip" aria-pressed="${F.kind === k}" data-dk="${esc(k)}">${esc(l)}${k !== 'all' ? `<span class="faint">${cnt(k === 'high' ? (t) => t.impact === 'high' : (t) => t.kind === k)}</span>` : ''}</button>`).join('')}
       <span class="grow"></span><span class="small muted">Sort</span><button class="dchip" aria-pressed="${F.sort === 'impact'}" data-ds="impact">Biggest impact</button><button class="dchip" aria-pressed="${F.sort === 'newest'}" data-ds="newest">Newest</button>`;
    $('#dtl-filters').querySelectorAll('[data-dk]').forEach((b) => b.addEventListener('click', () => { F.kind = b.dataset.dk; decisionTimeline(g); }));
    $('#dtl-filters').querySelectorAll('[data-ds]').forEach((b) => b.addEventListener('click', () => { F.sort = b.dataset.ds; decisionTimeline(g); }));
    box.innerHTML = ds.length ? more(ds, 5, (d) => `<li class="ev decision ${d.status === 'implemented' ? '' : 'open'}" data-node="${esc(d.id)}"><span class="pin"></span><div class="when">${esc(day(d.at))}</div>
      <div class="tags">${triBadges(d.id.slice(2))}</div>
      <div class="what">${esc(d.label)}</div>
      <div class="det">${d.by && d.by !== 'unknown' ? 'Decided by <b>' + esc(d.by) + '</b> · ' : ''}${esc(convName(d.src))}${d.where ? ' · “' + esc(d.where) + '”' : ''} · ${d.status === 'implemented' ? `written into the docs${d.done_at ? ' ' + esc(day(d.done_at)) : ''}${d.mr ? ` in <a href="${esc(d.mr_url)}" target="_blank" rel="noopener">MR !${esc(d.mr)} ↗</a>` : ''}` : '<span style="color:var(--red)">not documented yet</span>'}</div>
      ${triWhy(d.id.slice(2))}</li>`, 5) : '<li class="muted small">No decisions match.</li>';
    box.onclick = (e) => {
      const li = e.target.closest('[data-node]'); if (!li || e.target.closest('a')) return;
      box.querySelectorAll('.ev').forEach((x) => x.classList.toggle('on', x === li));
      S.exploreSel(li.dataset.node); $('#g-explore').scrollIntoView({ behavior: 'smooth', block: 'center' });
    };
    if (!S.triage) loadTriage().then(() => decisionTimeline(g));
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
    const [r, pr] = await Promise.all([api('/api/review'), api('/api/proposals').catch(() => ({ proposals: [] })), loadTriage()]);
    const rank = { high: 3, medium: 2, low: 1 };
    r.decided_not_documented.sort((a, b) => (rank[(S.triage[b.decision] || {}).impact] || 0) - (rank[(S.triage[a.decision] || {}).impact] || 0));
    r.proposals = pr.proposals;
    S.props = Object.fromEntries(pr.proposals.map((p) => [p.id, p]));
    const groups = [
      ['stale_answers', 'Answers that went stale', 'A document an earlier answer relied on changed, or a more authoritative source contradicts it. Owners were notified; refresh re-answers from the current version.',
        (a) => `<td><b>${esc(a.question)}</b><div class="small muted">${esc(a.reason)}</div></td><td class="small">${a.kind === 'source_conflict' ? '<span class="badge amber">sources disagree</span>' : '<span class="badge">doc changed</span>'}<div class="muted">${esc(day(a.since))}</div></td><td class="small muted">${esc((a.notified || []).join(', '))}</td><td><button class="linkbtn small" data-story="${esc(a.id)}">Trail</button></td>`],
      ['source_conflicts', 'Sources that disagree', 'A static file (e.g. an old PDF on the shared drive) says something different from the live, more authoritative page.',
        (c) => `<td><b>${esc(c.topic)}</b><div class="small muted">${esc(c.reason)}</div></td><td class="small"><div>outdated <code>${esc(c.outdated)}</code></div><div>wins <code>${esc(c.authoritative)}</code></div></td><td class="small muted">${esc((c.notify || []).join(', ') || 'no owner')}</td><td></td>`],
      ['decided_not_documented', 'Decided, but not documented', 'Decisions made in a meeting or chat that no document change implemented after three weeks.',
        (d) => `<td><div class="tags">${triBadges(d.decision)}</div><b>${esc(d.statement)}</b><div class="small muted">${esc(convName(d.source))} · ${esc(d.meeting)} · ${esc(day(d.decided_at))}</div>${triWhy(d.decision)}</td><td class="small"><span class="badge accent">${Math.round(d.days_open)} days open</span><div class="muted"><code>${esc(d.section || '')}</code></div></td><td class="small muted">${esc([...(d.page_owners || []), ...(d.assignees || [])].join(', '))}</td><td>${d.section ? `<button class="btn" style="height:32px" data-draft="${esc(d.decision)}">Draft update</button>` : ''}</td>`],
      ['proposals', 'Drafted updates', 'Updates the agent drafted from decisions. Only a page owner\'s sign-off is final; anyone else\'s approval is recorded as an endorsement.',
        (p) => `<td><b>${esc(p.summary || p.decision)}</b><div class="small muted">${esc(p.page)} › ${esc(p.heading)} · from “${esc(p.where || '')}”</div></td><td class="small"><span class="badge ${p.status === 'approved' ? 'green' : p.status === 'rejected' ? 'red' : 'accent'}">${p.status === 'pending' ? 'waiting for owner' : esc(p.status)}</span>${p.status !== 'pending' && p.decided_by ? `<div class="muted">by owner ${esc(p.decided_by)}</div>` : ''}${p.status === 'pending' && p.endorsements ? `<div class="muted">${p.endorsements} endorsement${p.endorsements === 1 ? '' : 's'}</div>` : ''}</td><td class="small muted">approver: ${esc((p.approvers || []).join(', ') || 'no owner')}</td><td><button class="linkbtn small" data-prop="${esc(p.id)}">Open</button></td>`],
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
    view.querySelectorAll('[data-story]').forEach((b) => b.addEventListener('click', () => { S.demoId = b.dataset.story; location.hash = '#/ask'; }));
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
    const approvers = (p.approvers || []).map((a) => (typeof a === 'string' ? a : a.name || a.handle)).filter(Boolean);
    const signs = p.signoffs || [];
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
        <div class="small">${approvers.length ? `Final approval comes from an owner of this page: <b>${esc(approvers.join(', '))}</b> (CODEOWNERS / the document's owner field).` : 'No owner on record for this page: the first sign-off decides. Route it to the team lead.'}
          ${approvers.length ? ' Anyone else can approve or object: it is recorded and shown to the owner, but the update stays <b>pending until an owner signs off</b>.' : ''}</div>
        ${signs.length ? `<ul class="signs">${signs.map((x) => `<li><span class="badge ${x.vote === 'approve' ? 'green' : 'red'}">${x.vote === 'approve' ? 'approved' : 'objected'}</span> <b>${esc(x.by)}</b> <span class="muted">· ${esc(x.role)} · ${esc(day(x.at))}</span></li>`).join('')}</ul>` : ''}
        ${(p.status || 'pending') === 'pending' ? `
          ${signs.some((x) => x.role === 'not an owner') ? `<div class="small"><span class="badge accent">Waiting for an owner</span> ${p.endorsements || 0} endorsement${p.endorsements === 1 ? '' : 's'}${p.objections ? `, ${p.objections} objection${p.objections === 1 ? '' : 's'}` : ''} so far.</div>` : ''}
          <div class="signrow"><label class="small muted" for="signas">Sign as</label>
            <select id="signas" class="search" style="min-width:200px">${approvers.map((a) => `<option value="${esc(a)}">${esc(a)} (owner)</option>`).join('')}<option value="__other">Someone else…</option></select>
            <input class="search" id="approver" placeholder="Your name" style="min-width:160px" ${approvers.length ? 'hidden' : ''}>
            <button class="btn primary" id="approve">Approve</button><button class="btn" id="reject">Reject</button></div>
          <div class="small muted">Approving records who signed off and produces the patch to merge; the document itself changes in its own system, and the next sync closes the loop. (Demo: sign-in would come from SSO.)</div>` :
          `<div class="small"><span class="badge ${p.status === 'approved' ? 'green' : 'red'}">${esc(p.status)}</span> by owner <b>${esc(p.decided_by || '')}</b> ${esc(day(p.decided_at))} · <button class="linkbtn small" id="undo">Undo (demo)</button></div>`}
        <div id="signmsg" class="small"></div></div>`;
    bindPageLinks($('#drawer-body'));
    const act = async (status) => {
      const sel = $('#signas'); const who = !sel || sel.value === '__other' ? ($('#approver').value || '').trim() : sel.value;
      if (status !== 'pending' && !who) { $('#signmsg').innerHTML = '<span style="color:var(--red)">Enter your name to sign off.</span>'; return; }
      try {
        const r = await api('/api/proposals/' + encodeURIComponent(p.id), { method: 'POST', body: JSON.stringify({ status, by: who }) });
        openProposal({ ...p, ...r }, false);
        const last = (r.signoffs || []).slice(-1)[0];
        if (status !== 'pending' && last && last.role === 'not an owner') $('#signmsg').innerHTML = `<span class="badge accent">Recorded</span> ${esc(who)} is not an owner of this page, so this counts as ${status === 'approved' ? 'an endorsement' : 'an objection'}. The update stays pending until ${esc(approvers.join(' or '))} signs off.`;
        if (location.hash.includes('review')) vReview();
      } catch (e) { $('#signmsg').innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
    };
    const sel = $('#signas'); if (sel) sel.addEventListener('change', () => { $('#approver').hidden = sel.value !== '__other'; if (sel.value === '__other') $('#approver').focus(); });
    if ($('#approve')) { $('#approve').addEventListener('click', () => act('approved')); $('#reject').addEventListener('click', () => act('rejected')); }
    if ($('#undo')) $('#undo').addEventListener('click', () => act('pending'));
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
  function bindPageLinks(root) { root.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => openPage(b.dataset.page, b.dataset.focus))); }
  async function openPage(path, focus) {
    const dr = $('#drawer'); dr.hidden = false;
    $('#drawer-body').innerHTML = loading(); $('#drawer-close').focus();
    try {
      const [d, g] = await Promise.all([api('/api/page?path=' + encodeURIComponent(path)), api('/api/graph/page?path=' + encodeURIComponent(path))]);
      const det = d.details || {};
      $('#drawer-body').innerHTML = `
        <h2 id="drawer-title" class="title">${esc(det.title || path)}</h2>
        <div class="evacts">${askBtn('P:' + path, 'Ask about this document')}</div>
        <div class="meta"><code>${esc(path)}</code>${det.url && /^https?:/.test(det.url) ? `<a href="${esc(det.url)}" target="_blank" rel="noopener">Open original ↗</a>` : ''}</div>
        <div class="meta">${(det.owners || []).map((o) => `<span class="badge green">${esc(String(o).split('|').pop())}</span>`).join('') || '<span class="badge red">No owner</span>'}</div>
        <div class="graphbox"><div id="g-page"></div></div>
        ${d.sections.map((s) => `<details class="card${focus && s.cite === focus ? ' focus' : ''}" data-cite="${esc(s.cite)}" ${s.outdated_by.length || s.versions || (focus && s.cite === focus) ? 'open' : ''}>
          <summary style="cursor:pointer;font-weight:600">${esc(s.heading)} ${s.versions ? `<span class="badge blue">${s.versions + 1} versions</span>` : ''}${s.outdated_by.length ? ' <span class="badge red">outdated copy</span>' : ''}${s.older_copies.length ? ' <span class="badge amber">older copies disagree</span>' : ''}</summary>
          <div class="small muted evacts">this version since ${esc(day(s.since))} · <code>${esc(s.cite)}</code>${askBtn('cite:' + s.cite, 'Ask about this section')}</div>
          ${docBlock(s.text)}
          ${(s.history || []).filter((h) => h.diff && !h.diff.startsWith('(first')).map((h) => `<div class="small"><b>${esc(day(h.since))}</b> ${esc(h.change || '')} ${h.mr_url ? `<a href="${esc(h.mr_url)}" target="_blank" rel="noopener">MR !${esc(h.mr)} ↗</a>` : ''}
            <div class="diff">${h.diff.split('\n').map((l) => l.startsWith('REMOVED') ? `<div class="d">− ${esc(l.replace(/^REMOVED \(old text\):\s*/, ''))}</div>` : `<div class="a">+ ${esc(l.replace(/^ADDED \(new text\):\s*/, ''))}</div>`).join('')}</div></div>`).join('')}
        </details>`).join('')}`;
      if (g.nodes.length) { const pg = HBGraph.mount($('#g-page'), g, { layout: 'layered', height: 300, label: 'Document graph' }); if (S.domain) pg.focus(null); }
      $('#drawer-body').querySelectorAll('.doc').forEach((x) => linkTerms(x));
      $('#drawer-body').querySelectorAll('details').forEach((x) => x.addEventListener('toggle', () => bindDocs(x)));
      bindDocs($('#drawer-body'));
      const f = focus && $('#drawer-body').querySelector('details.focus');
      if (f) setTimeout(() => f.scrollIntoView({ block: 'start', behavior: 'smooth' }), 60);
    } catch (e) { $('#drawer-body').innerHTML = `<div class="card warn">${esc(e.message)}</div>`; }
  }
  const closeDrawer = () => { $('#drawer').hidden = true; };
  $('#drawer-close').addEventListener('click', closeDrawer);
  $('#drawer').addEventListener('click', (e) => { if (e.target.id === 'drawer') closeDrawer(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#drawer').hidden) closeDrawer(); });

  // ------------------------------------------------------------------ router
  async function route() {
    let r = (location.hash.replace(/^#\/?/, '') || 'overview').split('?')[0];
    if (r === 'demo') r = 'ask';
    document.querySelectorAll('[data-nav]').forEach((a) => { if (a.dataset.nav === r) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    try {
      if (r === 'learn') await vLearn();
      else if (r === 'demo' || r === 'ask') await vDemo();
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
