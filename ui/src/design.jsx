// Defining a harness without writing code: a scenario becomes agent specs, you edit them, the studio writes the files.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ReactFlow, ReactFlowProvider, Background, useReactFlow } from '@xyflow/react';
import { nodeTypes, edgeTypes, harnessGraph } from './flow.jsx';
import { Sec } from './inspect.jsx';

export const api = (path, body) => fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined).then(r => r.json());

// A path on this computer. Click it to open the folder with the file selected. The path is also copied.
export function FilePath({ base, rel, sep = '/', label }) {
  const [note, setNote] = useState('');
  const full = rel === undefined ? base : `${base}${sep}${String(rel).split('/').join(sep)}`;
  const open = async ev => {
    ev.stopPropagation();
    try { await navigator.clipboard.writeText(full); } catch (x) { /* clipboard needs focus */ }
    const r = await api('/api/reveal', { path: full });
    setNote(r.error ? r.error : r.opened ? 'opened in your file browser · path copied' : r.note || 'path copied');
    setTimeout(() => setNote(''), 3500);
  };
  return <span className="fp" onClick={open} title="Click to show this file on your computer">
    <svg viewBox="0 0 16 16" width="13" height="13"><path d="M1.5 3.5h4l1.5 1.5h7.5v8h-13z" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>
    {label && <b>{label}</b>}<span className="mono">{full}</span>{note && <em>{note}</em>}</span>;
}

// Defined once, outside any render, so the canvas is not rebuilt each time the page refreshes its data.
function TeamCanvas({ graph, count }) {
  const { fitView } = useReactFlow();
  useEffect(() => { const t = setTimeout(() => fitView({ padding: 0.08, duration: 400 }), 50); return () => clearTimeout(t); }, [count, fitView]);
  return <ReactFlow nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} nodesConnectable={false} nodesDraggable={false} elementsSelectable={false} zoomOnScroll={false} panOnDrag={false} proOptions={{ hideAttribution: true }}><Background color="#e9cfc0" gap={22} /></ReactFlow>;
}

function TeamPreview({ draft, tiers }) {
  const h = useMemo(() => ({ building: draft.building || 'system', id_prefix: draft.id_prefix || 'sys',
    agents: { parameterizer: { tier: 'balanced' }, systems_engineer: { tier: 'deep' }, fallback: { tier: 'balanced' },
              specialists: draft.agents.map(a => ({ capability: a.capability || 'unnamed', tier: a.tier, tools: a.tools })) } }), [draft]);
  const graph = useMemo(() => harnessGraph(h, null), [h]);
  return <div className="teamflow"><ReactFlowProvider><TeamCanvas graph={graph} count={graph.nodes.length} /></ReactFlowProvider></div>;
}

const BLANK = { name: '', building: '', id_prefix: '', description: '', structure_depth: 3, scenario: '', notes: [], agents: [], tools: [] };

export function Designer({ meta, state, settings, onUse }) {
  const [draft, setDraft] = useState(BLANK);
  const [target, setTarget] = useState({ kind: 'kit', name: '' });
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(null);
  const [offline, setOffline] = useState(meta.offline);
  const tiers = Object.keys(settings?.tiers || { fast: 1, balanced: 1, deep: 1 });
  const tools = draft.tools || [];
  const setTool = (i, patch) => set({ tools: tools.map((t, k) => k === i ? { ...t, ...patch } : t) });
  const checkTool = async i => { const r = await api('/api/tools/check', { name: tools[i].name, code: tools[i].code }); setDraft(d => ({ ...d, tools: d.tools.map((t, k) => k === i ? { ...t, ...r } : t) })); };
  const renameTool = (i, name) => { const old = tools[i].name; setDraft(d => ({ ...d, tools: d.tools.map((t, k) => k === i ? { ...t, name, code: t.code.replace(new RegExp(`def ${old || 'tool_name'}\\(`), `def ${name || 'tool_name'}(`) } : t),
    agents: d.agents.map(a => ({ ...a, tools: a.tools.map(x => x === old ? name : x) })) })); setSaved(null); };
  const removeTool = i => { const gone = tools[i].name; setDraft(d => ({ ...d, tools: d.tools.filter((_, k) => k !== i), agents: d.agents.map(a => ({ ...a, tools: a.tools.filter(x => x !== gone) })) })); setSaved(null); };
  const addTool = () => { let n = 'new_tool', c = 1; while (tools.some(t => t.name === n)) n = `new_tool_${++c}`; set({ tools: [...tools, { name: n, code: (meta.tool_template || '').replace('{name}', n).replace('{{', '{').replace('}}', '}'), doc: '', signature: '', error: '' }] }); };
  const set = patch => { setDraft(d => ({ ...d, ...patch })); setSaved(null); };
  const setAgent = (i, patch) => set({ agents: draft.agents.map((a, k) => k === i ? { ...a, ...patch } : a) });
  const load = async (kind, name) => { const r = await api(`/api/harness?kind=${kind}&name=${encodeURIComponent(name)}`); if (r.draft) { setDraft(r.draft); setTarget({ kind, name }); setSaved(null); setError(''); } };
  const generate = async () => {
    setBusy('An agent is proposing the team'); setError('');
    const r = await api('/api/harness/generate', { scenario: draft.scenario, offline, tools }).catch(e => ({ error: String(e) }));
    setBusy('');
    if (r.error) return setError(r.error);
    setDraft(r.draft); setSaved(null);
    if (target.kind === 'kit' && !target.name) setTarget({ kind: 'kit', name: r.draft.id_prefix });
  };
  const save = async () => {
    setError('');
    const r = await api('/api/harness/save', { kind: target.kind, name: target.name || draft.id_prefix, draft });
    if (r.error) return setError(r.error);
    setSaved(r);
  };
  const where = target.kind === 'project' ? `${meta.repo}${meta.sep}projects${meta.sep}${target.name}` : `${meta.repo}${meta.sep}kits${meta.sep}${(target.name || draft.id_prefix || 'new-harness').toLowerCase().replace(/[^a-z0-9-]+/g, '-')}`;
  return <div className="designer">
    <div className="col">
      <Sec title="1 · Operational scenario" note="how the work is done today: who does what, and what they hand to each other" />
      <textarea className="scenario" value={draft.scenario} placeholder="Describe how your team does this kind of engineering today. Name the disciplines, what each one works out, and what they pass to each other." onChange={e => set({ scenario: e.target.value })} />
      <div className="rowline">
        <button className="btn" disabled={!!busy || !draft.scenario.trim()} onClick={generate}>{busy || 'Generate agent specs'}</button>
        <label className="legend"><input type="checkbox" checked={offline} onChange={e => setOffline(e.target.checked)} /> Offline: replay the saved CubeSat team</label>
        <span className="grow" />
        <select value="" onChange={e => { const [k, n] = e.target.value.split('|'); if (n) load(k, n); }}>
          <option value="">Load an existing harness</option>
          {meta.kits.map(k => <option key={k.kit} value={`kit|${k.kit}`}>definition: {k.kit}</option>)}
          {state?.name && <option value={`project|${state.name}`}>project: {state.name}</option>}
        </select>
      </div>
      {error && <div className="card"><b className="bad">{error}</b></div>}
      {draft.notes.length > 0 && <>{draft.notes.map((n, i) => <div className="card quiet" key={i}>{n}</div>)}</>}
      <Sec title="2 · What is being built" />
      <div className="form">
        <label>Name<input value={draft.name} onChange={e => set({ name: e.target.value })} /></label>
        <label>One design is called a<input value={draft.building} placeholder="CubeSat" onChange={e => set({ building: e.target.value })} /></label>
        <label>Function identifier prefix<input className="mono" value={draft.id_prefix} placeholder="cubesat" onChange={e => set({ id_prefix: e.target.value })} /></label>
        <label className="wide">Description<input value={draft.description} onChange={e => set({ description: e.target.value })} /></label>
        <label>Decomposition depth<input type="number" min="1" max="6" value={draft.structure_depth || 3} onChange={e => set({ structure_depth: Math.max(1, Math.min(6, +e.target.value || 3)) })} /><small className="legend">levels of parts below the whole. The systems engineer stops here, and so does a drill-down</small></label>
      </div>
      <Sec title="3 · Write the files" note="the build reads these. Nothing else needs to be coded" />
      <div className="rowline">
        <select value={target.kind} onChange={e => setTarget({ kind: e.target.value, name: e.target.value === 'project' ? state?.name || '' : '' })}>
          <option value="kit">a harness definition, reusable by any project</option>
          {state?.name && <option value="project">this project only: {state.name}</option>}
        </select>
        {target.kind === 'kit' && <input className="mono" placeholder="name" value={target.name} onChange={e => setTarget({ kind: 'kit', name: e.target.value })} />}
        <button className="btn" disabled={!draft.name || !draft.building || !draft.id_prefix} onClick={save}>Write files</button>
      </div>
      <p className="legend">Will write to</p><FilePath base={where} />
      {saved && <><Sec title="Written" note={`${saved.written.length} files`} />
        {saved.written.map(p => <div key={p}><FilePath base={p} /></div>)}
        {saved.kind === 'kit' && <div className="rowline"><button className="btn" onClick={() => onUse(saved.name)}>Build a design system with this harness</button></div>}
        {saved.kind === 'project' && <p className="legend">The next request to this project uses these agents. The change is recorded in the changelog.</p>}</>}
    </div>
    <div className="col agents">
      <Sec title="The team" note={`${draft.agents.length} specialist agent(s), plus the systems engineer`} />
      <TeamPreview draft={draft} tiers={tiers} />
      <div className="agent locked">
        <div className="t"><b>Systems engineer</b><span className="badge">always present</span><span className="badge quiet">deep tier</span><span className="badge quiet">mem0 long-term memory</span></div>
        <p>Writes the interface specification, reviews every build, and records lessons. It acts as a systems engineer and nothing else, with ISO/IEC/IEEE 15288 as its general frame. It cannot be removed or reassigned here.</p>
        <FilePath base={where} rel="agents/systems_engineer.md" sep={meta.sep} />
      </div>
      {draft.agents.map((a, i) => <div className="agent" key={i}>
        <div className="t">
          <input className="mono cap" value={a.capability} placeholder="capability" onChange={e => setAgent(i, { capability: e.target.value })} />
          <select value={a.tier} onChange={e => setAgent(i, { tier: e.target.value })}>{[...new Set([...tiers, a.tier])].map(t => <option key={t} value={t}>{t} tier · {settings?.tiers?.[t] || 'not defined'}</option>)}</select>
          <span className="grow" /><button className="btn ghost small" onClick={() => set({ agents: draft.agents.filter((_, k) => k !== i) })}>Remove</button>
        </div>
        <div className="tools"><span className="legend">may call:</span>{tools.length ? tools.map(t => <label key={t.name} title={t.doc}><input type="checkbox" checked={a.tools.includes(t.name)}
          onChange={e => setAgent(i, { tools: e.target.checked ? [...a.tools, t.name] : a.tools.filter(x => x !== t.name) })} /> <span className="mono">{t.name}</span></label>) : <span className="legend">no tools defined in this harness yet</span>}</div>
        <textarea value={a.instructions} onChange={e => setAgent(i, { instructions: e.target.value })} />
        <FilePath base={where} rel={`agents/${(a.capability || 'unnamed').toLowerCase().replace(/[^a-z0-9]+/g, '_')}.md`} sep={meta.sep} />
      </div>)}
      <button className="btn ghost" onClick={() => set({ agents: [...draft.agents, { capability: '', tier: tiers.includes('balanced') ? 'balanced' : tiers[0], tools: [],
        instructions: '# New specialist\n\nSay who this specialist is and what they own.\n\n## How you work\n\n- A rule a senior person in this field would insist on.\n' }] })}>Add an agent</button>
      <Sec title="Tools" note={`${tools.length} defined in this harness. Tick them on an agent above to map them`} />
      <p className="legend">A tool is a Python function an agent may call while it designs, for example to check a number. You define them here. Each one becomes a file. They run on your computer during a live build.</p>
      {tools.map((t, i) => <div className="agent tool" key={i}>
        <div className="t">
          <input className="mono cap" value={t.name} onChange={e => renameTool(i, e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_'))} />
          <span className="legend mono">{t.signature}</span><span className="grow" />
          <button className="btn ghost small" onClick={() => checkTool(i)}>Check</button><button className="btn ghost small" onClick={() => removeTool(i)}>Remove</button>
        </div>
        <textarea className="code" spellCheck={false} value={t.code} onChange={e => setTool(i, { code: e.target.value })} onBlur={() => checkTool(i)} />
        {t.error ? <p><b className="bad">{t.error}</b></p> : t.doc && <p className="legend">The model is told: {t.doc}</p>}
        <p className="legend">mapped to: {draft.agents.filter(a => a.tools.includes(t.name)).map(a => a.capability).join(', ') || 'no agent yet'}</p>
        <FilePath base={where} rel={`tools/${t.name || 'unnamed'}.py`} sep={meta.sep} />
      </div>)}
      <button className="btn ghost" onClick={addTool}>Add a tool</button>
    </div>
  </div>;
}

// The API key and the model tiers. Saved to .env on this computer. The key is never sent back to the page.
export function SettingsPanel({ settings, reload, onClose }) {
  const [key, setKey] = useState('');
  const [tiers, setTiers] = useState(Object.entries(settings.tiers));
  const [models, setModels] = useState(null);
  const [msg, setMsg] = useState('');
  useEffect(() => { setTiers(Object.entries(settings.tiers)); }, [settings]);
  const fetchModels = useCallback(async () => { setMsg('Asking Anthropic'); const r = await api('/api/models'); setModels(r); setMsg(r.note || `${r.models.length} models from the ${r.source}`); }, []);
  const save = async () => {
    const r = await api('/api/settings', { api_key: key, tiers: Object.fromEntries(tiers.filter(([n]) => n.trim())) });
    if (r.error) return setMsg(r.error);
    setKey(''); setMsg('Saved to .env'); await reload();
    if (key) fetchModels();
  };
  const ids = models?.models || [];
  return <div className="modal" onClick={onClose}><div className="sheet" onClick={e => e.stopPropagation()}>
    <div className="head"><h2>Settings</h2><button onClick={onClose}>Close</button></div>
    <div className="body">
      <Sec title="Anthropic API key" note={settings.has_key ? `a key is saved, ${settings.key_hint}` : 'no key saved yet'} />
      <div className="rowline"><input type="password" className="mono" style={{ flex: 1 }} placeholder={settings.has_key ? 'paste a new key to replace it' : 'sk-ant-...'} value={key} onChange={e => setKey(e.target.value)} autoComplete="off" /></div>
      <p className="legend">The key is written to the file below on this computer. This page never receives it back.</p>
      <FilePath base={settings.env_file} />
      <Sec title="Model tiers" note="an agent spec names a tier. A tier names a model" />
      <div className="rowline"><button className="btn ghost" onClick={fetchModels}>Load the model list from Anthropic</button><span className="legend">{msg}</span></div>
      <datalist id="models">{ids.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</datalist>
      <table className="t"><thead><tr><th>Tier</th><th>Model</th><th /></tr></thead><tbody>{tiers.map(([name, model], i) => <tr key={i}>
        <td><input className="mono" style={{ width: 130 }} value={name} disabled={settings.defaults.includes(name)} onChange={e => setTiers(t => t.map((r, k) => k === i ? [e.target.value.toLowerCase(), r[1]] : r))} /></td>
        <td>{ids.length ? <select style={{ width: 330 }} value={model} onChange={e => setTiers(t => t.map((r, k) => k === i ? [r[0], e.target.value] : r))}>
              {[...new Set([model, ...ids.map(m => m.id)])].filter(Boolean).map(id => <option key={id} value={id}>{(n => n && n !== id ? `${n} · ${id}` : id)(ids.find(m => m.id === id)?.name)}</option>)}</select>
            : <input className="mono" style={{ width: 330 }} list="models" value={model} onChange={e => setTiers(t => t.map((r, k) => k === i ? [r[0], e.target.value] : r))} />}</td>
        <td>{!settings.defaults.includes(name) && <button className="btn ghost small" onClick={() => setTiers(t => t.filter((_, k) => k !== i))}>Remove</button>}</td></tr>)}</tbody></table>
      <div className="rowline"><button className="btn ghost" onClick={() => setTiers(t => [...t, ['', ids[0]?.id || '']])}>Add a tier</button><span className="grow" /><button className="btn" onClick={save}>Save</button></div>
      <p className="legend">A build started after you save uses these tiers. Strands also runs on other providers. That choice is one function in agents/runner.py.</p>
    </div>
  </div></div>;
}
