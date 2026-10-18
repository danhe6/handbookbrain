/* Tiny dependency-free graph renderer (SVG): force or layered layout, zoom/pan, drag, select, focus.
   HBGraph.mount(el, {nodes, edges}, {layout:'force'|'layered', height, onSelect, labels:'hl'|'all'}) */
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const TYPES = {
    Question: { c: '#8A8780', r: 9, rank: 1, name: 'Question' },
    Answer: { c: '#1F1E1D', r: 13, rank: 1, name: 'Answer' },
    Section: { c: '#3E63DD', r: 8, rank: 2, name: 'Section version' },
    Page: { c: '#1F1E1D', r: 11, rank: 3, ring: true, name: 'Document' },
    Change: { c: '#7C5CD6', r: 8, rank: 4, name: 'Change' },
    MergeRequest: { c: '#A48BE6', r: 7, rank: 4, name: 'Merge request' },
    Decision: { c: '#C96442', r: 11, rank: 5, name: 'Decision' },
    Conversation: { c: '#D39B2C', r: 9, rank: 6, name: 'Meeting / chat' },
    Claim: { c: '#C2412D', r: 7, rank: 5, name: 'Chat claim' },
    Person: { c: '#2E8B62', r: 7, rank: 7, name: 'Person' },
    Team: { c: '#2E8B62', r: 9, rank: 7, ring: true, name: 'Team' },
    Source: { c: '#8A8780', r: 10, rank: 2.5, ring: true, name: 'Source' },
    System: { c: '#0F766E', r: 9, rank: 8, ring: true, name: 'System' },
    Dashboard: { c: '#B5579A', r: 8, rank: 8, name: 'Dashboard' },
    Field: { c: '#64748B', r: 6, rank: 8, name: 'Field' },
    Metric: { c: '#14B8A6', r: 7, rank: 8, name: 'Metric' },
    Proposal: { c: '#C96442', r: 9, rank: 8, ring: true, name: 'Drafted update' },
  };
  const typeOf = (t) => TYPES[t] || { c: '#8A8780', r: 8, rank: 4, name: t };
  const el = (tag, attrs = {}, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };
  const trunc = (s, n) => { s = String(s ?? ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

  function rng(seed) { let s = seed % 2147483647; if (s <= 0) s += 2147483646; return () => (s = (s * 16807) % 2147483647) / 2147483647; }

  function forceLayout(nodes, edges, W, H) {
    const rand = rng(nodes.length * 7919 + edges.length);
    const k = Math.sqrt((W * H) / Math.max(nodes.length, 1)) * (nodes.length > 60 ? 1.05 : 0.8);
    nodes.forEach((n, i) => { const a = (i / nodes.length) * Math.PI * 2; const rr = 0.35 + rand() * 0.15; n.x = W / 2 + Math.cos(a) * W * rr; n.y = H / 2 + Math.sin(a) * H * rr; });
    const idx = new Map(nodes.map((n, i) => [n.id, i]));
    const E = edges.map((e) => [idx.get(e.source), idx.get(e.target)]).filter(([a, b]) => a != null && b != null);
    let t = W / 8;
    const N = nodes.length;
    const iters = N > 250 ? 180 : 320;
    for (let it = 0; it < iters; it++) {
      const dx = new Float64Array(N), dy = new Float64Array(N);
      for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
        let x = nodes[i].x - nodes[j].x, y = nodes[i].y - nodes[j].y;
        let d2 = x * x + y * y; if (d2 < 0.01) { x = rand() - 0.5; y = rand() - 0.5; d2 = 0.5; }
        const d = Math.sqrt(d2), f = (k * k) / d;
        dx[i] += (x / d) * f; dy[i] += (y / d) * f; dx[j] -= (x / d) * f; dy[j] -= (y / d) * f;
      }
      for (const [a, b] of E) {
        const x = nodes[a].x - nodes[b].x, y = nodes[a].y - nodes[b].y;
        const d = Math.sqrt(x * x + y * y) || 0.01, f = (d * d) / k;
        dx[a] -= (x / d) * f; dy[a] -= (y / d) * f; dx[b] += (x / d) * f; dy[b] += (y / d) * f;
      }
      for (let i = 0; i < N; i++) {
        dx[i] += (W / 2 - nodes[i].x) * 0.02 * k / 40; dy[i] += (H / 2 - nodes[i].y) * 0.02 * k / 40;
        const d = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) || 1;
        nodes[i].x += (dx[i] / d) * Math.min(d, t); nodes[i].y += (dy[i] / d) * Math.min(d, t);
      }
      t *= 0.985;
    }
  }

  // Story layout: the reasoning chain left to right (meeting -> decision -> change -> section -> document -> answer
  // -> question); people, teams, sources, merge requests and chat claims orbit the chain node they attach to.
  const CHAIN = { Conversation: 0, Decision: 1, Change: 2, Section: 3, Page: 4, Answer: 5, Question: 6 };
  function layeredLayout(nodes, edges, W, H) {
    const nb = new Map(nodes.map((n) => [n.id, []]));
    edges.forEach((e) => { nb.get(e.source)?.push(e.target); nb.get(e.target)?.push(e.source); });
    const used = [...new Set(nodes.filter((n) => n.type in CHAIN).map((n) => CHAIN[n.type]))].sort((a, b) => a - b);
    const colOf = new Map(used.map((r, i) => [r, i]));
    const M = 70, gapX = (W - 2 * M) / Math.max(used.length, 1);
    const cols = used.map(() => []);
    nodes.filter((n) => n.type in CHAIN).forEach((n) => cols[colOf.get(CHAIN[n.type])].push(n));
    const mid = H * 0.5;
    cols.forEach((C, i) => {
      C.sort((a, b) => (b.hl - a.hl) || ((a.current === false) - (b.current === false)));
      C.forEach((n, j) => { n._col = i;   // highlighted nodes on the centre line, the rest alternate below / above it
        n.x = M + gapX * (i + 0.5);
        const k = Math.ceil(j / 2), side = j % 2 ? 1 : -1;
        n.y = j === 0 ? mid : mid + side * k * 78;
      });
    });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const slots = new Map();
    nodes.filter((n) => !(n.type in CHAIN)).forEach((n) => {
      const anchors = nb.get(n.id).map((x) => byId.get(x)).filter((a) => a && a.type in CHAIN);
      const ax = anchors.length ? anchors.reduce((s, a) => s + a.x, 0) / anchors.length : W / 2;
      const key = Math.round(ax / 40);
      const k = slots.get(key) || 0; slots.set(key, k + 1);
      n.x = ax + (k % 2 ? 26 : -26) * (k > 1 ? 1.6 : 1);
      n.y = (k % 2 ? H - 40 - Math.floor(k / 2) * 34 : 40 + Math.floor(k / 2) * 34);
    });
  }

  function fit(nodes, W, H, pad) {
    if (!nodes.length) return;
    const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const sx = (W - 2 * pad) / Math.max(x1 - x0, 1), sy = (H - 2 * pad) / Math.max(y1 - y0, 1);
    const k = Math.min(sx, sy, 2.2);
    const ox = (W - (x1 - x0) * k) / 2, oy = (H - (y1 - y0) * k) / 2;
    nodes.forEach((n) => { n.x = ox + (n.x - x0) * k; n.y = oy + (n.y - y0) * k; });
  }

  function mount(container, data, opts = {}) {
    container.innerHTML = '';
    const H = opts.height || 520;
    const W = Math.max(container.clientWidth || 900, 320);
    const nodes = data.nodes.map((n) => ({ ...n }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const edges = data.edges.filter((e) => byId.has(e.source) && byId.has(e.target));
    if (opts.layout === 'preset') nodes.forEach((n) => { n.x = 70 + n.px * (W - 140); n.y = 40 + n.py * (H - 90); });   // caller-placed (fractions)
    else if (opts.layout === 'layered') layeredLayout(nodes, edges, W, H); else forceLayout(nodes, edges, W, H);
    if (opts.layout !== 'layered' && opts.layout !== 'preset') fit(nodes, W, H, 50);

    const svg = el('svg', { width: '100%', height: H, viewBox: `0 0 ${W} ${H}`, class: 'hbg', role: 'img', 'aria-label': opts.label || 'Graph' }, container);
    const defs = el('defs', {}, svg);
    for (const [id, c] of [['arr', '#C9C5BC'], ['arrh', '#C96442']]) {
      const m = el('marker', { id: `${id}-${container.id || 'g'}`, viewBox: '0 0 10 10', refX: 10, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' }, defs);
      el('path', { d: 'M0 0L10 5L0 10z', fill: c }, m);
    }
    const root = el('g', {}, svg);
    const gE = el('g', {}, root), gN = el('g', {}, root);
    const anyHl = nodes.some((n) => n.hl);
    const labelsAll = opts.labels === 'all' || nodes.length <= 40;

    const eEls = edges.map((e) => {
      const hl = !!e.hl;
      const conflict = e.type === 'CONFLICTS_WITH' || e.type === 'CONTRADICTS';
      const ln = el('line', { class: 'e', stroke: conflict ? '#C2412D' : hl ? '#C96442' : '#D6D2C9', 'stroke-width': hl ? 2 : 1,
        'stroke-dasharray': e.type === 'SUPERSEDES' || conflict ? '4 3' : '', 'marker-end': `url(#${hl ? 'arrh' : 'arr'}-${container.id || 'g'})`, opacity: anyHl && !hl && !conflict ? 0.55 : 1 }, gE);
      const tt = el('title', {}, ln); tt.textContent = e.type + (e.reason ? ': ' + e.reason : '');
      return { e, ln };
    });
    const nEls = nodes.map((n) => {
      const t = typeOf(n.type);
      const g = el('g', { class: 'n', tabindex: 0, role: 'button', 'aria-label': `${t.name}: ${n.label}` }, gN);
      const superseded = n.type === 'Section' && n.current === false;
      const r = (nodes.length > 60 ? t.r * 0.72 : t.r) + (n.hl ? 2 : 0);
      const stroke = n.outdated_copy ? '#C2412D' : n.type === 'Page' && n.kind === 'static' ? '#B07A12' : t.c;
      if (n.hl) el('circle', { r: r + 5, fill: t.c, opacity: 0.14 }, g);
      el('circle', { r, fill: t.ring || superseded ? '#FFFFFF' : t.c, stroke,
        'stroke-width': t.ring || superseded || n.outdated_copy ? 2.2 : 0, 'stroke-dasharray': superseded ? '3 2' : '' }, g);
      const showLabel = labelsAll || n.hl || (nodes.length > 60 ? ['Source', 'Team'] : ['Page', 'Decision', 'Source', 'Team', 'Answer']).includes(n.type);
      {
        const up = n._col != null && n._col % 2 === 1;   // layered: alternate labels above / below the chain
        const tx = el('text', { y: up ? -(r + 7) : r + 13, 'text-anchor': 'middle', class: 'nl' + (n.hl ? ' hl' : '') }, g);
        tx.textContent = trunc(n.label || t.name, opts.labelMax || (n._col != null ? 26 : n.hl ? 34 : 24));
        if (!showLabel) { tx.style.display = 'none'; n._lazy = tx; }   // shown when its domain is in focus
      }
      const tt = el('title', {}, g); tt.textContent = `${t.name}: ${n.label}`;
      if (anyHl && !n.hl) g.setAttribute('opacity', 0.6);
      return { n, g };
    });
    const place = () => {
      nEls.forEach(({ n, g }) => g.setAttribute('transform', `translate(${n.x},${n.y})`));
      eEls.forEach(({ e, ln }) => {
        const a = byId.get(e.source), b = byId.get(e.target);
        const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
        const ra = typeOf(a.type).r + 2, rb = typeOf(b.type).r + 4;
        ln.setAttribute('x1', a.x + (dx / d) * ra); ln.setAttribute('y1', a.y + (dy / d) * ra);
        ln.setAttribute('x2', b.x - (dx / d) * rb); ln.setAttribute('y2', b.y - (dy / d) * rb);
      });
    };
    place();

    // zoom + pan
    let view = { x: 0, y: 0, k: 1 };
    const apply = () => root.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`);
    const pt = (ev) => { const r = svg.getBoundingClientRect(); return { x: ((ev.clientX - r.left) / r.width) * W, y: ((ev.clientY - r.top) / r.height) * H }; };
    svg.addEventListener('wheel', (ev) => {
      if (!ev.ctrlKey && !ev.metaKey && !opts.wheelZoom) return;
      ev.preventDefault();
      const p = pt(ev), f = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
      view.x = p.x - (p.x - view.x) * f; view.y = p.y - (p.y - view.y) * f; view.k *= f; apply();
    }, { passive: false });
    let drag = null;
    svg.addEventListener('pointerdown', (ev) => {
      const g = ev.target.closest('g.n');
      const p = pt(ev);
      drag = g ? { node: nEls.find((x) => x.g === g).n, moved: false } : { pan: true, sx: p.x - view.x, sy: p.y - view.y };
      svg.setPointerCapture(ev.pointerId);
    });
    svg.addEventListener('pointermove', (ev) => {
      if (!drag) return;
      const p = pt(ev);
      if (drag.pan) { view.x = p.x - drag.sx; view.y = p.y - drag.sy; apply(); return; }
      drag.moved = true; drag.node.x = (p.x - view.x) / view.k; drag.node.y = (p.y - view.y) / view.k; place();
    });
    svg.addEventListener('pointerup', () => {
      if (drag && drag.node && !drag.moved) select(drag.node.id, true);
      drag = null;
    });
    svg.addEventListener('keydown', (ev) => {
      const g = ev.target.closest && ev.target.closest('g.n');
      if (g && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); select(nEls.find((x) => x.g === g).n.id, true); }
    });

    let selected = null, focused = null;
    const inFocus = (n) => !focused || (n.domains || []).includes(focused);
    const home = new Map(nodes.map((n) => [n.id, [n.x, n.y]]));
    function focus(dom) {
      focused = dom || null;
      const on = nodes.filter(inFocus);
      if (!focused) nodes.forEach((n) => { [n.x, n.y] = home.get(n.id); });
      else {   // lay the domain out on its own, on the full canvas, so it is readable rather than a zoomed-in knot
        const ids = new Set(on.map((n) => n.id));
        const sub = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
        if (opts.layout === 'layered') layeredLayout(on, sub, W, H); else { forceLayout(on, sub, W, H); fit(on, W, H, 60); }
      }
      nEls.forEach(({ n, g }) => {
        const k = inFocus(n);
        g.style.display = k ? '' : 'none'; g.setAttribute('opacity', 1);
        if (n._lazy) n._lazy.style.display = focused && k && on.length <= 70 ? '' : 'none';
      });
      eEls.forEach(({ e, ln }) => { const k = inFocus(byId.get(e.source)) && inFocus(byId.get(e.target)); ln.style.display = k ? '' : 'none'; ln.setAttribute('opacity', 1); });
      view = { x: 0, y: 0, k: 1 }; apply(); place();
      return on.length;
    }
    function select(id, fire) {
      selected = id;
      if (!id && focused) { focus(focused); return; }
      const nbrs = new Set([id]);
      edges.forEach((e) => { if (e.source === id) nbrs.add(e.target); if (e.target === id) nbrs.add(e.source); });
      nEls.forEach(({ n, g }) => { g.classList.toggle('sel', n.id === id); g.setAttribute('opacity', !id || nbrs.has(n.id) ? 1 : 0.25); });
      eEls.forEach(({ e, ln }) => ln.setAttribute('opacity', !id || e.source === id || e.target === id ? 1 : 0.15));
      if (fire && opts.onSelect) opts.onSelect(byId.get(id));
    }
    function filter(hidden) {
      nEls.forEach(({ n, g }) => { g.style.display = hidden.has(n.type) ? 'none' : ''; });
      eEls.forEach(({ e, ln }) => { ln.style.display = hidden.has(byId.get(e.source).type) || hidden.has(byId.get(e.target).type) ? 'none' : ''; });
    }
    function find(q) {
      q = (q || '').toLowerCase().trim();
      if (!q) return select(null);
      const hit = nodes.find((n) => String(n.label || '').toLowerCase().includes(q));
      if (hit) select(hit.id, true);
    }
    function reset() { view = { x: 0, y: 0, k: 1 }; apply(); focused = null; select(null); focus(null); }
    return { select: (id) => select(id, false), filter, find, reset, focus, types: [...new Set(nodes.map((n) => n.type))] };
  }

  function legend(types) {
    return types.map((t) => { const x = typeOf(t); return `<span class="lg"><i style="background:${x.ring ? '#fff' : x.c};border:2px solid ${x.c}"></i>${x.name}</span>`; }).join('');
  }
  window.HBGraph = { mount, legend, TYPES, typeOf };
})();
