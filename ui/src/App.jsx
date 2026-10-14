import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ReactFlow, ReactFlowProvider, Background, Controls, useReactFlow } from '@xyflow/react';
import { nodeTypes, edgeTypes, taskGraph, systemGraph, harnessGraph } from './flow.jsx';
import { Code, Json, Prose, Sec, Tag, Result, Timeline, Diff } from './inspect.jsx';

import { api, FilePath, Designer, SettingsPanel } from './design.jsx';
import { Tornado, Interactions } from './tornado.jsx';
import { SysmlDiagram } from './sysml.jsx';
const fmtNum = v => (v === null || v === undefined ? '?' : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toFixed(3));
const Defs = () => <svg style={{ position: 'absolute', width: 0, height: 0 }}><defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#a27a78" /></marker></defs></svg>;

// The canvas fits itself to the graph until you pan, zoom, or drag. After that it leaves your view alone.
// Nodes you drag stay where you put them, even as new events arrive.
function Flow({ nodes, edges, onNodeClick, fitKey, viewKey = '' }) {
  const { fitView } = useReactFlow();
  const touched = useRef(false), moved = useRef({});
  const [tick, setTick] = useState(0);
  useEffect(() => { touched.current = false; moved.current = {}; setTick(t => t + 1); }, [viewKey]);
  useEffect(() => { if (touched.current) return undefined; const t = setTimeout(() => fitView({ padding: 0.12, duration: 400 }), 60); return () => clearTimeout(t); }, [fitKey, viewKey, fitView]);
  const placed = useMemo(() => nodes.map(n => moved.current[n.id] ? { ...n, position: moved.current[n.id] } : n), [nodes, tick]);
  const onNodesChange = useCallback(changes => {
    let any = false;
    for (const c of changes) if (c.type === 'position' && c.position) { moved.current[c.id] = c.position; any = true; }
    if (any) { touched.current = true; setTick(t => t + 1); }
  }, []);
  return <ReactFlow nodes={placed} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodeClick={onNodeClick} minZoom={0.2} maxZoom={1.6}
    onNodesChange={onNodesChange} onMoveStart={ev => { if (ev) touched.current = true; }}
    nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}>
    <Background color="#e9cfc0" gap={22} /><Controls showInteractive={false} /></ReactFlow>;
}

// ---- 1. Orchestration: every task, live, with every payload behind it --------------------
function Orchestration({ project, state, running }) {
  const [iteration, setIteration] = useState(state.iteration);
  const [events, setEvents] = useState([]);
  const [control, setControl] = useState({});
  const [keepTier, setKeepTier] = useState(true);
  const ctl = async (action, extra = {}) => { const r = await api('/api/control', { project, iteration, action, ...extra }); if (!r.error) setControl(r); };
  const [cursor, setCursor] = useState(null);                 // null follows the live log. A number replays it
  const [selected, setSelected] = useState(null);
  const [result, setResult] = useState(undefined);
  useEffect(() => { setIteration(state.iteration); }, [state.iteration]);
  useEffect(() => { setEvents([]); setCursor(null); setSelected(null); }, [project, iteration]);
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      const r = await api(`/api/events?project=${project}&iteration=${iteration}&since=0`).catch(() => null);
      if (!stop && r && r.events) { setEvents(prev => r.total !== prev.length ? r.events : prev); setControl(r.control || {}); }
    };
    tick(); const t = setInterval(tick, 700); return () => { stop = true; clearInterval(t); };
  }, [project, iteration]);
  useEffect(() => {
    if (cursor === null) return;
    if (cursor >= events.length) { setCursor(null); return; }
    const t = setTimeout(() => setCursor(c => c + 1), 150); return () => clearTimeout(t);
  }, [cursor, events.length]);
  const shown = cursor === null ? events : events.slice(0, cursor);
  const reqMeta = (state.request_meta || [])[iteration - 1] || {};
  const graph = useMemo(() => taskGraph(shown, selected, iteration, reqMeta), [shown, selected, iteration, reqMeta.kind, reqMeta.part]);
  const mine = useMemo(() => shown.filter(e => e.task === selected), [shown, selected]);
  useEffect(() => { setResult(undefined); if (selected && selected !== 'request') api(`/api/result?project=${project}&iteration=${iteration}&task=${encodeURIComponent(selected)}`).then(setResult); }, [selected, project, iteration, events.length]);
  const run = (state.runs || []).filter(r => r.iteration === iteration).slice(-1)[0]?.run;
  const built = result && result.module_code !== undefined ? result : null;
  const counts = [...graph.tasks.values()].reduce((a, t) => (a[t.status] = (a[t.status] || 0) + 1, a), {});
  return <div className="main">
    <div className="canvas">
      <div style={{ position: 'absolute', zIndex: 4, top: 10, right: 14, display: 'flex', gap: 8, alignItems: 'center' }}>
        <span className="status">{running ? 'building · ' : ''}{counts.done || 0} done{counts.running ? ` · ${counts.running} running` : ''}{counts.failed ? ` · ${counts.failed} failed` : ''} · {shown.length} events</span>
        <select value={iteration} onChange={e => setIteration(+e.target.value)}>{Array.from({ length: state.iteration }, (_, i) => <option key={i} value={i + 1}>request {i + 1}{((state.request_meta || [])[i] || {}).kind === 'drilldown' ? ` · drill down ${(state.request_meta || [])[i].part}` : ''}</option>)}</select>
        <button className="btn ghost" onClick={() => setCursor(0)}>Replay</button>
        {running && (control.paused ? <button className="btn" onClick={() => ctl('resume')}>Continue</button> : <button className="btn ghost" onClick={() => ctl('pause')}>Pause before next attempt</button>)}
      </div>
      {running && control.paused && <div className="notice" style={{ position: 'absolute', zIndex: 4, top: 56, left: 0, right: 0 }}><span>Paused. Tasks finish their current attempt, then wait. Agents that are mid-answer still run to the end of that answer. Pick a task to stop it or change its tier, then Continue.</span></div>}
      <Flow nodes={graph.nodes} edges={graph.edges} fitKey={`${graph.nodes.length}:${!!selected}`} viewKey={`${project}:${iteration}`} onNodeClick={(_, n) => n.type === 'task' && setSelected(n.id)} />
    </div>
    {selected && <div className="panel wide">
      <div className="head"><h2>{selected}</h2><small>runtime trace</small><button onClick={() => setSelected(null)}>Close</button></div>
      <div className="body">{selected === 'request' ? <Sec title="What you asked for"><Prose text={state.requests[iteration - 1]} /></Sec> : <>
        {run && <><FilePath base={run} rel="events.jsonl" sep={state.sep} label="event log" /><FilePath base={run} rel={`results/${selected}.json`} sep={state.sep} label="saved output" /></>}
        {built && <FilePath base={state.root} rel={`functions/${built.function_id}/${built.version}`} sep={state.sep} label="function" />}
        {running && graph.tasks.get(selected)?.kind === 'agent' && graph.tasks.get(selected)?.status !== 'done' && (() => {
          const t = graph.tasks.get(selected); const fails = mine.filter(e => e.event === 'gate_failed');
          const checks = fails.map(e => (String(e.errors && e.errors[0] || '').split(':')[0] || '').trim());
          const same = checks.length >= 2 && checks.every(c => c === checks[0]);
          const cap = (selected.match(/^(?:i\d+\.)?build\.([a-z0-9_]+)/) || [])[1];
          const current = cap ? (state.harness.agents.specialists.find(s => s.capability === cap) || {}).tier : (selected.startsWith('review') || selected === 'interfaces' ? state.harness.agents.systems_engineer.tier : 'balanced');
          const next = current === 'fast' ? 'balanced' : 'deep';
          const overridden = control.tier && control.tier[selected];
          return <Sec title="While it runs" note={`attempt ${t.attempts} of ${selected.startsWith('build') ? 4 : 3}${overridden ? ` · next attempt on the ${overridden} tier` : ''}`}>
            {same && !overridden && current !== 'deep' && <p><b className="bad">The same check has failed {checks.length} times: {checks[0]}.</b> When an agent keeps failing on something that is not a logic error in the spec, a stronger model usually clears it. The remaining attempts can run on the {next} tier.</p>}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              {current !== 'deep' && !overridden && cap && <button className="btn" onClick={() => ctl('tier', { task: selected, tier: next, capability: cap, keep: keepTier })}>Retry on the {next} tier</button>}
              {current !== 'deep' && !overridden && cap && <label className="legend"><input type="checkbox" checked={keepTier} onChange={e => setKeepTier(e.target.checked)} /> and keep it in this project's harness</label>}
              {!(control.stop || []).includes(selected) ? <button className="btn ghost" onClick={() => ctl('stop', { task: selected })}>Stop this task after the current attempt</button> : <span className="legend">Will stop after the current attempt.</span>}
            </div>
            <p className="legend">A stopped task fails and blocks what depends on it. Fix the cause, then Resume the build from the register. Finished tasks are kept.</p>
          </Sec>; })()}
        <Sec title="Every call, in both directions" note={`${mine.length} events`}><Timeline events={mine} /></Sec>
        <Sec title="What the task produced"><Result value={result} /></Sec></>}</div>
    </div>}
  </div>;
}

// ---- 2. Build system: the architecture, and the runtime drawn on top of it -------------------
function BuildSystem({ project, state, refresh, onDrill, running }) {
  const names = Object.keys(state.designs);
  const [designName, setDesignName] = useState(names.includes('baseline') ? 'baseline' : names[0]);
  const design = state.designs[designName];
  const [values, setValues] = useState({});
  const [versions, setVersions] = useState({});
  const [result, setResult] = useState(null);
  const [live, setLive] = useState({ kind: '', playing: false, shown: new Set() });
  const [tab, setTab] = useState('run');
  const [selected, setSelected] = useState(null);
  const [files, setFiles] = useState(null);
  const [emergent, setEmergent] = useState(null);
  const [sweeping, setSweeping] = useState(false);
  const [sysmlView, setSysmlView] = useState('text');
  const [popped, setPopped] = useState(false);
  useEffect(() => { if (!popped) return undefined; const k = e => e.key === 'Escape' && setPopped(false); window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [popped]);
  const [sysmlFile, setSysmlFile] = useState('');
  useEffect(() => { if (tab === 'sysml' && design && !(result && result.sysml)) fetch(`/api/file?project=${project}&path=outputs/${designName}/design.sysml`).then(r => r.ok ? r.text() : 'Not rendered yet. Run the build system.').then(setSysmlFile).catch(() => setSysmlFile('')); }, [tab, project, designName, state.iteration, result]);
  const timer = useRef(null);
  useEffect(() => { if (design) { setValues(design.values); setResult(design.result); setEmergent(null); setLive({ kind: '', playing: false, shown: new Set() }); } }, [designName, state.iteration, project]);
  const targets = new Set((state.interfaces.exchanges || []).map(x => x.target_input));
  const run = useCallback(async () => {
    clearInterval(timer.current);
    const r = await api('/api/evaluate', { project, values, versions, design: designName, usages: design && design.usages });
    setResult(r);
    let i = -1; const shown = new Set();
    setLive({ kind: 'start', playing: true, shown });
    timer.current = setInterval(() => {
      i += 1;
      if (i >= r.trace.length) { clearInterval(timer.current); setLive({ kind: 'done', playing: false, shown }); return; }
      const step = r.trace[i]; shown.add(step.kind === 'call' ? step.function_id : step.id);
      setLive({ kind: step.kind, id: step.kind === 'call' ? step.function_id : step.id, playing: true, shown: new Set(shown) });
    }, 620);
  }, [project, values, versions]);
  useEffect(() => () => clearInterval(timer.current), []);
  const onVersion = useCallback((id, v) => setVersions(prev => ({ ...prev, [id]: v })), []);
  const graph = useMemo(() => systemGraph(state, design && { ...design, values }, result, live, versions, onVersion, selected), [state, design, values, result, live, versions, selected]);
  const selectedVersion = selected && (versions[selected] || state.registry[selected]?.active);
  useEffect(() => { setFiles(null); if (selected) api(`/api/function?project=${project}&id=${selected}&version=${selectedVersion}`).then(setFiles); }, [selected, selectedVersion, project]);
  if (!design) return <div className="empty">The build system has no design instance yet. It appears when the build reaches step 9.</div>;
  const changed = k => String(values[k]) !== String(design.values[k]);
  const fixed = new Set(state.spec.parameters.filter(p => !p.parameterized).map(p => p.name));
  const parse = v => { if (v === '') return ''; try { return JSON.parse(v); } catch (x) { return v; } };
  const entry = selected && state.registry[selected];
  return <div className="main">
    <div className="canvas">
      <Flow nodes={graph.nodes} edges={graph.edges} fitKey={`sys:${graph.nodes.length}:${!!selected}`} viewKey={`sys:${project}`} onNodeClick={(_, n) => { if (n.type === 'element') setSelected(n.data.functionId); if (n.id === 'out') { setSelected(null); setTab('sysml'); } }} />
    </div>
    {selected && entry ? <div className="panel wide">
      <div className="head"><h2>{selected}</h2><small>{selectedVersion}</small><button onClick={() => setSelected(null)}>Close</button></div>
      <div className="body">
        <p>{state.interfaces.contracts.find(c => c.function_id === selected)?.question}</p>
        {['solution.py', 'test_solution.py', 'test_acceptance.py', 'manifest.json'].map(f => <div key={f}><FilePath base={state.root} rel={`functions/${selected}/${selectedVersion}/${f}`} sep={state.sep} /></div>)}
        <FilePath base={state.root} rel="registry.json" sep={state.sep} label="routing" />
        <Sec title="Versions behind this static identifier" note="calls route through the registry">
          {Object.entries(entry.versions).map(([v, m]) => <div className={'card ' + (v === entry.active ? 'ok' : 'quiet')} key={v}>
            <div className="t">{v} {v === entry.active && <span className="badge ok">active</span>}<small>request {m.iteration} · {m.reason} · {m.built_with}</small></div>
            <p className="legend">{m.gates.own_tests?.passed} agent tests · {m.gates.acceptance?.passed} of {(m.gates.acceptance?.passed || 0) + (m.gates.acceptance?.failed || 0)} worked cases ({m.evidence.user_cases} from you) · {m.evidence.measures} measures</p>
            {v !== entry.active && <button className="btn ghost" onClick={async () => { await api('/api/route', { project, id: selected, version: v }); setVersions(p => ({ ...p, [selected]: undefined })); refresh(); }}>Route {selected.split('.').pop()} to {v}</button>}
          </div>)}</Sec>
        {files && <>
          <Sec title="Schema the agent designed">{<table className="t"><tbody>{Object.entries(files.manifest.parameters || {}).map(([k, d]) => <tr key={k}><td className="mono">{k}</td><td>{d}</td></tr>)}</tbody></table>}</Sec>
          <Sec title="Design notes"><Prose text={files.manifest.design_notes} /></Sec>
          <Sec title="Implementation" note={`functions/${selected}/${selectedVersion}/solution.py`}><Code text={files.code} /></Sec>
          <details><summary>The agent's own tests</summary><Code text={files.tests} /></details>
          <details><summary>Acceptance tests the harness generated from worked cases</summary><Code text={files.acceptance} /></details></>}
      </div>
    </div> : <div className={'panel' + (tab === 'sysml' ? ' wide' : '')}>
      <div className="subtabs">{[['run', 'Run'], ['spec', 'Spec'], ['reqs', 'Requirements'], ['evidence', 'Evidence'], ['sysml', 'SysML v2']].map(([k, t]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{t}</button>)}</div>
      <div className="body">
        {tab === 'run' && <>
          <Sec title="Design instance" note="literals for the metamodel">
            <select value={designName} onChange={e => setDesignName(e.target.value)}>{names.map(n => <option key={n}>{n}</option>)}</select>
            <p className="legend">{design.description}</p>
            <FilePath base={state.root} rel={`inputs/${designName}.json`} sep={state.sep} label="inputs" /><FilePath base={state.root} rel={`outputs/${designName}`} sep={state.sep} label="outputs" /></Sec>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '8px 0' }}>
            <button className="btn" onClick={run} disabled={live.playing}>{live.playing ? 'Generating' : 'Generate the spec'}</button>
            <button className="btn ghost" onClick={() => { setValues(design.values); setVersions({}); }}>Reset</button>
            <span className="legend">The functions run, then the SysML v2 spec is written. No model is called.</span></div>
          {result && !live.playing && <>
            <p><span className={'badge ' + (result.passes ? 'ok' : 'bad')}>{result.passes ? 'spec generated · every requirement satisfied' : `spec generated · ${result.findings.length + Object.keys(result.errors).length} requirement(s) not satisfied`}</span></p>
            {result.findings.map((f, i) => <div className="card" key={i}><div className="t mono">{f.rule}</div>{f.message}</div>)}
            {Object.entries(result.errors).map(([k, v]) => <div className="card" key={k}><div className="t mono">{k}</div>Outside the evidence: {v}</div>)}
            <p className="legend">This run wrote <a href={`/api/file?project=${project}&path=outputs/${designName}/report.html`} target="_blank" rel="noreferrer">report.html</a> and <a href={`/api/file?project=${project}&path=outputs/${designName}/design.sysml`} target="_blank" rel="noreferrer">design.sysml</a>. <button className="link" onClick={() => setTab('sysml')}>Open the SysML v2 model</button></p></>}
          <Sec title="Inputs" note="the usage tree, with the values each usage carries. Edit a value, then run">
            {(() => {
              const keys = Object.keys(design.values).filter(k => !targets.has(k.split('.').pop()));
              const groups = {};                                   // usage path -> attribute keys; '' holds the design-wide values
              keys.forEach(k => { const i = k.lastIndexOf('.'); const g = i < 0 ? '' : k.slice(0, i); (groups[g] = groups[g] || []).push(k); });
              const partOf = {}; const walk = (us, prefix) => (us || []).forEach(u => { const p = prefix ? `${prefix}.${u.name}` : u.name; partOf[p] = u.part; walk(u.parts, p); });
              walk(design.usages, '');
              const paths = Object.keys(partOf).filter(p => groups[p]);
              const row = k => <tr key={k}>
                <td className="mono">{k.split('.').pop()}</td><td><input className={changed(k) ? 'changed' : ''} disabled={fixed.has(k)} value={String(values[k] ?? '')} onChange={e => setValues(v => ({ ...v, [k]: parse(e.target.value) }))} /></td>
                <td><Tag v={changed(k) ? 'user' : design.tags[k] || 'ai'} />{fixed.has(k) && <span className="badge quiet">fixed</span>}</td></tr>;
              return <>
                {paths.map(p => <div key={p} className="usage-group"><div className="usage-head"><span className="mono">{p}</span> <span className="legend">: {partOf[p]}</span></div>
                  <table className="t"><tbody>{groups[p].map(row)}</tbody></table></div>)}
                {Object.keys(groups).filter(g => g && !partOf[g]).map(p => <div key={p} className="usage-group"><div className="usage-head"><span className="mono">{p}</span></div><table className="t"><tbody>{groups[p].map(row)}</tbody></table></div>)}
                {groups[''] && <div className="usage-group"><div className="usage-head"><span className="legend">whole design</span></div><table className="t"><tbody>{groups[''].map(row)}</tbody></table></div>}
              </>; })()}</Sec>
          <Sec title="Sensitivity and interactions" note="move inputs, watch every margin. No model is called">
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button className="btn ghost" disabled={sweeping} onClick={async () => { setSweeping(true); setEmergent(await api('/api/emergent', { project, values, design: designName, usages: design.usages })); setSweeping(false); }}>{sweeping ? 'Sweeping' : emergent ? 'Sweep again' : 'Sweep'}</button>
              {emergent && <span className="legend">{emergent.inputs_moved} inputs, each ±{Math.round(emergent.step * 100)}%, then every pair together</span>}</div>
            {emergent && emergent.error && <p><b className="bad">{emergent.error}</b></p>}
            {emergent && emergent.tornado && <>
              <Tornado data={emergent} onMove={(k, v) => setValues(vals => ({ ...vals, [k]: Math.round(v * 1e6) / 1e6 }))} />
              <h4>Pass flips at one move</h4>
              {emergent.flips.length === 0 && <p className="legend">No single {Math.round(emergent.step * 100)}% change flips a result.</p>}
              {emergent.flips.map((f, i) => <div className="card" key={i}><b className="mono">{f.input}</b> {f.change} {f.passes_after ? 'fixes' : 'breaks'} <span className="mono">{f.function_id}</span>{f.cross_domain && <> <span className="badge bad">cross-element</span></>}<p className="legend">owned by {f.owner}</p></div>)}
              <h4>Second-order interactions</h4>
              <p className="legend">Two inputs moved together, against the sum of their single moves. What the sum misses is the interaction. A cross-element one is behavior no single specialist could have seen.</p>
              <Interactions data={emergent} />
              <details><summary>{emergent.couplings.length} first-order cross-element couplings</summary><table className="t"><tbody>{emergent.couplings.map((c, i) => <tr key={i}><td className="mono">{c.input}</td><td>{c.owner}</td><td className="mono">{c.affects.split('.')[1]}</td><td>{fmtNum(c.metric_before)} → {fmtNum(c.metric_after)}</td></tr>)}</tbody></table></details></>}</Sec></>}
        {tab === 'sysml' && <>
          <Sec title="SysML v2 model" note={result && result.sysml ? 'rendered by the last run of the build system' : `outputs/${designName}/design.sysml, from the last run`} />
          <div className="chips">{[['text', 'Textual notation'], ['diagram', 'Graphical notation']].map(([k, t]) => <button key={k} className={'chip ' + (sysmlView === k ? 'on' : '')} onClick={() => setSysmlView(k)}>{t}</button>)}<span className="grow" /><button className="btn ghost small" onClick={() => setPopped(true)}>Pop out</button></div>
          <FilePath base={state.root} rel={`outputs/${designName}/design.sysml`} sep={state.sep} />
          {sysmlView === 'text' ? <Code text={(result && result.sysml) || sysmlFile} />
            : <SysmlDiagram state={state} design={{ ...design, name: designName }} values={result ? result.values : values} result={result} onDrill={running ? null : onDrill} />}
          {popped && createPortal(<div className="modal-back" onClick={() => setPopped(false)}><div className="popout" onClick={e => e.stopPropagation()}>
            <div className="modal-head"><h2>SysML v2 · {designName}</h2>
              <div className="chips">{[['text', 'Textual notation'], ['diagram', 'Graphical notation']].map(([k, t]) => <button key={k} className={'chip ' + (sysmlView === k ? 'on' : '')} onClick={() => setSysmlView(k)}>{t}</button>)}</div>
              <span className="grow" /><button onClick={() => setPopped(false)}>Close · Esc</button></div>
            <div className="modal-body">{sysmlView === 'text' ? <Code text={(result && result.sysml) || sysmlFile} />
              : <SysmlDiagram state={state} design={{ ...design, name: designName }} values={result ? result.values : values} result={result} onDrill={running ? null : p => { setPopped(false); onDrill(p); }} />}</div>
          </div></div>, document.body)}</>}
        {tab === 'spec' && <>
          <Sec title={state.spec.mission_name} note={state.spec.summary} />
          <FilePath base={state.root} rel="spec.json" sep={state.sep} /><FilePath base={state.root} rel="interfaces.json" sep={state.sep} label="metamodel" />
          <table className="t"><thead><tr><th>Parameter</th><th>Value</th><th>Source</th></tr></thead><tbody>{state.spec.parameters.map(p => <tr key={p.name} title={p.rationale}>
            <td className="mono">{p.name}</td><td>{String(p.value)} {p.unit}</td><td><Tag v={p.source} />{!p.parameterized && <span className="badge quiet">fixed</span>}{p.changed_in && <span className="badge ok">request {p.changed_in}</span>}</td></tr>)}</tbody></table>
          <Sec title="How your instructions were applied">{state.spec.parameterization_notes.map((t, i) => <div className="card quiet" key={i}>{t}</div>)}</Sec>
          <Sec title="Questions for you">{state.spec.open_questions.map((t, i) => <div className="card" key={i}>{t}</div>)}</Sec></>}
        {tab === 'reqs' && state.spec.requirements.map(r => <div className="card quiet" key={r.id}><div className="t">{r.id} <Tag v={r.source} /><span className="badge quiet">v{r.version}</span><small>{r.capability}</small></div>{r.text}{r.previous && <p className="legend">Was: {r.previous}</p>}</div>)}
        {tab === 'evidence' && Object.entries(state.context).map(([cap, c]) => <div key={cap}><Sec title={cap} note={`context version ${c.version} · ${c.measures.length} measures · ${c.cases.length} worked cases`} />
          <FilePath base={state.root} rel={`context/${cap}.yaml`} sep={state.sep} /><p className="legend">{c.notes}</p>
          {c.cases.map((k, i) => <div className={'card ' + (k.source === 'user' ? 'ok' : 'quiet')} key={i}><div className="t">case {i + 1} <Tag v={k.source === 'user' ? 'user' : 'quiet'} />{k.source !== 'user' && <small>library</small>}<small>since request {k.since}</small></div>
            <span className="mono" style={{ fontSize: 11.5 }}>{k.raises ? 'must refuse' : JSON.stringify(k.expect)}</span></div>)}</div>)}
      </div>
    </div>}
  </div>;
}

// ---- 3. Versions: configuration management ----------------------------------------------------
class TabBoundary extends React.Component {
  constructor(p) { super(p); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidUpdate(prev) { if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null }); }
  render() {
    if (this.state.error) return <div className="empty"><b className="bad">This view hit an error: {String(this.state.error && this.state.error.message || this.state.error)}</b>
      <p className="legend">The rest of the page still works. Open the browser console for the stack, and send it along with runs/studio-console.log.</p></div>;
    return this.props.children;
  }
}

function RollbackCard({ project, n, meta, refresh }) {
  const [busy, setBusy] = useState(false), [err, setErr] = useState('');
  return <div className="card quiet rollback"><div className="t">Request {n} can be undone</div>
    <p className="legend">{meta.kind === 'drilldown' ? `The drill-down of ${meta.part}: its parts, the inputs it added, and the function version it built.` : 'The follow-up: its parameter, requirement, and evidence changes, and the versions it built.'} The checkpoint is history/i{n}.before. Versions stay in the registry, inactive, so an identifier can be routed to them again.</p>
    <button className="btn ghost" disabled={busy} onClick={async () => { setBusy(true); const r = await api('/api/rollback', { project }); setBusy(false); if (r.error) setErr(r.error); else refresh(); }}>{busy ? 'Rolling back' : `Roll back request ${n}`}</button>
    {err && <p className="legend bad">{err}</p>}</div>;
}

function Versions({ project, state, refresh }) {
  const [pick, setPick] = useState(null);
  const [pair, setPair] = useState(null);
  useEffect(() => { setPair(null); if (!pick) return;
    const vs = Object.keys(state.registry[pick.id].versions), i = vs.indexOf(pick.v);
    Promise.all([i > 0 ? api(`/api/function?project=${project}&id=${pick.id}&version=${vs[i - 1]}`) : null, api(`/api/function?project=${project}&id=${pick.id}&version=${pick.v}`)])
      .then(([a, b]) => setPair({ before: a, after: b, prev: vs[i - 1] })); }, [pick, project]);
  const list = a => (Array.isArray(a) ? a : []).join(', ');
  const active = a => Object.entries(a || {}).map(([k, v]) => `${k.split('.').slice(1).join('.')} ${v}`).join(', ') || 'none';
  const label = c => {
    switch (c.kind) {                                    // every entry shape the project writes; unknown fields never crash the view
      case 'function': return `${c.function_id} ${c.version}: ${c.reason || ''}`;
      case 'route': return `${c.function_id} routed to ${c.to}`;
      case 'follow-up': return c.summary || 'Follow-up';
      case 'harness': return c.summary || `Harness edited.${c.agents ? ` Agents: ${list(c.agents)}.` : ''}${c.version ? ` Version ${c.version}` : ''}`;
      case 'drill-down': return `${c.part} decomposed into ${list(c.parts)}.${(c.attributes || []).length ? ` ${c.function_id} gains ${list(c.attributes)}.` : ' No new inputs.'}`;
      case 'rollback': return `Request ${c.of_iteration} (${c.part ? `drill-down of ${c.part}` : 'follow-up'}) undone. Active: ${active(c.active)}`;
      case 'iteration': return `Request ${c.iteration} complete. Active: ${active(c.active)}`;
      default: return c.summary || JSON.stringify(c);
    }
  };
  return <div className="versions">
    <div><Sec title="Changelog" note="every change, in order" /><FilePath base={state.root} rel="changelog.json" sep={state.sep} /><FilePath base={state.root} rel="history" sep={state.sep} label="snapshots" />
      {state.rollbacks && state.rollbacks.length > 0 && <RollbackCard project={project} n={state.rollbacks[0]} meta={(state.request_meta || [])[state.rollbacks[0] - 1] || {}} refresh={refresh} />}
      <div className="timeline">{state.changelog.map((c, i) => <div className="ev" key={i}><span className={'badge ' + (c.kind === 'follow-up' || c.kind === 'drill-down' ? 'user' : c.kind === 'route' || c.kind === 'rollback' ? 'bad' : 'quiet')}>{c.kind}</span> <small className="legend">request {c.iteration} · {c.at}</small>
        <div>{label(c)}</div>{c.kind === 'follow-up' && <p className="legend">parameters: {list(c.parameters) || 'none'} · usages: {list(c.usages) || 'none'} · requirements: {list(c.requirements) || 'none'} · evidence: {Object.entries(c.evidence || {}).map(([k, v]) => `${k} v${v}`).join(', ') || 'none'} · rebuilt: {list(c.revise) || 'none'}</p>}</div>)}</div>
      <Sec title="Requirements" note="versioned with the spec" />
      {state.spec.requirements.map(r => <div className="card quiet" key={r.id}><div className="t">{r.id} <span className="badge quiet">v{r.version}</span><Tag v={r.source} /></div>{r.text}</div>)}
    </div>
    <div><Sec title="Registry" note="a static identifier routes to one active version. Click a version to compare it with the one before" /><FilePath base={state.root} rel="registry.json" sep={state.sep} />
      <table className="t"><thead><tr><th>Static identifier</th><th>Versions</th><th>Why the latest exists</th></tr></thead><tbody>
        {Object.entries(state.registry).map(([id, e]) => <tr key={id}><td className="mono">{id}</td>
          <td>{Object.keys(e.versions).map(v => <span key={v} className={'chip ' + (v === e.active ? 'on' : '')} onClick={() => setPick({ id, v })}>{v}</span>)}</td>
          <td>{(Object.values(e.versions || {}).slice(-1)[0] || {}).reason || ''}</td></tr>)}</tbody></table>
      {pick && pair && <>
        <Sec title={`${pick.id} ${pick.v}`} note={pair.prev ? `compared with ${pair.prev}` : 'first version'}>
          {state.registry[pick.id].active !== pick.v && <button className="btn" onClick={async () => { await api('/api/route', { project, id: pick.id, version: pick.v }); refresh(); }}>Route this identifier to {pick.v}</button>}
        </Sec>
        {pair.before ? <><Sec title="Implementation diff"><Diff before={pair.before.code} after={pair.after.code} /></Sec><Sec title="Test diff"><Diff before={pair.before.tests} after={pair.after.tests} /></Sec></>
          : <Sec title="Implementation"><Code text={pair.after.code} /></Sec>}</>}
    </div>
  </div>;
}

// ---- 0. Harness: what this project builds, with which agents, under which rules ---------------
function Harness({ state }) {
  const h = state.harness, A = h.agents;
  const [selected, setSelected] = useState('systems_engineer');
  const graph = useMemo(() => harnessGraph(h, selected), [h, selected]);
  const agent = selected === 'systems_engineer' ? { ...A.systems_engineer, title: 'Systems engineer' }
    : selected.startsWith('s:') ? (() => { const s = A.specialists.find(x => `s:${x.capability}` === selected); return s ? { ...s, title: `${s.capability} specialist` } : { ...A.fallback, title: 'Generic specialist' }; })()
    : selected === 'parameterizer' ? { ...A.parameterizer, title: 'Parameterizer', text: 'Turns the request into named parameters and requirements. Tags every value user or ai. Follows your instructions on what to parameterize.' }
    : selected === 'gates' ? { title: 'Gates', text: 'Code only. The shared dictionary, the module structure, the agent\'s own tests, acceptance tests from worked cases, and interface conformance.' }
    : selected === 'built' ? { title: 'Build system', text: `Generated functions with static identifiers of the form ${h.id_prefix}.<capability>.<noun>, a registry, and example inputs. It runs with no AI.` }
    : { title: 'You', text: `You describe the ${h.building} you want. You judge the build system with systems principles. You send follow-ups when it needs something more.` };
  return <div className="main">
    <div className="canvas"><Flow nodes={graph.nodes} edges={graph.edges} fitKey={`h:${graph.nodes.length}`} onNodeClick={(_, n) => setSelected(n.id)} /></div>
    <div className="panel wide">
      <div className="head"><h2>{h.name}</h2><small>harness {state.harness_version}</small></div>
      <div className="body">
        <p>{h.description}</p>
        <p className="legend">This project builds a <b>{h.building}</b>. It started from the <span className="mono">{state.kit}</span> harness definition. Change the agents in the Define view, or edit the files.</p>
        <FilePath base={state.root} rel="harness.yaml" sep={state.sep} /><FilePath base={state.root} sep={state.sep} label="project folder" />
        <Sec title={agent.title} note={agent.tier ? `${agent.tier} tier${agent.memory ? ' · long-term memory' : ''}${(agent.tools || []).length ? ` · tools: ${agent.tools.join(', ')}` : ''}` : ''}>
          {agent.instructions && <FilePath base={state.root} rel={agent.instructions} sep={state.sep} />}
          <Prose text={agent.text || 'No instruction file.'} /></Sec>
        <Sec title="Tools this harness defines" note="mapped to agents in the Define view">
          {(h.tools || []).length ? h.tools.map(t => <div className="card quiet" key={t.name}><div className="t mono">{t.signature || t.name}</div>{t.doc}{t.error && <b className="bad"> {t.error}</b>}
            <div><FilePath base={state.root} rel={`tools/${t.name}.py`} sep={state.sep} /></div>
            <p className="legend">used by: {A.specialists.filter(s => (s.tools || []).includes(t.name)).map(s => s.capability).join(', ') || 'no agent yet'}</p></div>) : <p className="legend">No tools yet. Agents work from their instructions and the toolbox helpers.</p>}</Sec>
        <Sec title="Architecture every function must follow">{h.architecture.map((t, i) => <div className="card quiet" key={i}>{t}</div>)}</Sec>
        <Sec title="Non-functional requirements">{h.non_functional.map((t, i) => <div className="card quiet" key={i}>{t}</div>)}</Sec>
      </div>
    </div>
  </div>;
}

function MemoryView({ meta, project }) {
  const [scope, setScope] = useState(project ? 'project' : 'shared');
  const [data, setData] = useState(null);
  const [query, setQuery] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const load = useCallback(async q => { setBusy(true); const qs = new URLSearchParams(); if (q) qs.set('q', q); if (project) qs.set('project', project); const r = await fetch(`/api/memory?${qs}`).then(r => r.json()).catch(e => ({ error: String(e) })); setData(r); setBusy(false); }, [project]);
  useEffect(() => { load(''); }, [load]);
  const add = async () => { if (!text.trim()) return; setBusy(true); const r = await api('/api/memory', { text, scope: scope === 'project' ? project : null }); setNote(r.error || (scope === 'project' ? `Remembered for ${project}.` : 'Remembered for every project.')); if (!r.error) setText(''); await load(''); };
  const forget = async id => { setBusy(true); const r = await api('/api/memory', { forget: id }); setNote(r.error || 'Forgotten.'); await load(query); };
  const items = data ? (data.entries || (data.hits || []).map((t, i) => ({ id: `hit${i}`, text: t, source: 'match' }))) : [];
  return <div className="main memory">
    <div className="panel wide" style={{ borderLeft: 0 }}>
      <div className="body">
        <Sec title="What the systems engineer agent remembers" note={project ? `lessons for ${project}, then the pool every project shares` : 'the pool every project shares'} />
        <p className="legend">Before each review the agent recalls lessons from this project and from the shared pool. After it, it records new ones in this project, or in the pool when a lesson is about method rather than this system. Add what you know here, one plain sentence each. {data && data.offline ? 'Offline mode uses a keyword store in place of mem0.' : ''}</p>
        {data && data.store && <FilePath base={data.store} rel="" sep={meta.sep} label="store" />}
        <div className="memrow">
          <textarea value={text} onChange={e => setText(e.target.value)} placeholder="A lesson for the systems engineer. For example: a parameter description without a unit slips past the tests; check every PARAMETERS entry for one." />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <button className="btn" disabled={busy || !text.trim()} onClick={add}>Remember</button>
            {project && <label className="legend"><input type="radio" checked={scope === 'project'} onChange={() => setScope('project')} /> for this project</label>}
            <label className="legend"><input type="radio" checked={scope === 'shared'} onChange={() => setScope('shared')} /> for every project</label>
          </div>
        </div>
        {note && <p className="legend">{note}</p>}
        <div className="memrow">
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Ask the memory the way the agent does: a few words about a situation" onKeyDown={e => e.key === 'Enter' && load(query)} />
          <button className="btn ghost" disabled={busy} onClick={() => load(query)}>Recall</button>
          {query && <button className="btn ghost" disabled={busy} onClick={() => { setQuery(''); load(''); }}>Show all</button>}
        </div>
        {data && data.error && <p><b className="bad">{data.error}</b></p>}
        {data && data.busy && <p className="legend">Agents are building, so the store is read-only until they finish.</p>}
        <Sec title={query ? `Lessons that match "${query}"` : `${items.length} lesson(s)`} />
        {busy && !data && <p className="legend">Opening the memory store. The first time loads the embedding model.</p>}
        {items.map(e => <div className="card quiet" key={e.id}><div className="t">{e.text}</div>
          <p className="legend">{e.scope && e.scope !== 'match' ? <b>{e.scope === 'shared' ? 'every project' : e.scope}</b> : null}{e.scope && e.scope !== 'match' ? ' · ' : ''}{e.source === 'person' ? 'from you' : e.source === 'match' ? 'recalled' : 'recorded by the agent'}{e.created ? ` · ${String(e.created).slice(0, 10)}` : ''}
            {e.source !== 'match' && <> · <button className="link" disabled={busy} onClick={() => forget(e.id)}>forget</button></>}</p></div>)}
        {data && !data.error && items.length === 0 && <p className="legend">Nothing yet. The first review writes the first lessons.</p>}
      </div>
    </div>
  </div>;
}

// The landing page: the projects on this computer, each a design system you can reopen where you left it,
// and the fresh start. Selecting a project here is the only way in, so each one is its own restore point.
// The landing page: the register of design systems on this computer, each a restore point, and a way
// to begin a new one. Nothing else. Selecting a project here is the only way in.
function Home({ meta, settings, onOpen, onStart, offline, setOffline, onDefine, onAbout, refresh }) {
  const [kit, setKit] = useState('cubesat');
  const [starting, setStarting] = useState(false);
  const [removing, setRemoving] = useState(null);           // { name, purge, busy, error }: the entry being deleted, awaiting confirmation
  const remove = async () => { setRemoving(r => ({ ...r, busy: true })); const r = await api('/api/project/delete', { project: removing.name, purge: removing.purge });
    if (r.error) setRemoving(x => ({ ...x, busy: false, error: r.error })); else { setRemoving(null); refresh && refresh(); } };
  const kitInfo = name => meta.kits.find(k => k.kit === name) || {};
  const [text, setText] = useState('');
  useEffect(() => { if (!text && meta.kits.length) setText(kitInfo(kit).example_request || ''); }, [meta.kits.length]);
  const when = t => { if (!t) return ''; const d = new Date(t * 1000); return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); };
  return <div className="home">
    <svg className="home-shapes" viewBox="0 0 1200 800" preserveAspectRatio="xMaxYMin slice" aria-hidden="true">
      <circle cx="980" cy="150" r="210" fill="none" stroke="#2a231f" strokeWidth="1" />
      <circle cx="980" cy="150" r="150" fill="none" stroke="#2a231f" strokeWidth="1" />
      <circle cx="980" cy="150" r="7" fill="#ff0011" />
      <line x1="640" y1="150" x2="1200" y2="150" stroke="#2a231f" strokeWidth="1" />
      <line x1="980" y1="-60" x2="980" y2="420" stroke="#2a231f" strokeWidth="1" />
      <path d="M820,400 A160,160 0 0 1 980,240" fill="none" stroke="#a27a78" strokeWidth="1" />
      <rect x="1040" y="330" width="110" height="110" fill="none" stroke="#2a231f" strokeWidth="1" transform="rotate(12 1095 385)" />
    </svg>
    <div className="home-inner">
      <header className="masthead">
        <img src="/gf-logo-dark.png" alt="Ground &amp; Form" className="home-logo" />
        <h1>GF<i>·</i>DSB</h1>
        <p className="tag">Ground &amp; Form Design System Builder</p>
        <p className="lede">Describe a thing to build. Agents write the build system: a metamodel of parts, a calc on every part worth assessing, the evidence and gates behind each. You judge it. Then the functions generate the SysML v2 spec for any instance, with no model in the loop.</p>
        <p className="legend"><button className="link" onClick={onAbout}>About · MIT License · before you build</button></p>
      </header>
      <section className="register">
        <div className="register-head"><h2>Register</h2><span className="legend">{meta.projects.length} design system{meta.projects.length === 1 ? '' : 's'} on this computer</span></div>
        <ol>
          {meta.projects.map((p, i) => <li key={p.name} className={removing && removing.name === p.name ? 'removing' : ''}>
            <div className="entry-row">
              <button className="entry" onClick={() => onOpen(p.name)}>
                <span className="num">{String(i + 1).padStart(2, '0')}</span>
                <span className="ename">{p.mission || p.name}</span>
                <span className="edetail">{p.harness || p.kit}{p.building ? `, builds a ${p.building}` : ''}. {p.built ? `${p.functions} function${p.functions === 1 ? '' : 's'}, ${p.versions} version${p.versions === 1 ? '' : 's'}, ${p.designs} design${p.designs === 1 ? '' : 's'}, request ${p.iteration}.` : p.iteration ? 'The first build did not finish. Resume inside.' : 'Empty.'}</span>
                <span className="ewhen">{when(p.updated)}</span>
                <span className="epath">projects/{p.name}</span>
              </button>
              <button className="entry-x" title={`Delete ${p.name}`} disabled={meta.running && meta.last && meta.last.project === p.name} onClick={() => setRemoving(removing && removing.name === p.name ? null : { name: p.name, purge: false })}>×</button>
            </div>
            {removing && removing.name === p.name && <div className="confirm">
              <span>Delete <b>{p.mission || p.name}</b>? The folder <code>projects/{p.name}</code> moves to <code>projects/.trash</code>, where you can move it back. Run logs stay under <code>runs/</code>.</span>
              <label className="legend"><input type="checkbox" checked={removing.purge} onChange={e => setRemoving(r => ({ ...r, purge: e.target.checked }))} /> remove it for good instead</label>
              <span className="grow" />
              <button className="btn ghost small" onClick={() => setRemoving(null)}>Keep</button>
              <button className="btn small danger" disabled={removing.busy} onClick={remove}>{removing.busy ? 'Deleting' : removing.purge ? 'Delete for good' : 'Move to trash'}</button>
              {removing.error && <span className="bad">{removing.error}</span>}
            </div>}
          </li>)}
          <li><button className={'entry new ' + (starting ? 'open' : '')} onClick={() => setStarting(s => !s)}>
            <span className="num">{String(meta.projects.length + 1).padStart(2, '0')}</span>
            <span className="ename">A new design system</span>
            <span className="edetail">Say what to build. Pick the harness that knows how.</span>
            <span className="ewhen">{starting ? 'close' : 'begin'}</span>
          </button>
          {starting && <div className="fresh">
            <textarea value={text} onChange={e => setText(e.target.value)} />
            <div className="row">
              <select value={kit} onChange={e => { setKit(e.target.value); setText(kitInfo(e.target.value).example_request || ''); }} title="The harness definition this project starts from">
                {meta.kits.map(k => <option key={k.kit} value={k.kit}>{k.kit}: builds a {k.building}</option>)}</select>
              <label className="legend"><input type="checkbox" checked={offline} onChange={e => setOffline(e.target.checked)} /> Offline: replay saved answers</label>
              <span className="grow" />
              <button className="btn ghost" onClick={onDefine}>Define a new harness first</button>
              <button className="btn" disabled={meta.running || !text.trim()} onClick={() => onStart(text, kit)}>Build it</button>
            </div>
            {settings && !settings.has_key && !offline && <p className="legend">Live builds need an API key: Settings, top right.</p>}
          </div>}</li>
        </ol>
      </section>
    </div>
  </div>;
}

function AboutNotice({ meta, onClose }) {
  useEffect(() => { const k = e => e.key === 'Escape' && onClose(); window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onClose]);
  return <div className="modal-back" onClick={onClose}><div className="popout about" onClick={e => e.stopPropagation()}>
    <div className="about-head"><img src="/gf-logo.png" alt="Ground &amp; Form" /><div><h2>GF<i>·</i>DSB</h2><p>Ground &amp; Form Design System Builder</p></div></div>
    <div className="modal-body">
      <p>A request goes in. A versioned build system comes out: a metamodel, one function per element, the evidence and gates behind each, and the SysML v2 spec they generate. Agents write the build system. You judge it. After that, nothing calls a model.</p>
      <h3>Open source, MIT License</h3>
      <p>Copyright © 2026 Ground &amp; Form Advisory, LLC. Permission is granted, free of charge, to use, copy, modify, merge, publish, distribute, sublicense, and sell copies of this software, subject to the notice in the LICENSE file.</p>
      <p className="warranty">The software is provided “as is”, without warranty of any kind, express or implied, including the warranties of merchantability, fitness for a particular purpose, and non-infringement. In no event shall the authors or copyright holders be liable for any claim, damages, or other liability arising from the software or its use.</p>
      <h3>Before you build</h3>
      <ul>
        <li>Agents write Python during a build, and the gates run it on this computer. Review generated functions before you rely on them.</li>
        <li>Live builds call the model provider on your API key and cost money. Offline replays call nothing.</li>
        <li>Generated functions are engineering estimates with the evidence you supplied. They are not a substitute for qualified review.</li>
      </ul>
      {meta.repo && <p className="legend">Installed at <code>{meta.repo}</code>. The license is in <code>LICENSE</code> there.</p>}
    </div>
    <div className="about-foot"><button className="btn" onClick={onClose}>I understand</button><span className="legend">Esc closes. Click the logo to read this again.</span></div>
  </div></div>;
}

export default function App() {
  const [meta, setMeta] = useState({ projects: [], running: false, offline: false, kits: [], tool_template: '', repo: '', sep: '/' });
  const [project, setProjectRaw] = useState(() => { try { return localStorage.getItem('gfdsb.project') || ''; } catch (e) { return ''; } });
  const setProject = name => { try { if (name) localStorage.setItem('gfdsb.project', name); else localStorage.removeItem('gfdsb.project'); } catch (e) { /* private window */ } setProjectRaw(name); };
  const [state, setState] = useState(null);
  const [tab, setTab] = useState('orchestration');
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [kit, setKit] = useState('cubesat');
  const [offline, setOffline] = useState(null);
  const [settings, setSettings] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [runError, setRunError] = useState('');
  const [drill, setDrill] = useState(null);                  // { part, text }: a drill-down being composed
  const [showAbout, setShowAbout] = useState(() => { try { return !localStorage.getItem('gfdsb.notice.v1'); } catch (e) { return true; } });
  const closeAbout = () => { try { localStorage.setItem('gfdsb.notice.v1', String(Date.now())); } catch (e) { /* private window */ } setShowAbout(false); };
  const wanted = useRef(null);
  useEffect(() => { if (!runError) return undefined; const t = setTimeout(() => setRunError(''), 12000); return () => clearTimeout(t); }, [runError]);
  const loadSettings = useCallback(async () => setSettings(await api('/api/settings')), []);
  useEffect(() => { loadSettings(); }, [loadSettings]);
  const refresh = useCallback(async () => {
    const m = await api('/api/projects').catch(() => null);
    if (!m) return;
    setMeta(m);
    const waiting = wanted.current && Date.now() - wanted.current.at < 30000 && project === wanted.current.name;   // a project that is still being created
    const name = project && (m.projects.some(p => p.name === project) || waiting) ? project : '';   // no project means the landing page
    if (name !== project) setProject(name);
    if (name) { const s = await api(`/api/state?project=${name}`); if (!s.error) setState(prev => JSON.stringify(prev) === JSON.stringify(s) ? prev : s); }
  }, [project]);
  useEffect(() => { refresh(); const t = setInterval(refresh, 1500); return () => clearInterval(t); }, [refresh]);
  const off = offline ?? meta.offline;
  const start = async (kind, override) => { setOpen(false); setDrill(null); setTab('orchestration'); const r = await api('/api/run', { kind, text: override ? override.text : text, project, offline: off, kit: override ? override.kit : kit, part: override ? override.part : undefined }); setRunError(r && r.error ? r.error : ''); if (r && r.project) { wanted.current = { name: r.project, at: Date.now() }; setProject(r.project); setState(null); } setTimeout(refresh, 800); };
  const goHome = () => { setProject(''); setState(null); setOpen(false); setTab('orchestration'); };
  const ready = state && state.built;
  const stale = meta.api !== undefined && meta.api !== 4 || (meta.projects.length > 0 && meta.api === undefined);
  const unfinished = !stale && state && !state.built && (state.requests || []).length > 0 && !meta.running;
  const kitInfo = name => meta.kits.find(k => k.kit === name) || {};
  const openBar = () => { setOpen(o => !o); const k = ready ? state.kit || 'cubesat' : kit; setKit(k); setText(ready ? kitInfo(k).example_followup || '' : kitInfo(k).example_request || ''); };
  return <div className="app"><Defs />
    <header className="top">
      <a className="brand" href="#" onClick={e => { e.preventDefault(); setShowAbout(true); }} title="About GFDSB">
        <img src="/gf-mark.png" alt="Ground &amp; Form" />
        <span className="word"><span className="mark">GF<i>·</i>DSB</span><small>Ground &amp; Form · Design System Builder</small></span>
      </a>
      <button className="home-btn" onClick={goHome} title="All design systems">{project ? '‹ Projects' : 'Projects'}</button>
      {project && <div className="tabs">{[['define', 'Define'], ['harness', 'Harness'], ['orchestration', 'Orchestration'], ['system', 'Build system'], ['versions', 'Versions'], ['memory', 'Memory']].map(([k, t]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{t}</button>)}</div>}
      <span className="grow" />
      <span className="status">{meta.running ? 'agents are building' : project && ready ? `${state.spec.mission_name} · request ${state.iteration}` : project && unfinished ? `${state.name} · first build did not finish` : project ? project : `${meta.projects.length} design system${meta.projects.length === 1 ? '' : 's'}`}</span>
      <button className="btn ghost" onClick={() => setShowSettings(true)}>{settings && !settings.has_key ? 'Settings · add a key' : 'Settings'}</button>
      {project && ready && <button className="btn" onClick={openBar}>Follow up</button>}
    </header>
    {showAbout && <AboutNotice meta={meta} onClose={closeAbout} />}
    {stale && <div className="notice"><span>This page is newer than the server behind it. Stop <code>python studio.py ui</code> with Ctrl+C in its window and start it again, then reload.</span></div>}
    {meta.failure && !meta.running && <div className="notice"><span>The last build process stopped with an error: <code>{meta.failure}</code> Full log in runs/studio-console.log.</span></div>}
    {(unfinished || runError) && <div className="notice">
      <span>{runError || `The first build of ${state.name} stopped before it produced a build system. Missing: ${(state.missing || []).join('; ')}. Resume keeps every task that finished and runs the rest again. The red task in Orchestration says why it stopped.`}</span>
      {unfinished && <button className="btn" onClick={() => start('resume')}>Resume the build</button>}
      {unfinished && <button className="btn ghost" onClick={() => start('rebuild')}>Start over</button>}
      {runError && <button className="btn ghost small" onClick={() => setRunError('')}>Dismiss</button>}
    </div>}
    {open && <div className="requestbar">
      <textarea value={text} onChange={e => setText(e.target.value)} />
      <div className="side">
        <button className="btn" disabled={meta.running} onClick={() => start('followup')}>Send follow-up</button>
        <label className="legend"><input type="checkbox" checked={off} onChange={e => setOffline(e.target.checked)} /> Offline: replay saved answers</label>
        <span className="legend">A follow-up changes this design system. A new one starts from Projects.</span>
      </div>
    </div>}
    {drill && state && (() => { const part = (state.interfaces.structure || []).find(p => p.name === drill.part) || {}; const who = (state.harness.agents.specialists || []).find(s => s.capability === part.capability);
      return <div className="requestbar drill">
        <div className="side wide">
          <div className="t">Drill down into {drill.part}</div>
          <p className="legend">The {part.capability} specialist{who ? '' : ' (generic)'} names the parts inside it and any attribute the assessment should take from them. The systems engineer reviews the proposal with memory. If the {part.capability} function gains inputs, it is rebuilt as a new version and re-verified against the same worked cases. Depth limit {state.harness.structure_depth || 3}. Every step is in Orchestration, and Versions can roll the whole request back.</p>
        </div>
        <textarea value={drill.text} placeholder="Optional guidance: what to split by, what to leave out, what the assessment should start using." onChange={e => setDrill(d => ({ ...d, text: e.target.value }))} />
        <div className="side">
          <button className="btn" disabled={meta.running} onClick={() => start('drilldown', { text: drill.text, part: drill.part })}>Decompose {drill.part}</button>
          <label className="legend"><input type="checkbox" checked={off} onChange={e => setOffline(e.target.checked)} /> Offline: replay saved answers</label>
          <button className="btn ghost small" onClick={() => setDrill(null)}>Cancel</button>
        </div>
      </div>; })()}
    {showSettings && settings && <SettingsPanel settings={settings} reload={loadSettings} onClose={() => setShowSettings(false)} />}
    {!project && tab !== 'define' ? <Home meta={meta} settings={settings} offline={off} setOffline={setOffline} onAbout={() => setShowAbout(true)} refresh={refresh}
        onOpen={name => { setProject(name); setState(null); setTab('orchestration'); }}
        onStart={(t, k) => start('new', { text: t, kit: k })} onDefine={() => setTab('define')} />
      : tab === 'define' ? <Designer meta={meta} state={state} settings={settings} onUse={name => { setKit(name); setText(kitInfo(name).example_request || ''); goHome(); }} />
      : !state ? <div className="empty">Opening {project}…</div>
      : <ReactFlowProvider key={tab}><TabBoundary resetKey={`${tab}:${project}`}>
        {tab === 'harness' && (state.harness ? <Harness state={state} /> : null)}
        {tab === 'orchestration' && <Orchestration project={project} state={state} running={meta.running} />}
        {tab === 'system' && (Object.keys(state.registry || {}).length && Object.keys(state.designs || {}).length ? <BuildSystem project={project} state={state} refresh={refresh} running={meta.running} onDrill={part => { setDrill({ part, text: '' }); setOpen(false); }} /> : <div className="empty">Nothing is built yet. Watch the Orchestration tab.</div>)}
        {tab === 'memory' && <MemoryView meta={meta} project={project} />}
        {tab === 'versions' && (Object.keys(state.registry || {}).length ? <Versions project={project} state={state} refresh={refresh} /> : <div className="empty">Nothing to version yet.</div>)}
      </TabBoundary></ReactFlowProvider>}
  </div>;
}
