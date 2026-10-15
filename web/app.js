/* HandbookBrain web UI: plain JS, no build step. Every view reads the FastAPI layer (hb/api.py). */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const view = $('#view');
  const state = { overview: null, last: null, tab: 'review', asking: false };

  // ------------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const day = (ts) => {
    if (!ts) return '';
    const d = new Date(String(ts).replace(' ', 'T'));
    return isNaN(d) ? String(ts).slice(0, 10) : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  };
  const pct = (x) => (x == null ? '–' : Math.round(x * 100) + '%');
  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}, ...opts });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) { /* not json */ }
      throw new Error(msg);
    }
    return r.json();
  }
  const sourceBadge = (src, kind, uploaded) => {
    if (!src) return '';
    const label = kind === 'static' ? `${esc(src)} · static${uploaded ? ' · uploaded ' + esc(day(uploaded)) : ''}` : `${esc(src)} · live`;
    return `<span class="badge ${kind === 'static' ? 'orange' : 'blue'}">${label}</span>`;
  };
  const ownerBadge = (ownerSource, owners) => {
    const who = (owners || []).filter(Boolean).map((o) => String(o).split('|').pop()).join(', ');
    if (ownerSource === 'explicit') return `<span class="badge green" title="Declared in the document or CODEOWNERS">Owner: ${esc(who)}</span>`;
    if (ownerSource === 'inferred') return `<span class="badge orange" title="Only the file author is known, nobody declared ownership">Owner inferred: ${esc(who)}</span>`;
    return '<span class="badge red" title="Nobody owns this document">No owner</span>';
  };
  const convIcon = (src) => ({ zoom: 'Zoom', zoom_captions: 'Zoom', teams: 'Teams', meet: 'Meet', slack: 'Slack', teams_chat: 'Teams chat',
    gchat: 'Google Chat', whatsapp: 'WhatsApp', discord: 'Discord', email: 'Email', jira: 'Jira', zendesk: 'Zendesk', notes: 'Notes' }[src] || src || 'Conversation');
  const TOOL = {
    search_handbook: 'Searched the docs', page_details: 'Opened a page', section_history: 'Read the version history',
    why_changed: 'Traced why it changed', decisions_about: 'Looked up decisions', who_knows: 'Found who knows',
    recent_changes: 'Listed recent changes', search_conversations: 'Searched meetings and chat', run_cypher: 'Ran a graph query',
  };

  // tiny markdown: paragraphs, "- " lists, **bold**, and [cite#keys] -> numbered chips
  function renderAnswer(text, cites) {
    const num = new Map();
    const order = [];
    const keyOf = (k) => k.trim();
    const chip = (k) => {
      k = keyOf(k);
      if (!num.has(k)) { num.set(k, num.size + 1); order.push(k); }
      return `<a class="cite" href="#src-${num.get(k)}" title="${esc(k)}">${num.get(k)}</a>`;
    };
    let html = esc(text).replace(/\[([^\[\]]+#[^\[\]]+)\]/g, (_, g) => g.split(/[,;]\s*/).filter((x) => x.includes('#')).map(chip).join(''));
    html = html.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    const blocks = html.split(/\n{2,}/).map((b) => {
      const lines = b.split('\n');
      if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) return '<ul>' + lines.map((l) => `<li>${l.replace(/^\s*([-*]|\d+\.)\s+/, '')}</li>`).join('') + '</ul>';
      return `<p>${lines.join('<br>')}</p>`;
    });
    const byKey = Object.fromEntries((cites || []).map((c) => [c.cite, c]));
    return { html: blocks.join(''), sources: order.map((k, i) => ({ n: i + 1, key: k, ...(byKey[k] || {}) })) };
  }

  // ------------------------------------------------------------------ header
  async function loadHeader() {
    try {
      const [ov, cs] = await Promise.all([api('/api/overview'), api('/api/companies')]);
      state.overview = ov;
      document.title = `HandbookBrain · ${ov.name}`;
      $('#company').innerHTML = cs.companies.map((c) => `<option value="${esc(c.id)}" ${c.id === cs.current ? 'selected' : ''}>${esc(c.name)}</option>`).join('');
      const n = Object.values(ov.review || {}).reduce((a, b) => a + b, 0);
      const nr = $('#nav-review');
      nr.hidden = !n; nr.textContent = n;
      paintSync(ov.sync, ov);
    } catch (e) {
      $('#sync-text').textContent = 'API offline';
    }
  }
  function paintSync(sync, ov) {
    const pill = $('#sync-pill');
    pill.classList.toggle('busy', !!(sync && sync.running));
    const synced = (ov?.sources || []).map((s) => s.last_synced).filter(Boolean).sort().pop();
    $('#sync-text').textContent = sync && sync.running ? 'Syncing…' : `Docs as of ${day(ov?.as_of)}${synced ? ' · synced ' + day(synced) : ''}`;
  }
  $('#company').addEventListener('change', async (e) => {
    const name = e.target.value;
    view.innerHTML = `<div class="thinking"><div class="spinner"></div>Switching to ${esc(e.target.selectedOptions[0].text)}…</div>`;
    try { await api('/api/switch', { method: 'POST', body: JSON.stringify({ name }) }); } catch (err) { /* restarting */ }
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 500));
      try { const c = await api('/api/companies'); if (c.current === name) { location.hash = '#/ask'; location.reload(); return; } } catch (err) { /* still restarting */ }
    }
    view.innerHTML = '<div class="empty">The server did not come back. Restart it with <code>python -m hb --company ' + esc(name) + ' serve</code>.</div>';
  });

  // ------------------------------------------------------------------ ask
  const EXAMPLES = {
    larkspur: ['If I get an OpsAlert notification, how long do I have to respond?', 'What is the maximum relocation reimbursement per move?',
      'Why did the remote work stipend approver change, and who decided it?', 'I lost my building badge. What do I do?'],
    gitlab: ['How much is the 401(k) match now, and when did it change?', 'Who owns the paid time off page?',
      'What changed in the total rewards pages since September?', 'Why did the 401(k) match change?'],
  };
  function viewAsk() {
    const ex = EXAMPLES[state.overview?.company] || EXAMPLES.larkspur;
    view.innerHTML = `
      <div class="qhead"><h1 class="display">Ask how ${esc(state.overview?.name || 'the company')} works</h1>
        <p class="lede">Answers come only from the company's own documents, with a citation on every claim. When a document changes, the answers that relied on it are re-checked.</p></div>
      <form class="askbox" id="askf"><label class="sr" for="q">Question</label>
        <input id="q" autocomplete="off" placeholder="Ask a policy question…" value="${esc(state.last?.question || '')}">
        <button class="btn primary" type="submit">Ask</button></form>
      <div class="chips">${ex.map((q) => `<button type="button" class="chip">${esc(q)}</button>`).join('')}</div>
      <div id="result"></div>`;
    $('#askf').addEventListener('submit', (e) => { e.preventDefault(); doAsk($('#q').value); });
    view.querySelectorAll('.chip').forEach((b) => b.addEventListener('click', () => { $('#q').value = b.textContent; doAsk(b.textContent); }));
    if (state.last) paintAnswer(state.last);
  }
  async function doAsk(q) {
    q = (q || '').trim();
    if (!q || state.asking) return;
    state.asking = true;
    $('#result').innerHTML = '<div class="card"><div class="thinking"><div class="spinner"></div>Searching the graph, checking citations, and having an independent reviewer check the answer…</div></div>';
    try {
      state.last = await api('/api/ask', { method: 'POST', body: JSON.stringify({ question: q }) });
      paintAnswer(state.last);
    } catch (e) {
      $('#result').innerHTML = `<div class="card warn">Could not answer: ${esc(e.message)}</div>`;
    } finally { state.asking = false; }
  }
  function paintAnswer(r) {
    const { html, sources } = renderAnswer(r.answer, r.citations);
    const steps = r.steps.map((s, i) => `<li><span class="stepn">${i + 1}</span><div><div class="step-t">${esc(TOOL[s.tool] || s.tool)}</div>
        <code>${esc(Object.values(s.args || {}).join(' · '))}</code> <span class="muted small">${s.n ?? 0} sources${s.error ? ' · error' : ''}</span></div></li>`).join('');
    const revs = (r.revisions || []).map((v) => `<li><span class="stepn rev">↻</span><div><div class="step-t">Revised after ${esc(v.stage === 'critic' ? 'independent review' : v.stage === 'abstain_guard' ? 'giving up too early' : 'citation check')}</div>
        <div class="small muted">${esc((v.issues || []).join(' '))}</div></div></li>`).join('');
    $('#result').innerHTML = `
      <div class="row">
        <section class="main-col" aria-label="Answer">
          <div class="card hl">
            <div class="meta">${r.answerable ? '<span class="badge green">Answered from the docs</span>' : '<span class="badge orange">Not in the docs</span>'}
              <span>docs as of ${esc(day(r.as_of))}</span><span>· ${r.secs}s · $${(r.cost_usd || 0).toFixed(3)}</span></div>
            <div class="answer">${html}</div>
          </div>
          <h2 class="label">Sources</h2>
          ${sources.length ? sources.map((s) => `
            <div class="src" id="src-${s.n}">
              <div class="src-h"><span class="src-n">${s.n}</span><b>${esc(s.page || s.key)}</b><span class="muted">› ${esc(s.heading || '')}</span></div>
              <div class="meta">${sourceBadge(s.source, s.kind, s.uploaded_on)} ${s.owner_source ? ownerBadge(s.owner_source, s.owners) : ''}
                ${s.since ? `<span>this version since ${esc(day(s.since))}</span>` : ''}
                ${s.path ? `<button class="linkbtn small" data-page="${esc(s.path)}">Open page and history</button>` : ''}</div>
              ${s.text ? `<div class="quote">${esc(s.text)}</div>` : ''}
            </div>`).join('') : '<div class="empty">No citations.</div>'}
        </section>
        <aside class="side-col">
          <div class="card"><h2 class="label">How I got here</h2><ol class="steps">${steps}${revs}
            <li><span class="stepn">✓</span><div><div class="step-t">Checked every citation</div><div class="small muted">Each cited key points to an exact section version in the graph. If that section changes, this answer is re-checked.</div></div></li></ol></div>
        </aside>
      </div>`;
    bindPageLinks($('#result'));
  }

  // ------------------------------------------------------------------ updates + review
  async function viewUpdates() {
    view.innerHTML = `
      <div class="qhead"><h1 class="display">Updates</h1><p class="lede">What changed in the documents, and what needs a human: answers that went stale, decisions nobody wrote down, sources that disagree, questions with no answer.</p></div>
      <div class="tabs" role="tablist">
        <button role="tab" data-tab="review" aria-selected="${state.tab === 'review'}">Needs review <span class="count" id="rv-n"></span></button>
        <button role="tab" data-tab="changes" aria-selected="${state.tab === 'changes'}">What changed</button></div>
      <div id="tabbody"><div class="thinking"><div class="spinner"></div>Loading…</div></div>`;
    view.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => { state.tab = b.dataset.tab; viewUpdates(); }));
    const n = Object.values(state.overview?.review || {}).reduce((a, b) => a + b, 0);
    $('#rv-n').textContent = n || '';
    if (state.tab === 'changes') return paintChanges();
    return paintReview();
  }
  async function paintChanges() {
    const d = await api('/api/updates');
    $('#tabbody').innerHTML = d.changes.length ? `<ol class="steps" style="gap:12px">${d.changes.map((c) => `
      <li style="display:block"><div class="card">
        <div class="meta"><span>${esc(day(c.ts))}</span>${sourceBadge(c.source, c.kind)}${c.mr ? `<a href="${esc(c.mr_url)}" target="_blank" rel="noopener">MR !${esc(c.mr)} ↗</a>` : ''}</div>
        <div style="font-size:17px">${esc(c.title)}</div>
        <ul class="small" style="margin:0;padding-left:18px">${c.sections.map((s) => `<li><button class="linkbtn" data-page="${esc(s.path)}">${esc(s.page)}</button> › ${esc(s.heading)} <span class="badge">${esc(s.kind)}</span></li>`).join('')}</ul>
        ${c.decisions.map((x) => `<div class="small"><span class="badge blue">${esc(convIcon(x.src))}</span> Decided in “${esc(x.where)}” on ${esc(day(x.at))}: ${esc(x.statement)}</div>`).join('')}
      </div></li>`).join('')}</ol>` : '<div class="empty">No changes since the graph was built.</div>';
    bindPageLinks($('#tabbody'));
  }
  async function paintReview() {
    const r = await api('/api/review');
    const sec = (title, desc, items, render, extra = '') => `
      <section class="card"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><h2 style="margin:0;font-size:18px">${title}</h2>
        <span class="badge ${items.length ? 'orange' : 'green'}">${items.length}</span><div class="grow"></div>${extra}</div>
        <p class="lede small" style="margin:0">${desc}</p>
        ${items.length ? `<div class="tbl-box"><table class="tbl"><tbody>${items.map(render).join('')}</tbody></table></div>` : '<div class="muted small">Nothing here.</div>'}</section>`;
    $('#tabbody').innerHTML = [
      sec('Answers that went stale', 'A document an earlier answer relied on changed (or a newer source contradicts it). Owners were notified; refresh re-answers from the current version.',
        r.stale_answers, (a) => `<tr><td><b>${esc(a.question)}</b><div class="small muted">${esc(a.reason)}</div></td>
          <td class="small">${a.kind === 'source_conflict' ? '<span class="badge orange">sources disagree</span>' : '<span class="badge">doc changed</span>'}<div class="muted">${esc(day(a.since))}</div></td>
          <td class="small muted">${esc((a.notified || []).join(', '))}</td></tr>`,
        r.stale_answers.length ? '<button class="btn" id="refresh">Refresh answers</button>' : ''),
      sec('Sources disagree', 'A static file (e.g. an old PDF on the shared drive) says something different from the live, more authoritative page.',
        r.source_conflicts, (c) => `<tr><td><b>${esc(c.topic)}</b><div class="small muted">${esc(c.reason)}</div></td>
          <td class="small"><div>Outdated: <code>${esc(c.outdated)}</code></div><div>Authoritative: <code>${esc(c.authoritative)}</code></div></td>
          <td class="small muted">${esc((c.notify || []).join(', ') || 'no owner to notify')}</td></tr>`),
      sec('Decided, but not documented', 'Decisions made in a meeting or chat that no document change has implemented after 3 weeks.',
        r.decided_not_documented, (d) => `<tr><td><b>${esc(d.statement)}</b><div class="small muted"><span class="badge blue">${esc(convIcon(d.source))}</span> ${esc(d.meeting)} · ${esc(day(d.decided_at))}</div></td>
          <td class="small"><span class="badge orange">${Math.round(d.days_open)} days open</span><div class="muted"><code>${esc(d.section || '')}</code></div></td>
          <td class="small muted">${esc([...(d.page_owners || []), ...(d.assignees || [])].join(', '))}</td></tr>`),
      sec('Chat or meeting contradicts the docs', 'Someone stated a rule in a conversation that the current document says differently.',
        r.chat_vs_docs, (c) => `<tr><td><b>“${esc(c.statement)}”</b><div class="small muted">${esc(c.speaker || '')} · <span class="badge blue">${esc(convIcon(c.source))}</span> ${esc(c.where)} · ${esc(day(c.at))}</div></td>
          <td class="small">${esc(c.reason)}<div class="muted"><code>${esc(c.cite)}</code></div></td><td class="small muted">${esc((c.owners || []).join(', '))}</td></tr>`),
      sec('Knowledge gaps', 'Questions people asked that no document answers. "Tribal" means a colleague answered it in chat, but it was never written down.',
        r.gaps, (g) => `<tr><td><b>${esc(g.question)}</b>${g.chat_answer ? `<div class="small muted">Chat answer: ${esc(g.chat_answer)}</div>` : ''}</td>
          <td class="small">${g.tribal ? '<span class="badge orange">tribal</span>' : '<span class="badge">gap</span>'}<div class="muted">asked ${g.asked}×</div></td><td class="small muted">${esc(g.where || '')}</td></tr>`),
    ].join('');
    const rb = $('#refresh');
    if (rb) rb.addEventListener('click', async () => {
      rb.disabled = true; rb.innerHTML = '<span class="spinner"></span> Re-answering…';
      try { await api('/api/refresh', { method: 'POST' }); } catch (e) { alertCard(e); }
      await loadHeader(); paintReview();
    });
  }

  // ------------------------------------------------------------------ company brain
  async function viewBrain() {
    view.innerHTML = '<div class="thinking"><div class="spinner"></div>Loading the graph…</div>';
    const [ov, b] = await Promise.all([api('/api/overview'), api('/api/brain')]);
    state.overview = ov;
    const c = ov.counts;
    const hasUploads = ov.sources.some((s) => s.type === 'uploads');
    view.innerHTML = `
      <div class="qhead"><h1 class="display">${esc(ov.name)} brain</h1>
        <p class="lede">Every document, version, owner, decision and conversation, as one graph in FalkorDB (<code>${esc(ov.graph)}</code>). Docs as of ${esc(day(ov.as_of))}.</p></div>
      <div class="stats">
        ${[['Documents', c.pages], ['Current sections', c.sections], ['Older versions kept', c.versions], ['Conversations', c.conversations],
          ['Decisions', c.decisions], ['Answers watched', c.fresh], ['Stale answers', c.stale]].map(([k, v]) => `<div class="stat"><b>${v ?? 0}</b><span>${k}</span></div>`).join('')}
      </div>
      <h2 class="label">Sources</h2>
      <div class="row">
        <div class="main-col" style="flex-direction:row;flex-wrap:wrap">
          ${ov.sources.map((s) => `<div class="card" style="flex:1 1 260px">
            <div class="meta"><span class="badge ${s.kind === 'static' ? 'orange' : 'blue'}">${esc(s.kind)}</span><span>authority ${esc(s.authority)}</span></div>
            <div style="font-size:17px;font-weight:600">${esc(s.label || s.name)}</div>
            <div class="small muted">${s.pages} documents · ${s.kind === 'static' ? 'changes only when a new file is uploaded' : 're-synced daily'}${s.last_synced ? ' · last sync ' + esc(day(s.last_synced)) : ''}</div></div>`).join('')}
        </div>
        <aside class="side-col">
          <div class="card"><h2 class="label">Daily sync</h2>
            <p class="small muted" style="margin:0">Pull live sources, apply every change since the last sync, re-check stored answers and cross-source conflicts, refresh what went stale.</p>
            <div><button class="btn primary" id="syncbtn">Sync now</button></div><div id="synclog"></div></div>
        </aside>
      </div>
      ${hasUploads ? `<div class="drop" id="drop"><b style="color:var(--ink)">Upload documents</b>
        <span class="small">PDF, DOCX, HTML, Markdown, TXT or CSV. They are stored as a static source and checked against the live docs on the next sync. Nothing leaves your machine except model calls.</span>
        <label class="btn"><input type="file" id="files" multiple hidden accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm,.csv">Choose files</label><div id="upmsg" class="small"></div></div>` : ''}
      <h2 class="label">Documents</h2>
      <div class="card tbl-box"><table class="tbl"><thead><tr><th>Document</th><th>Source</th><th>Owner</th><th>Sections</th><th>Last change</th><th></th></tr></thead><tbody>
        ${b.pages.map((p) => `<tr><td><button class="linkbtn" data-page="${esc(p.path)}">${esc(p.title)}</button><div class="small muted mono">${esc(p.path)}</div></td>
          <td>${sourceBadge(p.source, p.kind, p.uploaded_on)}</td><td>${ownerBadge(p.owner_source, p.owners)}</td><td>${p.sections}</td>
          <td class="small">${esc(day(p.last_changed))}</td><td>${p.open_conflicts ? `<span class="badge orange">${p.open_conflicts} conflict${p.open_conflicts > 1 ? 's' : ''}</span>` : ''}</td></tr>`).join('')}
      </tbody></table></div>
      <h2 class="label">Decisions from meetings and chat</h2>
      <div class="card tbl-box"><table class="tbl"><thead><tr><th>Decided</th><th>Decision</th><th>Where</th><th>Status</th></tr></thead><tbody>
        ${b.decisions.length ? b.decisions.map((d) => `<tr><td class="small">${esc(day(d.at))}</td><td>${esc(d.statement)}<div class="small muted"><code>${esc(d.about || '')}</code></div></td>
          <td class="small"><span class="badge blue">${esc(convIcon(d.source))}</span> ${esc(d.where)}</td>
          <td class="small">${d.implemented_at ? `<span class="badge green">in the docs ${esc(day(d.implemented_at))}</span><div class="muted">${d.lag_days ?? ''} days later</div>` : d.changes_policy ? '<span class="badge orange">not documented yet</span>' : '<span class="badge">no policy change</span>'}</td></tr>`).join('') : '<tr><td colspan="4" class="muted">No decisions yet.</td></tr>'}
      </tbody></table></div>`;
    bindPageLinks(view);
    $('#syncbtn').addEventListener('click', startSync);
    if (ov.sync?.running) pollSync();
    const drop = $('#drop');
    if (drop) {
      const send = async (files) => {
        const fd = new FormData();
        [...files].forEach((f) => fd.append('files', f));
        $('#upmsg').textContent = 'Uploading…';
        try { const r = await api('/api/upload', { method: 'POST', body: fd }); $('#upmsg').innerHTML = `Saved ${r.saved.map(esc).join(', ')}. <b>Run a sync</b> to add them.`; } catch (e) { $('#upmsg').textContent = e.message; }
      };
      $('#files').addEventListener('change', (e) => send(e.target.files));
      drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
      drop.addEventListener('dragleave', () => drop.classList.remove('over'));
      drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); send(e.dataTransfer.files); });
    }
  }
  async function startSync() {
    await api('/api/sync', { method: 'POST' });
    pollSync();
  }
  async function pollSync() {
    const btn = $('#syncbtn');
    for (;;) {
      const s = await api('/api/sync');
      paintSync(s, state.overview);
      const log = $('#synclog');
      if (log) log.innerHTML = s.log.length ? `<div class="log">${esc(s.log.join('\n'))}</div>` : '';
      if (btn) { btn.disabled = s.running; btn.textContent = s.running ? 'Syncing…' : 'Sync now'; }
      if (!s.running) { if (s.error && log) log.insertAdjacentHTML('afterbegin', `<div class="badge red">${esc(s.error)}</div>`); await loadHeader(); return; }
      await new Promise((r) => setTimeout(r, 1500));
    }
  }

  // ------------------------------------------------------------------ page drawer
  function bindPageLinks(root) {
    root.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => openPage(b.dataset.page)));
  }
  async function openPage(path) {
    const dr = $('#drawer');
    dr.hidden = false;
    $('#drawer-body').innerHTML = '<div class="thinking"><div class="spinner"></div>Loading…</div>';
    $('#drawer-close').focus();
    try {
      const d = await api('/api/page?path=' + encodeURIComponent(path));
      const det = d.details || {};
      $('#drawer-body').innerHTML = `
        <h2 id="drawer-title" style="margin:0;font-family:var(--serif);font-weight:400;font-size:30px">${esc(det.title || path)}</h2>
        <div class="meta"><code>${esc(path)}</code>${det.url && /^https?:/.test(det.url) ? `<a href="${esc(det.url)}" target="_blank" rel="noopener">Open original ↗</a>` : ''}</div>
        <div class="meta">${(det.owners || []).map((o) => `<span class="badge green">${esc(String(o).split('|').pop())}</span>`).join('') || '<span class="badge red">No owner</span>'}</div>
        ${d.sections.map((s) => `<details class="card" ${s.outdated_by.length || s.versions ? 'open' : ''}>
          <summary style="cursor:pointer;font-weight:600">${esc(s.heading)} ${s.versions ? `<span class="badge blue">${s.versions + 1} versions</span>` : ''}
            ${s.outdated_by.length ? '<span class="badge orange">outdated copy</span>' : ''}${s.older_copies.length ? '<span class="badge">older copies disagree</span>' : ''}</summary>
          <div class="small muted">this version since ${esc(day(s.since))} · <code>${esc(s.cite)}</code></div>
          ${s.outdated_by.length ? `<div class="small">Conflicts with the authoritative <code>${s.outdated_by.map(esc).join(', ')}</code></div>` : ''}
          <div class="quote">${esc(s.text)}</div>
          ${(s.history || []).filter((h) => h.diff && !h.diff.startsWith('(first')).map((h) => `<div class="small"><b>${esc(day(h.since))}</b> ${esc(h.change || '')}
            ${h.mr_url ? `<a href="${esc(h.mr_url)}" target="_blank" rel="noopener">MR !${esc(h.mr)} ↗</a>` : ''}
            <div>${h.diff.split('\n').map((l) => l.startsWith('REMOVED') ? `<div><span class="diff-del">${esc(l.replace(/^REMOVED \(old text\):\s*/, ''))}</span></div>` : `<div><span class="diff-add">${esc(l.replace(/^ADDED \(new text\):\s*/, ''))}</span></div>`).join('')}</div></div>`).join('')}
        </details>`).join('')}`;
    } catch (e) { $('#drawer-body').innerHTML = `<div class="card warn">${esc(e.message)}</div>`; }
  }
  const closeDrawer = () => { $('#drawer').hidden = true; };
  $('#drawer-close').addEventListener('click', closeDrawer);
  $('#drawer').addEventListener('click', (e) => { if (e.target.id === 'drawer') closeDrawer(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#drawer').hidden) closeDrawer(); });

  // ------------------------------------------------------------------ trust
  async function viewTrust() {
    const t = await api('/api/trust');
    if (!t.available) {
      view.innerHTML = `<h1 class="display">Trust</h1><div class="empty">No benchmark yet for this company. Run <code>python -m hb --company ${esc(state.overview?.company)} bench</code>.</div>`;
      return;
    }
    const st = t.staleness || {};
    const sc = t.source_conflicts;
    const frac = (s) => { const [a, b] = String(s).split('/').map(Number); return b ? a / b : 0; };
    const barRow = (label, val, note = '') => `<tr><td>${esc(label)}</td><td style="width:40%"><div class="bar"><i style="width:${Math.round(frac(val) * 100)}%"></i></div></td><td class="mono">${esc(val)}</td><td class="small muted">${esc(note)}</td></tr>`;
    view.innerHTML = `
      <div class="qhead"><h1 class="display">How much to trust it</h1>
        <p class="lede">End-to-end benchmark on ${esc(state.overview?.name || t.company)}: build the graph at the start date, ask questions, replay every document change and conversation in date order, then score against ground truth the agent never sees. Last run ${esc(t.ran_at)} · ${esc(t.minutes)} min · ${esc(t.usage)}.</p></div>
      <div class="stats">
        <div class="stat"><b>${pct(t.answer_accuracy)}</b><span>answers fully correct (${t.questions} questions)</span></div>
        <div class="stat"><b>${pct(t.answer_correct_or_partial)}</b><span>correct or partially correct</span></div>
        <div class="stat"><b>${st.tp}/${(st.tp || 0) + (st.fn || 0)}</b><span>stale answers caught (${st.fp} false alarms)</span></div>
        ${sc ? `<div class="stat"><b>${esc(sc.found)}</b><span>live vs static conflicts found (${esc(sc.false_alarms_on_consistent_sections)} false alarms)</span></div>` : ''}
      </div>
      <div class="row">
        <div class="main-col"><div class="card"><h2 class="label">Answers by question type</h2>
          <table class="tbl"><tbody>${Object.entries(t.by_type).map(([k, x]) => barRow(k.replace(/_/g, ' '), `${x.correct || 0}/${x.n}`, `${x.partial || 0} partial${x.doc_n ? ` · right doc cited ${x.doc_cited}/${x.doc_n}` : ''}`)).join('')}</tbody></table></div></div>
        <div class="side-col"><div class="card"><h2 class="label">Meetings, chat and tickets</h2>
          <table class="tbl"><tbody>${Object.entries(t.conversations).map(([k, v]) => barRow(k.replace(/_/g, ' '), v)).join('')}</tbody></table>
          <p class="small muted" style="margin:0">"false conflicts in clean threads" and similar rows are errors: lower is better.</p></div></div>
      </div>
      <div class="card warn small">Ground truth for ${esc(state.overview?.name || t.company)} is synthetic (generated with the dataset) and was never visible to the agent. Numbers come from a single run; LLM grading adds a few points of noise.</div>`;
  }

  // ------------------------------------------------------------------ router
  async function route() {
    const r = (location.hash.replace(/^#\/?/, '') || 'ask').split('?')[0];
    document.querySelectorAll('[data-nav]').forEach((a) => {
      if (a.dataset.nav === r) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    try {
      if (r === 'updates') await viewUpdates();
      else if (r === 'brain') await viewBrain();
      else if (r === 'trust') await viewTrust();
      else viewAsk();
    } catch (e) { alertCard(e); }
  }
  function alertCard(e) { view.insertAdjacentHTML('afterbegin', `<div class="card warn">Something went wrong: ${esc(e.message)}</div>`); }
  window.addEventListener('hashchange', route);
  loadHeader().then(route);
})();
