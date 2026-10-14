import React, { useMemo, useState } from 'react';

// SysML v2 graphical notation, drawn from the project's own data: the metamodel as part
// definitions, one design instance as part usages with their values and connections, and the
// requirements with satisfy relationships. The textual notation the build wrote is the same model.
//
// Boxes size themselves to their longest line, up to a cap. A line past the cap is cut with an
// ellipsis. Hover a box and it rises above its neighbours at its full width, so nothing is lost.
const camel = s => s.replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase());
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
const INK = '#2a231f', LINE = '#5b4a43', FAIL = '#c4161c', PAPER = '#fffdfa', HALO = '#ffffff';
const CH = 6.45, NAMECH = 7.6, LH = 15, HEAD = 36, PADX = 8;
const MINW = 210, MAXW = 300;

const textOf = l => (typeof l === 'string' ? l : l.text);
const natural = (name, lines) => Math.max(MINW, Math.ceil(name.length * NAMECH) + 24, ...lines.map(l => Math.ceil(textOf(l).length * CH) + PADX * 2));
const fit = (text, w) => { const max = Math.floor((w - PADX * 2) / CH); return text.length > max ? text.slice(0, Math.max(0, max - 1)) + '…' : text; };
const wrap = (text, width) => {               // greedy wrap of requirement text to the box width
  const max = Math.floor((width - PADX * 2) / CH), out = [], words = (text || '').split(' ');
  let line = '';
  words.forEach(w => { if ((line + ' ' + w).trim().length <= max) line = (line + ' ' + w).trim(); else { if (line) out.push(line); line = w; } });
  if (line) out.push(line);
  return out;
};

function Box({ x, y, w, h, kind, name, lines = [], color = INK, fill = PAPER, raised = false, onEnter, onLeave }) {
  return <g onMouseEnter={onEnter} onMouseLeave={onLeave} style={{ cursor: 'default' }} filter={raised ? 'url(#sy-raise)' : undefined}>
    <rect x={x} y={y} width={w} height={h} fill={fill} stroke={color} strokeWidth={raised ? 1.8 : 1.3} />
    <text x={x + w / 2} y={y + 14} textAnchor="middle" className="sy-kind">«{kind}»</text>
    <text x={x + w / 2} y={y + 29} textAnchor="middle" className="sy-name">{name}</text>
    {lines.length > 0 && <line x1={x} x2={x + w} y1={y + HEAD} y2={y + HEAD} stroke={color} strokeWidth="1" />}
    {lines.map((l, i) => { const full = textOf(l); const shown = raised ? full : fit(full, w);
      return <text key={i} x={x + PADX} y={y + HEAD + 15 + i * LH} className={'sy-line ' + ((l && l.cls) || '')} style={{ whiteSpace: 'pre' }}>{shown !== full && <title>{full}</title>}{shown}</text>; })}
  </g>;
}

export function SysmlDiagram({ state, design, values, result, onDrill }) {
  const [view, setView] = useState('usage');
  const [hover, setHover] = useState(null);                 // 'el:<i>' or 'req:<id>'
  const contracts = state.interfaces.contracts || [];
  const exchanges = state.interfaces.exchanges || [];
  const reqs = state.spec.requirements || [];
  const whole = cap(camel((state.harness.building || 'system').toLowerCase().replace(/[^a-z0-9]+/g, '_')));
  const structure = (state.interfaces.structure && state.interfaces.structure.length) ? state.interfaces.structure
    : contracts.map(c => ({ name: cap(c.capability) + 'Element', parent: '', specializes: '', multiplicity: '1', capability: c.capability, description: '' }));
  const byName = useMemo(() => Object.fromEntries(structure.map(p => [p.name, p])), [structure]);
  const rootOf = useMemo(() => Object.fromEntries(contracts.map(c => [c.capability, (structure.find(p => p.capability === c.capability && !p.parent) || structure[0]).name])), [contracts, structure]);
  const partOf = c => c.part || rootOf[c.capability];
  const supers = name => { const out = []; let cur = byName[name]; const seen = new Set(); while (cur && cur.specializes && !seen.has(cur.specializes)) { seen.add(cur.specializes); out.push(cur.specializes); cur = byName[cur.specializes]; } return out; };
  const calcsOn = part => contracts.filter(c => partOf(c) === part || supers(part).includes(partOf(c)));
  const kidsOf = name => structure.filter(p => p.parent === name);
  const usages = design.usages && design.usages.length ? design.usages : defaultUsages(structure);
  const failing = useMemo(() => {
    const out = new Set();
    if (result) Object.entries(result.functions || {}).forEach(([fid, r]) => { if (r.result && r.result.passes === false) out.add(fid); if (r.error) out.add(fid); });
    return out;
  }, [result]);
  const maxLines = 14;
  const defLines = c => [
    { text: `doc: ${c.function_id}`, cls: 'muted' },
    ...c.inputs.map(t => `attribute ${camel(t.name)} : ${typeof (values || {})[t.name] === 'string' ? 'String' : 'Real'}  // ${t.unit}`),
    ...c.outputs.map(t => ({ text: `attribute ${camel(t.name)} : ${t.name === 'passes' ? 'Boolean' : 'Real'}  // ${t.unit}, computed`, cls: 'calc' })),
  ];
  // The instance as nested part usages, the way the textual notation writes it: the subsystem root with its values
  // and its calc, then each part inside it as a nested usage with the values it carries, down the structure tree.
  // the usage tree as indented lines: each usage with the calcs that ran on it, the values it carries, and its parts
  const runOn = (fid, path) => (((result || {}).functions || {})[fid] || {}).usages || {};
  const usageLines = (u, path, depth) => {
    const own = Object.keys(values || {}).filter(k => k.lastIndexOf('.') >= 0 && k.slice(0, k.lastIndexOf('.')) === path);
    const outputs = new Set(contracts.flatMap(c => c.outputs.map(t => t.name)));
    const pad = '  '.repeat(depth);
    const lines = [];
    calcsOn(u.part).forEach(c => { const r = runOn(c.function_id)[path];
      if (r) lines.push({ text: `${pad}${(state.registry[c.function_id] || {}).active || 'v?'} of ${c.function_id.split('.').slice(1).join('.')} · ${r.error ? 'ERROR' : r.result && r.result.passes === false ? 'DOES NOT PASS' : 'passes'}`, cls: r.error || (r.result && r.result.passes === false) ? 'bad' : 'muted' }); });
    own.forEach(k => { const attr = k.slice(k.lastIndexOf('.') + 1); const v = values[k];
      lines.push({ text: `${pad}:>> ${camel(attr)} = ${typeof v === 'number' ? show(v) : JSON.stringify(v)}${outputs.has(attr) ? '  // computed' : ''}`, cls: outputs.has(attr) ? 'calc' : (design.tags || {})[k] === 'user' ? '' : 'muted' }); });
    (u.parts || []).forEach(k => { const kp = byName[k.part] || {}; const kpath = `${path}.${k.name}`;
      lines.push({ text: `${pad}part ${k.name} : ${k.part}${kp.specializes ? `  :> ${kp.specializes}` : ''}`, cls: 'part' });
      lines.push(...usageLines(k, kpath, depth + 1)); });
    return lines;
  };
  const useLines = u => usageLines(u, u.name, 0);
  // ---- layout: each column as wide as its longest line, within limits ----
  // one column per top-level usage (interconnection) or per capability root (the flat definition fallback)
  const columns = view === 'usage' ? usages.map(u => ({ u, c: calcsOn(u.part)[0] || contracts.find(c => rootOf[c.capability] === u.part) || contracts[0] })) : contracts.map(c => ({ c }));
  const cols = columns.map(({ c, u }) => {
    const root = u ? u.part : rootOf[c.capability];
    const name = view === 'usage' ? `${u.name} : ${root}` : root;
    const all = view === 'usage' ? useLines(u) : defLines(c);
    const lines = all.length > maxLines ? [...all.slice(0, maxLines - 1), `… ${all.length - maxLines + 1} more`] : all;
    const full = natural(name, all);
    return { c, u, name, lines, all, w: Math.min(MAXW, full), full };
  });
  const gap = 26, pad = 30;
  const xs = []; let acc = pad;
  cols.forEach(col => { xs.push(acc); acc += col.w + gap; });
  const rowW = Math.max(acc - gap - pad, 400);
  const width = pad + rowW + pad;
  const boxH = Math.max(80, ...cols.map(col => HEAD + LH * col.lines.length + 16));
  const nX = exchanges.length;
  const topY = 40, frameY = view === 'usage' ? 40 : 60;
  const wholeH = 44, busY = topY + wholeH + 46;
  const elemY = view === 'usage' ? frameY + 44 + nX * 16 + 20 : busY + 40;
  const reqH = 36 + LH * 3 + 10;
  const idx = {};                                            // capability or part name -> column index
  cols.forEach((col, i) => { if (col.u) { idx[col.u.part] = i; supers(col.u.part).forEach(s => { if (idx[s] === undefined) idx[s] = i; }); } });
  contracts.forEach(c => { const col = cols.findIndex(k => k.u ? (k.u.part === partOf(c) || supers(k.u.part).includes(partOf(c)) || structure.some(p => p.name === partOf(c) && isInside(p.name, k.u.part, byName))) : k.c === c); if (col >= 0 && idx[c.capability] === undefined) idx[c.capability] = col; });
  structure.forEach(p => { if (idx[p.name] === undefined) { const col = cols.findIndex(k => k.u && isInside(p.name, k.u.part, byName)); if (col >= 0) idx[p.name] = col; } });
  const perCol = Math.max(1, ...cols.map((col, i) => reqs.filter(r => idx[r.capability] === i).length));
  const reqY = elemY + boxH + 80;
  const height = view === 'usage' ? reqY + perCol * (reqH + 14) + 24 : elemY + boxH + 70;
  const centre = i => xs[i] + cols[i].w / 2;
  const enter = key => () => setHover(key), leave = () => setHover(null);

  const colBad = col => col.u ? Object.entries((result || {}).functions || {}).some(([fid, r]) => Object.entries(r.usages || {}).some(([path, u]) => (path === col.u.name || path.startsWith(col.u.name + '.')) && (u.error || (u.result && u.result.passes === false)))) : failing.has(col.c.function_id);
  const elementBox = (i, raised) => {
    const col = cols[i], bad = colBad(col);
    const w = raised ? col.full : col.w, lines = raised ? col.all : col.lines;
    const h = raised ? HEAD + LH * lines.length + 16 : boxH;
    const x = raised ? Math.min(xs[i], width - pad - w) : xs[i];
    return <Box key={col.c.function_id + (raised ? ':r' : '')} x={x} y={raised ? elemY - 4 : elemY} w={w} h={h} kind={view === 'usage' ? 'part' : 'part def'} name={col.name}
      lines={lines} color={view === 'usage' && bad ? FAIL : INK} raised={raised} onEnter={enter(`el:${i}`)} onLeave={leave} />;
  };
  const reqBox = (r, i, k, raised) => {
    const bad = colBad(cols[i]);
    const w = cols[i].w - 20, rx = xs[i] + 10, ry = reqY + k * (reqH + 14);
    const all = wrap(r.text, raised ? Math.max(w, 300) : w);
    const lines = raised ? all : (all.length > 3 ? [...all.slice(0, 2), all[2].slice(0, 28) + '…'] : all);
    const rw = raised ? Math.max(w, 300) : w, rh = raised ? HEAD + LH * lines.length + 12 : reqH;
    return <Box key={r.id + (raised ? ':r' : '')} x={raised ? Math.min(rx, width - pad - rw) : rx} y={raised ? ry - 4 : ry} w={rw} h={rh} kind="requirement" name={`${r.id} v${r.version || 1}`} color={bad ? FAIL : INK}
      lines={lines.map(t => ({ text: t, cls: 'muted' }))} raised={raised} onEnter={enter(`req:${r.id}`)} onLeave={leave} />;
  };
  // exchange labels: hug the target end, each on its own lift, with a halo so lines behind stay legible
  const labelFor = (x, faded) => {
    const k = exchanges.indexOf(x);
    const a = idx[x.source_element], b = idx[x.target_element];
    if (a === undefined || b === undefined) return null;
    const x1 = centre(a), x2 = centre(b), lift = 18 + k * 16, y = elemY;
    const got = Object.entries(values || {}).filter(([k]) => k === x.target_input || k.endsWith('.' + x.target_input));
    const text = `${x.id} ${camel(x.target_input)}${got.length === 1 ? ` = ${show(got[0][1])}` : got.length > 1 ? ` × ${got.length}` : ''}`;
    const toRight = x2 > x1, tw = text.length * CH;
    let lx = toRight ? x2 - 10 - tw : x2 + 10;             // just before the drop into the target
    lx = Math.max(pad, Math.min(lx, width - pad - tw));
    return <g key={x.id} className={faded ? 'sy-fade' : ''}>
      <path d={`M${x1},${y} V${y - lift} H${x2} V${y}`} fill="none" stroke={LINE} strokeWidth="1.2" markerEnd="url(#sy-arrow)" />
      <text x={lx} y={y - lift - 4} className="sy-line muted halo"><title>{x.expression}</title>{text}</text>
    </g>;
  };
  const hoveredEl = hover && hover.startsWith('el:') ? Number(hover.slice(3)) : null;
  const hoveredReq = hover && hover.startsWith('req:') ? hover.slice(4) : null;
  return <div className="sysml">
    <div className="chips">
      <button className={'chip ' + (view === 'usage' ? 'on' : '')} onClick={() => setView('usage')}>Interconnection: {design.name} as a {whole}</button>
      <button className={'chip ' + (view === 'definition' ? 'on' : '')} onClick={() => setView('definition')}>Definitions: the metamodel</button>
      <button className={'chip ' + (view === 'interface' ? 'on' : '')} onClick={() => setView('interface')}>Interfaces: ports and flows</button>
      <button className={'chip ' + (view === 'parametric' ? 'on' : '')} onClick={() => setView('parametric')}>Parametric: constraints and bindings</button>
    </div>
    {view === 'definition' ? <div className="sy-scroll"><DefinitionTree state={state} whole={whole} values={values} contracts={contracts} hover={hover} setHover={setHover} onDrill={onDrill} /></div>
    : view === 'interface' ? <div className="sy-scroll"><InterfaceDiagram contracts={contracts} exchanges={exchanges} values={values} result={result} failing={failing} registry={state.registry} structure={structure} partOf={partOf} /></div>
    : view === 'parametric' ? <div className="sy-scroll"><ParametricDiagram contracts={contracts} exchanges={exchanges} values={values} result={result} registry={state.registry} spec={state.spec} partOf={partOf} /></div>
    : <div className="sy-scroll"><svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="SysML v2 diagram">
      <defs>
        <marker id="sy-arrow" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8" fill="none" stroke={LINE} strokeWidth="1.2" /></marker>
        <marker id="sy-arrow-bad" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8" fill="none" stroke={FAIL} strokeWidth="1.2" /></marker>
        <marker id="sy-diamond" markerWidth="14" markerHeight="10" refX="1" refY="5" orient="auto"><path d="M1,5 L7,1 L13,5 L7,9 z" fill={INK} /></marker>
        <filter id="sy-raise" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="6" stdDeviation="6" floodColor="#2a231f" floodOpacity="0.35" /></filter>
      </defs>
      {view === 'usage' && <>
        <rect x={pad - 14} y={frameY} width={rowW + 28} height={boxH + (elemY - frameY) + 40} fill="none" stroke={INK} strokeWidth="1.3" />
        <path d={`M${pad - 14},${frameY + 26} H${pad + 240} L${pad + 252},${frameY} `} fill="none" stroke={INK} strokeWidth="1.3" />
        <text x={pad - 4} y={frameY + 18} className="sy-name">part {design.name} : {whole}</text>
        {exchanges.map(x => labelFor(x, hoveredEl !== null && (idx[x.source_element] === hoveredEl || idx[x.target_element] === hoveredEl)))}
        {cols.map((col, i) => reqs.filter(r => idx[r.capability] === i).map((r, k) => {
          const bad = colBad(col), cx = xs[i] + cols[i].w / 2, ry = reqY + k * (reqH + 14);
          return <g key={r.id + ':link'} className={hoveredEl === i || hoveredReq === r.id ? 'sy-fade' : ''}>
            <path d={`M${cx},${ry} V${elemY + boxH}`} fill="none" stroke={bad ? FAIL : LINE} strokeWidth="1.1" strokeDasharray="5 4" markerEnd={bad ? 'url(#sy-arrow-bad)' : 'url(#sy-arrow)'} />
            {k === 0 && <text x={cx + 5} y={elemY + boxH + 62} className={'sy-line halo ' + (bad ? 'bad' : 'muted')}>{bad ? '«not satisfied»' : '«satisfy»'}</text>}
          </g>;
        }))}
        {cols.map((col, i) => hoveredEl === i ? null : elementBox(i, false))}
        {cols.map((col, i) => reqs.filter(r => idx[r.capability] === i).map((r, k) => hoveredReq === r.id ? null : reqBox(r, i, k, false)))}
      </>}
      {hoveredEl !== null && cols[hoveredEl] && elementBox(hoveredEl, true)}
      {hoveredReq && (() => { const r = reqs.find(q => q.id === hoveredReq); const i = r && idx[r.capability]; const k = r && reqs.filter(q => idx[q.capability] === i).indexOf(r);
        return r && i !== undefined ? reqBox(r, i, k, true) : null; })()}
    </svg></div>}
    <p className="legend">{view === 'definition' ? 'The metamodel as a tree. Hover a part to raise it at full width; from there, drill down to have its specialist decompose it further.'
      : view === 'interface' ? 'Each element with its ports: inputs on the left, outputs on the right. An exchange is a flow from the value it reads to the input it supplies. Hover an element to isolate its flows.'
      : view === 'parametric' ? 'Constraint blocks for every calc def, bound to the values they read and write. Exchanges on the left, the functions on the right. Hover a block to isolate its bindings.'
      : 'The same model as the textual notation. Hover a part or a requirement to raise it at full width. Hover an exchange label for its expression.'}</p>
  </div>;
}


// The metamodel as a tree: the whole on top, subsystems beneath, the concrete parts inside each beneath
// those. Composition diamonds sit on the parent's bottom edge. A subtype hangs from its supertype with a
// hollow triangle. Hover a box to raise it at full width; the base box goes and its lines fade.
function DefinitionTree({ state, whole, values, contracts, hover, setHover, onDrill }) {
  const maxDepth = Number((state.harness || {}).structure_depth) || 3;
  const all = (state.interfaces.structure && state.interfaces.structure.length) ? state.interfaces.structure
    : contracts.map(c => ({ name: cap(c.capability) + 'Element', parent: '', specializes: '', multiplicity: '1', capability: c.capability, description: '' }));
  // Each subsystem is a package. With many parts the packages start folded: the root shows how many parts it holds; click to open one.
  const roots = all.filter(p => !p.parent);
  const [open, setOpen] = useState(() => new Set(roots.map(r => r.capability)));       // every package open: the architecture is the point
  const toggle = capName => setOpen(s => { const n = new Set(s); n.has(capName) ? n.delete(capName) : n.add(capName); return n; });
  const structure = all.filter(p => !p.parent || open.has(p.capability));
  const countIn = Object.fromEntries(roots.map(r => [r.capability, all.filter(p => p.capability === r.capability && p.parent).length]));
  const byName = Object.fromEntries(all.map(p => [p.name, p]));
  const shownSet = new Set(structure.map(p => p.name));
  const rootOf = Object.fromEntries(contracts.map(c => [c.capability, (all.find(p => p.capability === c.capability && !p.parent) || all[0]).name]));
  const partOf = c => c.part || rootOf[c.capability];
  const attrs = {}, folded = {}, seenAttr = new Set();
  const addAttr = (part, text, cls, cap) => { if (seenAttr.has(part + '|' + text)) return; seenAttr.add(part + '|' + text);
    if (shownSet.has(part)) (attrs[part] = attrs[part] || []).push({ text, cls }); else folded[cap] = (folded[cap] || 0) + 1; };
  contracts.forEach(c => c.inputs.forEach(t => {
    if (t.aggregate) {                                   // the per-usage attribute lives on the aggregated part; the roll-up on the calc's part
      const of = t.of || t.name;
      if (t.part && byName[t.part]) addAttr(t.part, `attribute ${camel(of)} : Real  // ${t.unit}`, '', c.capability);
      addAttr(partOf(c), `attribute ${camel(t.name)} : Real  // ${t.aggregate} of ${camel(of)} over ${t.part}`, 'calc', c.capability);
      return; }
    const part = (t.part && byName[t.part]) ? t.part : partOf(c);
    addAttr(part, `attribute ${camel(t.name)} : ${typeof (values || {})[t.name] === 'string' ? 'String' : 'Real'}  // ${t.unit}`, '', c.capability); }));
  contracts.forEach(c => c.outputs.forEach(t => addAttr(partOf(c), `attribute ${camel(t.name)} : ${t.name === 'passes' ? 'Boolean' : 'Real'}  // computed`, 'calc', c.capability)));
  const calcsOn = part => contracts.filter(c => partOf(c) === part);
  const lines = p => p.whole ? [] : [
    { text: p.specializes ? `:> ${p.specializes}  · ${p.multiplicity}` : `multiplicity ${p.multiplicity}`, cls: 'muted' },
    ...(attrs[p.name] || []),
    ...calcsOn(p.name).map(c => ({ text: `calc ${camel(c.function_id.split('.').pop())} : ${cap(camel(c.function_id.split('.').pop()))}  // ${(state.registry[c.function_id] || {}).active || ''}`, cls: 'calc' })),
    ...(!p.parent && countIn[p.capability] ? [{ text: open.has(p.capability) ? `▾ package ${cap(camel(p.capability))} · ${countIn[p.capability]} parts · click to fold` : `▸ package ${cap(camel(p.capability))} · ${countIn[p.capability]} parts, ${folded[p.capability] || 0} attributes · click to open`, cls: 'pkg' }] : [])];
  const MAXL = 9, gapX = 22, gapY = 70, pad = 44;
  // tree layout: a node's block is as wide as itself or as all of its children side by side. Children sit under their parent.
  // A subtype is laid out under its supertype when both are shown; it keeps its composition edge to the real parent.
  const kidsOf = name => structure.filter(p => (p.specializes ? p.specializes === name : p.parent === name) || (p.parent === name && p.specializes && !shownSet.has(p.specializes)));
  const measure = {};
  const mk = p => { const a = lines(p); const shown = a.length > MAXL ? [...a.slice(0, MAXL - 1), `… ${a.length - MAXL + 1} more`] : a; return { p, all: a, shown, w: Math.min(MAXW, natural(p.name, a)), full: natural(p.name, a), h: HEAD + LH * shown.length + 14 }; };
  const blockW = name => { if (measure[name]) return measure[name].bw; const b = mk(byName[name]); const kids = kidsOf(name); const kw = kids.reduce((s, k) => s + blockW(k.name), 0) + gapX * Math.max(0, kids.length - 1); measure[name] = { ...b, bw: Math.max(b.w, kw) }; return measure[name].bw; };
  const topRoots = structure.filter(p => !p.parent);
  const totalW = topRoots.reduce((s, r) => s + blockW(r.name), 0) + gapX * Math.max(0, topRoots.length - 1);
  const width = Math.max(600, totalW + pad * 2);
  const node = {}; let maxY = 0;
  const place = (name, x0, y) => { const m = measure[name]; const x = x0 + (m.bw - m.w) / 2; node[name] = { ...m, x, y }; maxY = Math.max(maxY, y + m.h);
    let cx0 = x0 + (m.bw - (kidsOf(name).reduce((s, k) => s + blockW(k.name), 0) + gapX * Math.max(0, kidsOf(name).length - 1))) / 2;
    kidsOf(name).forEach(k => { place(k.name, cx0, y + m.h + gapY); cx0 += blockW(k.name) + gapX; }); };
  const wholeBox = { p: { name: whole, whole: true }, all: [], shown: [], w: 220, full: 220, h: HEAD + 8 };
  node[whole] = { ...wholeBox, x: width / 2 - 110, y: 20 };
  let rx = pad; topRoots.forEach(r => { place(r.name, rx, 20 + wholeBox.h + gapY); rx += blockW(r.name) + gapX; });
  const height = maxY + 30;
  const levels = [[wholeBox], ...[structure.map(p => node[p.name]).filter(Boolean)]];
  const parentOf = p => p.parent || (p.specializes ? '' : whole);
  const hovered = hover && hover.startsWith('el:') ? hover.slice(3) : null;
  const cx = n => node[n].x + node[n].w / 2;
  return <><div className="chips small"><span className="legend">{all.length} part defs in {roots.length} packages</span>
    <button className="chip" onClick={() => setOpen(new Set(roots.map(r => r.capability)))}>Open all</button>
    <button className="chip" onClick={() => setOpen(new Set())}>Fold all</button></div>
  <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="SysML v2 part definitions as a tree">
    <defs><filter id="sy-raise2" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="6" stdDeviation="6" floodColor="#2a231f" floodOpacity="0.35" /></filter></defs>
    {structure.map(p => {
      const kid = node[p.name]; if (!kid) return null;
      const faded = hovered === p.name || hovered === parentOf(p) || hovered === p.specializes;
      const links = [];
      const par = parentOf(p);
      if (par && node[par]) {                                   // composition: diamond on the parent's bottom edge, orthogonal drop
        const n = node[par], px = cx(par), py = n.y + n.h, midY = py + 28;
        links.push(<g key="c" className={faded ? 'sy-fade' : ''}>
          <path d={`M${px},${py} V${midY} H${cx(p.name)} V${kid.y}`} fill="none" stroke={INK} strokeWidth="1.2" />
          <text x={cx(p.name) + 4} y={kid.y - 4} className="sy-line muted">{p.multiplicity}</text></g>);
      }
      if (p.specializes && node[p.specializes]) {                 // specialization: hollow triangle at the supertype
        const n = node[p.specializes], sx = cx(p.specializes), sy = n.y + n.h, midY = sy + 28;
        links.push(<g key="s" className={faded ? 'sy-fade' : ''}>
          <path d={`M${sx},${sy} V${midY} H${cx(p.name)} V${kid.y}`} fill="none" stroke={INK} strokeWidth="1.2" strokeDasharray="5 3" /></g>);
      }
      return <g key={p.name}>{links}</g>;
    })}
    {roots.filter(r => open.has(r.capability) && countIn[r.capability]).map(r => {         // a dashed package frame around an open subsystem
      const members = structure.filter(p => p.capability === r.capability).map(p => node[p.name]).filter(Boolean);
      const x0 = Math.min(...members.map(m => m.x)) - 10, x1 = Math.max(...members.map(m => m.x + m.w)) + 10;
      const y0 = Math.min(...members.map(m => m.y)) - 26, y1 = Math.max(...members.map(m => m.y + m.h)) + 10;
      return <g key={'pkg' + r.capability}><path d={`M${x0},${y0 + 16} V${y0} H${x0 + 110} L${x0 + 118},${y0 + 8} V${y0 + 16}`} fill="none" stroke="#a27a78" strokeWidth="1" />
        <rect x={x0} y={y0 + 16} width={x1 - x0} height={y1 - y0 - 16} fill="none" stroke="#a27a78" strokeWidth="1" strokeDasharray="4 4" />
        <text x={x0 + 8} y={y0 + 12} className="sy-kind">package {cap(camel(r.capability))}</text></g>;
    })}
    {levels.flat().filter(Boolean).map(b => hovered === b.p.name ? null : <g key={b.p.name} onClick={() => !b.p.whole && !b.p.parent && countIn[b.p.capability] && toggle(b.p.capability)} style={{ cursor: !b.p.whole && !b.p.parent && countIn[b.p.capability] ? 'pointer' : 'default' }}>
      <Box x={node[b.p.name].x} y={node[b.p.name].y} w={b.w} h={b.h} kind="part def" name={b.p.name} lines={b.shown}
      onEnter={() => !b.p.whole && setHover('el:' + b.p.name)} onLeave={() => setHover(null)} /></g>)}
    {/* end markers last, so they sit on top of the box they touch: a filled diamond for composition, a hollow triangle for specialization, both hanging from the edge */}
    {Object.values(node).filter(n => !n.p.whole).flatMap(n => {
      const marks = [];
      const par = parentOf(n.p);
      if (par && node[par]) { const px = cx(par), py = node[par].y + node[par].h; marks.push(<path key={n.p.name + ':d'} d={`M${px - 6},${py + 12} L${px},${py} L${px + 6},${py + 12} L${px},${py + 24} z`} fill={INK} className={hovered === n.p.name || hovered === par ? 'sy-fade' : ''} />); }
      if (n.p.specializes && node[n.p.specializes]) { const sx = cx(n.p.specializes), sy = node[n.p.specializes].y + node[n.p.specializes].h; marks.push(<path key={n.p.name + ':t'} d={`M${sx - 7},${sy + 14} L${sx},${sy} L${sx + 7},${sy + 14} z`} fill={PAPER} stroke={INK} strokeWidth="1.2" className={hovered === n.p.name || hovered === n.p.specializes ? 'sy-fade' : ''} />); }
      return marks;
    })}
    {hovered && node[hovered] && (() => { const b = node[hovered]; const w = Math.max(b.full, onDrill ? 280 : 0), extra = onDrill ? 30 : 0, h = HEAD + LH * b.all.length + 14 + extra; const x = Math.min(b.x, width - pad - w);
      const depth = partDepth(b.p.name, byName), room = maxDepth - depth;
      const by = b.y - 4 + h - 24;
      return <g key="raised" onMouseEnter={() => setHover('el:' + hovered)} onMouseLeave={() => setHover(null)}>
        <g onClick={() => !b.p.parent && countIn[b.p.capability] && toggle(b.p.capability)} style={{ cursor: !b.p.parent && countIn[b.p.capability] ? 'pointer' : 'default' }}>
          <Box x={x} y={b.y - 4} w={w} h={h} kind="part def" name={b.p.name} lines={b.all} raised /></g>
        {onDrill && (room > 0
          ? <g className="sy-btn" style={{ cursor: 'pointer' }} onClick={e => { e.stopPropagation(); onDrill(b.p.name); }}>
              <rect x={x + 8} y={by} width={w - 16} height={20} rx="3" />
              <title>The {b.p.capability} specialist proposes the parts inside {b.p.name}; the systems engineer reviews; the {b.p.capability} function is rebuilt and re-verified if it gains inputs.</title>
              <text x={x + w / 2} y={by + 14} textAnchor="middle">⤵ Drill down into {b.p.name} · {room} level{room === 1 ? '' : 's'} left</text></g>
          : <text x={x + w / 2} y={by + 14} textAnchor="middle" className="sy-line muted">at the depth limit ({maxDepth}). Raise structure_depth in the harness to go further.</text>)}
      </g>; })()}
  </svg></>;
}

// levels below the whole: a subsystem is 1. A subtype sits at its supertype's level. Mirrors studio/pipeline.py part_depth.
export function partDepth(name, byName) {
  let depth = 0, cur = name; const seen = new Set();
  while (cur && byName[cur] && !seen.has(cur)) {
    seen.add(cur);
    const p = byName[cur];
    if (p.parent) { depth += 1; cur = p.parent; } else if (p.specializes) cur = p.specializes; else return depth + 1;
  }
  return depth + 1;
}

const isInside = (part, container, byName) => { let cur = part; const seen = new Set(); while (cur && byName[cur] && !seen.has(cur)) { seen.add(cur); const parent = byName[cur].parent || (byName[cur].specializes && byName[byName[cur].specializes] ? byName[byName[cur].specializes].parent : ''); if (parent === container) return true; cur = parent; } return false; };
export const defaultUsages = structure => { const ident = n => n.charAt(0).toLowerCase() + n.slice(1);
  const kids = name => structure.filter(p => p.parent === name && !p.specializes && !String(p.multiplicity || '1').startsWith('0')).map(p => ({ name: ident(p.name), part: p.name, parts: kids(p.name) }));
  return structure.filter(p => !p.parent && !p.specializes).map(p => ({ name: ident(p.name), part: p.name, parts: kids(p.name) })); };
const namesIn = expr => Array.from(new Set((expr || '').match(/[A-Za-z_][A-Za-z0-9_]*/g) || []));
const show = v => (v === undefined || v === null ? '' : typeof v === 'number' ? (Math.abs(v) >= 1000 ? v.toFixed(0) : Math.abs(v) >= 1 ? String(Math.round(v * 1000) / 1000) : String(Math.round(v * 1e5) / 1e5)) : JSON.stringify(v));

// ---- interface diagram: each part that has calcs as a block with ports; exchanges as flows between parts ----
function InterfaceDiagram({ contracts, exchanges, values, result, failing, registry, structure, partOf }) {
  const [hover, setHover] = useState(null);
  const byName = Object.fromEntries(structure.map(p => [p.name, p]));
  const blocksFor = [];                                  // parts with calcs, in contract order
  contracts.forEach(c => { const p = partOf(c); if (!blocksFor.includes(p)) blocksFor.push(p); });
  const calcsOn = Object.fromEntries(blocksFor.map(p => [p, contracts.filter(c => partOf(c) === p)]));
  // the block an exchange end lands on: that part, else the nearest enclosing part with calcs
  const blockOf = name => { let cur = byName[name] ? name : (structure.find(p => p.capability === name && !p.parent) || {}).name; const seen = new Set();
    while (cur && !seen.has(cur)) { seen.add(cur); if (blocksFor.includes(cur)) return cur; cur = (byName[cur] || {}).parent || ((byName[cur] || {}).specializes ? (byName[byName[cur].specializes] || {}).parent : ''); }
    return null; };
  const order = []; const left = new Set(blocksFor);
  let guard = 0;
  while (left.size && guard++ < 50) {
    const ready = Array.from(left).filter(b => !exchanges.some(x => blockOf(x.target_element) === b && left.has(blockOf(x.source_element)) && blockOf(x.source_element) !== b));
    (ready.length ? ready : [Array.from(left)[0]]).forEach(b => { order.push(b); left.delete(b); });
  }
  const W = 270, gap = 110, pad = 30, top = 70;
  const usagesOf = fid => (((result || {}).functions || {})[fid] || {}).usages || {};
  const boxes = {}; let x = pad, maxH = 0;
  order.forEach(part => {
    const rows = [];
    calcsOn[part].forEach(c => {
      c.inputs.forEach(t => { if (!rows.some(r => r.kind === 'in' && r.name === t.name)) rows.push({ kind: 'in', name: t.name, unit: t.unit, fid: c.function_id, agg: t.aggregate ? `${t.aggregate} of ${t.of || t.name} over ${t.part}` : '', part: t.part }); });
      c.outputs.forEach(t => rows.push({ kind: 'out', name: t.name, unit: t.unit, fid: c.function_id }));
    });
    const h = HEAD + LH * rows.length + 14;
    boxes[part] = { x, y: top, w: W, h, rows, calcs: calcsOn[part] };
    rows.forEach((r, i) => { r.y = top + HEAD + 10 + i * LH; });
    x += W + gap; maxH = Math.max(maxH, h);
  });
  const laneY = k => top + maxH + 26 + k * 18;
  const width = Math.max(600, x - gap + pad), height = laneY(exchanges.length) + 20;
  const portY = (part, name) => { const r = boxes[part].rows.find(q => q.name === name); return r ? r.y : boxes[part].y + HEAD / 2; };
  const bad = part => calcsOn[part].some(c => failing.has(c.function_id));
  const valueOf = name => { const hits = Object.entries(values || {}).filter(([k]) => k === name || k.endsWith('.' + name)); return hits.length === 1 ? show(hits[0][1]) : hits.length > 1 ? `×${hits.length}` : undefined; };
  const outputOf = (fid, name) => { const us = Object.values(usagesOf(fid)).filter(u => u.result && u.result[name] !== undefined); return us.length === 1 ? show(us[0].result[name]) : us.length > 1 ? `×${us.length}` : undefined; };
  return <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="SysML v2 interface diagram" onMouseLeave={() => setHover(null)}>
    <defs><marker id="sy-flow" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8 z" fill={LINE} /></marker></defs>
    {exchanges.map((x, k) => {
      const sp = blockOf(x.source_element), tp = blockOf(x.target_element);
      const a = boxes[sp], b = boxes[tp]; if (!a || !b) return null;
      const reads = namesIn(x.expression).filter(n => a.rows.some(r => r.name === n));
      const y1 = reads.length ? portY(sp, reads[0]) : a.y + a.h - 8;
      const x1 = a.x + a.w, x2 = b.x, y2 = portY(tp, x.target_input);
      const faded = hover && hover !== sp && hover !== tp;
      const ly = laneY(k), xa = x1 + 14 + k * 6, xb = x2 - 14 - k * 6;
      const d = `M${x1},${y1} H${xa} V${ly} H${xb} V${y2} H${x2}`;
      const v = valueOf(x.target_input);
      const label = `${x.id}: ${x.source_element} → ${x.target_element} · ${camel(x.target_input)} = ${x.expression.replace(/[A-Za-z_][A-Za-z0-9_]*/g, camel)}${v !== undefined ? `  → ${v}` : ''}`;
      return <g key={x.id} className={faded ? 'sy-fade' : ''}>
        <path d={d} fill="none" stroke={LINE} strokeWidth="1.3" markerEnd="url(#sy-flow)" />
        <text x={Math.min(xa, xb) + 10} y={ly - 4} className="sy-line muted halo"><title>{x.rationale}</title>{label}</text>
      </g>;
    })}
    {order.map(part => { const b = boxes[part], isBad = bad(part);
      const faded = hover && hover !== part && !exchanges.some(x => (blockOf(x.source_element) === hover && blockOf(x.target_element) === part) || (blockOf(x.target_element) === hover && blockOf(x.source_element) === part));
      const n = Math.max(...b.calcs.map(c => Object.keys(usagesOf(c.function_id)).length), 0);
      return <g key={part} className={faded ? 'sy-fade' : ''} onMouseEnter={() => setHover(part)}>
        <rect x={b.x} y={b.y} width={b.w} height={b.h} fill={PAPER} stroke={isBad ? FAIL : INK} strokeWidth="1.3" />
        <text x={b.x + b.w / 2} y={b.y + 14} textAnchor="middle" className="sy-kind">«part def» {b.calcs.map(c => `calc ${c.function_id.split('.').pop()} ${(registry[c.function_id] || {}).active || ''}`).join(' · ')}{n > 1 ? ` · ${n} usages` : ''}</text>
        <text x={b.x + b.w / 2} y={b.y + 29} textAnchor="middle" className="sy-name">{part}</text>
        <line x1={b.x} x2={b.x + b.w} y1={b.y + HEAD} y2={b.y + HEAD} stroke={isBad ? FAIL : INK} />
        {b.rows.map(r => { const isIn = r.kind === 'in'; const resolved = () => { const us = Object.values(usagesOf(r.fid)).filter(u => u.inputs && u.inputs[r.name] !== undefined); return us.length === 1 ? show(us[0].inputs[r.name]) : us.length > 1 ? `×${us.length}` : valueOf(r.name); };
          const val = isIn ? (r.agg ? resolved() : valueOf(r.name)) : outputOf(r.fid, r.name);
          const text = `${camel(r.name)}${val !== undefined ? ` = ${val}` : ''}${r.agg ? '  Σ' : ''}`;
          return <g key={r.kind + r.name}>
            <rect className={'sy-port ' + r.kind} x={(isIn ? b.x : b.x + b.w) - 4} y={r.y - 4} width={8} height={8} />
            <text x={isIn ? b.x + 10 : b.x + b.w - 10} y={r.y + 4} textAnchor={isIn ? 'start' : 'end'} className={'sy-line ' + (isIn ? '' : 'calc')}><title>{r.name}: {r.unit}{r.agg ? `, ${r.agg}` : r.part ? `, on ${r.part}` : ''}</title>{fit(text, b.w - 12)}</text>
          </g>; })}
      </g>; })}
  </svg>;
}

// ---- parametric diagram: every calc def as a constraint block, bound to the values it reads and writes ----
function ParametricDiagram({ contracts, exchanges, values, result, registry, spec, partOf }) {
  const [hover, setHover] = useState(null);
  const flat = {};                                         // attribute -> value (one usage) or "×n" (several)
  const add = (k, v) => { const name = k.slice(k.lastIndexOf('.') + 1); if (!(name in flat)) flat[name] = { n: 0, v }; flat[name].n += 1; if (flat[name].v !== v) flat[name].many = true; };
  Object.entries(values || {}).forEach(([k, v]) => add(k, v));
  Object.entries((result && result.values) || {}).forEach(([k, v]) => add(k, v));
  const vals = Object.fromEntries(Object.entries(flat).map(([k, f]) => [k, f.many ? `×${f.n}` : f.v]));
  const computed = fid => ((result && result.functions && result.functions[fid] && result.functions[fid].result) || {});
  const outputsOf = {}; contracts.forEach(c => c.outputs.forEach(t => { outputsOf[t.name] = c; }));
  const targets = new Set(exchanges.map(x => x.target_input));
  const given = (spec.parameters || []).map(p => p.name);
  // blocks: exchanges (column 1) and functions (column 3)
  const calcName = id => cap(camel(id.toLowerCase().replace(/[^a-z0-9]+/g, '_')));
  const xBlocks = exchanges.map(x => ({ id: x.id, kind: 'exchange', title: `${camel(x.id.toLowerCase().replace(/[^a-z0-9]+/g, '_'))} : ${calcName(x.id)}`, body: [`{ ${camel(x.target_input)} = ${x.expression.replace(/[A-Za-z_][A-Za-z0-9_]*/g, camel)} }`, `${x.source_element} → ${x.target_element}`],
    reads: namesIn(x.expression).filter(n => n !== x.target_input), writes: [x.target_input], note: x.rationale }));
  const fBlocks = contracts.map(c => ({ id: c.function_id, kind: 'function', title: `${partOf(c)}::${c.function_id.split('.').pop()} ${(registry[c.function_id] || {}).active || ''} : ${cap(camel(c.function_id.split('.').pop()))}`,
    body: [`{ ${c.outputs.map(t => camel(t.name)).join(', ')} = f(${c.inputs.map(t => (t.aggregate ? `${t.aggregate}(${camel(t.of || t.name)})` : camel(t.name))).join(', ')}) }`, `on each ${partOf(c)} · rule ${c.rule_id}`],
    reads: c.inputs.map(t => (t.aggregate ? (t.of || t.name) : t.name)), writes: c.outputs.map(t => `${c.capability}.${t.name}`), note: c.method || c.question }));
  const inputNames = [], seen = new Set();
  const want = n => { if (!seen.has(n) && !targets.has(n) && !outputsOf[n]) { seen.add(n); inputNames.push(n); } };
  xBlocks.forEach(b => b.reads.forEach(want)); fBlocks.forEach(b => b.reads.forEach(want));
  const exchanged = exchanges.map(x => x.target_input);
  const outputs = contracts.flatMap(c => c.outputs.map(t => `${c.capability}.${t.name}`));   // keyed per element: every function has its own passes
  const outputKey = n => (outputsOf[n] ? `${outputsOf[n].capability}.${n}` : n);
  const VW = 200, VH = 22, BW = 270, colGap = 70, pad = 30, top = 44;
  const bodyW = 500;
  const colX = [pad, pad + VW + colGap, pad + VW + colGap + BW + colGap, pad + VW + colGap + BW + colGap + VW + colGap, pad + VW + colGap + BW + colGap + VW + colGap + BW + colGap];
  const node = {};
  const stackValues = (names, col, y0) => { let y = y0; names.forEach(n => { node['v:' + n] = { x: colX[col], y, w: VW, h: VH, name: n }; y += VH + 8; }); return y; };
  const stackBlocks = (blocks, col, y0) => { let y = y0; blocks.forEach(b => { const lines = b.body.flatMap(l => wrap(l, BW)); const h = HEAD + LH * lines.length + 12; node['b:' + b.id] = { x: colX[col], y, w: BW, h, b, lines }; b.h = h; y += h + 18; }); return y; };
  const ys = [stackValues(inputNames, 0, top), stackBlocks(xBlocks, 1, top), stackValues(exchanged, 2, top), stackBlocks(fBlocks, 3, top), stackValues(outputs, 4, top)];
  const width = colX[4] + VW + pad, height = Math.max(...ys) + 20;
  const pinY = (nd, i, n) => nd.y + HEAD + 6 + (n > 1 ? i * ((nd.h - HEAD - 12) / (n - 1)) : (nd.h - HEAD) / 2 - 6);
  const curve = (x1, y1, x2, y2) => { const mx = (x1 + x2) / 2; return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`; };
  const links = [];
  [...xBlocks, ...fBlocks].forEach(b => {
    const nd = node['b:' + b.id];
    b.reads.forEach((n, i) => { const v = node['v:' + n] || node['v:' + outputKey(n)]; if (!v) return;
      const fromRight = v.x < nd.x, py = pinY(nd, i, b.reads.length);
      links.push({ key: b.id + '<' + n, b: b.id, d: fromRight ? curve(v.x + v.w, v.y + VH / 2, nd.x, py) : `M${v.x},${v.y + VH / 2} C${v.x - 60},${v.y + VH / 2} ${nd.x + nd.w + 60},${py} ${nd.x + nd.w},${py}`, pin: [fromRight ? nd.x : nd.x + nd.w, py] }); });
    b.writes.forEach((n, i) => { const v = node['v:' + n]; if (!v) return; const py = pinY(nd, i, b.writes.length);
      links.push({ key: b.id + '>' + n, b: b.id, d: curve(nd.x + nd.w, py, v.x, v.y + VH / 2), pin: [nd.x + nd.w, py], out: true }); });
  });
  const lit = key => !hover || hover === key;
  const header = (x, text) => <text key={text} x={x} y={top - 16} className="sy-kind">{text}</text>;
  return <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="SysML v2 parametric diagram" onMouseLeave={() => setHover(null)}>
    {[header(colX[0], 'given values'), header(colX[1], '«constraint» exchanges'), header(colX[2], 'exchanged values'), header(colX[3], '«constraint» calc defs'), header(colX[4], 'computed values')]}
    {links.map(l => <g key={l.key} className={lit(l.b) ? '' : 'sy-fade'}>
      <path d={l.d} fill="none" stroke={LINE} strokeWidth={hover === l.b ? 1.8 : 1.1} />
      <rect className={'sy-port ' + (l.out ? 'out' : '')} x={l.pin[0] - 4} y={l.pin[1] - 4} width={8} height={8} /></g>)}
    {Object.entries(node).filter(([k]) => k.startsWith('v:')).map(([k, v]) => { const n = v.name, dot = n.indexOf('.'), owner = dot > 0 ? n.slice(0, dot) : '', name = dot > 0 ? n.slice(dot + 1) : n;
      const val = owner ? (vals[name] !== undefined && !String(vals[name]).startsWith('×') ? vals[name] : computed((contracts.find(c => c.capability === owner) || {}).function_id)[name]) : vals[name];
      const touched = !hover || links.some(l => l.b === hover && (l.key.endsWith('<' + name) || l.key.endsWith('>' + n)));
      const src = '';
      return <g key={k} className={touched ? '' : 'sy-fade'}>
        <rect className={'sy-val' + (owner ? ' out' : '')} x={v.x} y={v.y} width={v.w} height={v.h} rx="4" />
        <text x={v.x + 8} y={v.y + 15} className={'sy-line ' + (owner ? 'calc' : '')}><title>{n}</title>{fit(`${owner ? owner + '.' : ''}${camel(name)}${val !== undefined ? ` = ${show(val)}` : ''}${src ? '  (ai)' : ''}`, v.w)}</text></g>; })}
    {Object.entries(node).filter(([k]) => k.startsWith('b:')).map(([k, nd]) => <g key={k} className={lit(nd.b.id) ? '' : 'sy-fade'} onMouseEnter={() => setHover(nd.b.id)} style={{ cursor: 'default' }}>
      <rect className="sy-cb" x={nd.x} y={nd.y} width={nd.w} height={nd.h} rx="10" />
      <text x={nd.x + nd.w / 2} y={nd.y + 14} textAnchor="middle" className="sy-kind">«constraint»</text>
      <text x={nd.x + nd.w / 2} y={nd.y + 29} textAnchor="middle" className="sy-name">{fit(nd.b.title, nd.w)}</text>
      <line x1={nd.x} x2={nd.x + nd.w} y1={nd.y + HEAD} y2={nd.y + HEAD} stroke={INK} />
      {nd.lines.map((l, i) => <text key={i} x={nd.x + PADX} y={nd.y + HEAD + 15 + i * LH} className={'sy-line ' + (i === 0 ? '' : 'muted')}><title>{nd.b.note}</title>{l}</text>)}
    </g>)}
  </svg>;
}
