// Node and edge types, and the two graphs: orchestration (tasks) and build system (elements and exchanges).
import React from 'react';
import { Handle, Position, EdgeLabelRenderer, getBezierPath, getSmoothStepPath } from '@xyflow/react';

export const STEPS = [null, ['Request', 'you'], ['Parameterize', 'agent'], ['Interfaces', 'SE agent'], ['Build functions', 'specialists'],
  ['Validate', 'code'], ['Review', 'SE agent + memory'], ['Redesign', 'new versions'], ['Verify interfaces', 'code'],
  ['Inputs', 'agent'], ['Generate the spec', 'code']];
const bare = id => id.replace(/^i\d+\./, '');
export function stepOf(id) {
  const t = bare(id);
  if (t === 'request') return 1;
  if (t === 'parameterize' || t === 'triage') return 2;
  if (t === 'interfaces' || t === 'assign' || t === 'decompose' || /^decompose\.\d+$/.test(t)) return 3;   // a drill-down changes the architecture too
  if (t.startsWith('decompose.review')) return 6;
  if (/^build\..*\.v\d+$/.test(t)) return 7;
  if (t.startsWith('build.')) return 4;
  if (t.startsWith('validate')) return 5;
  if (t.startsWith('review')) return 6;
  if (t.startsWith('verify')) return 8;
  if (t === 'inputs') return 9;
  return 10;
}

const Check = () => <svg className="mark" viewBox="0 0 18 18"><path d="M3 9.5 L7.5 14 L15 4.5" /></svg>;
const Cross = () => <svg className="mark x" viewBox="0 0 18 18"><path d="M4 4 L14 14 M14 4 L4 14" /></svg>;

function TaskNode({ data }) {
  return <div className={`task ${data.status} ${data.kind === 'agent' ? 'agent' : ''} ${data.selected ? 'selected' : ''}`}>
    <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
    <div className="meta">{data.kind === 'agent' ? `agent · ${data.model || ''}` : data.kind === 'you' ? 'you' : 'code'}</div>
    <div className="id">{data.label}</div>
    <div className="note">{data.note}</div>
    {data.attempts > 1 && <span className="tries">×{data.attempts}</span>}
    {data.status === 'done' && <Check />}{data.status === 'failed' && <Cross />}{(data.status === 'running' || data.status === 'retry') && <span className="spin" />}
    <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
  </div>;
}
const LaneNode = ({ data }) => <div className="lane"><b>{data.n}</b>{data.title}<small>{data.who}</small></div>;

function ElementNode({ data }) {
  const e = data.entry, run = data.run;
  const state = data.live ? 'running' : run?.result ? (run.result.passes ? 'pass' : 'fail') : run?.error ? 'fail' : '';
  return <div className={`el ${state} ${data.selected ? 'selected' : ''}`}>
    <Handle type="target" id="in" position={Position.Left} style={{ opacity: 0 }} />
    <h4>{data.root || data.capability}{data.usages > 1 ? <small className="count"> × {data.usages}</small> : null}</h4>
    <div className="fid">{data.functionId}{data.parts ? ` · ${data.parts} parts inside` : ''}{data.usages > 1 ? ` · runs on ${data.usages} usages` : ''}</div>
    <div className="row">routes to
      <select className="nodrag" value={data.version} onChange={ev => data.onVersion(data.functionId, ev.target.value)} onClick={ev => ev.stopPropagation()}>
        {Object.keys(e.versions).map(v => <option key={v} value={v}>{v}{v === e.active ? ' (active)' : ''}</option>)}
      </select>
      {state === 'pass' && <span className="badge ok">passes</span>}{state === 'fail' && <span className="badge bad">{run?.error ? 'refused' : 'fails'}</span>}
    </div>
    <div className="metric">{run?.result ? data.metric : run?.error ? String(run.error).slice(0, 60) : ''}</div>
    <Handle type="source" id="xo" position={Position.Right} style={{ opacity: 0, top: '35%' }} />
    <Handle type="source" id="out" position={Position.Right} style={{ opacity: 0, top: '52%' }} />
    <Handle type="target" id="xi" position={Position.Right} style={{ opacity: 0, top: '70%' }} />
  </div>;
}
const HubNode = ({ data }) => <div className={`hub ${data.live ? 'live' : ''}`}>
  <Handle type="target" position={Position.Left} style={{ opacity: 0 }} /><h4>{data.title}</h4><p>{data.text}</p>
  <Handle type="source" position={Position.Right} style={{ opacity: 0 }} /><Handle type="source" id="xo" position={Position.Bottom} style={{ opacity: 0 }} />
</div>;

// An edge that draws itself in, then carries a moving dot while data flows along it.
function DrawnEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data = {} }) {
  const loop = data.loop;
  let path, lx, ly;
  if (loop) {                                         // both ends on the right side: bow outward
    const bow = 70 + Math.abs(targetY - sourceY) * 0.22 + (data.lane || 0) * 40;
    path = `M ${sourceX} ${sourceY} C ${sourceX + bow} ${sourceY}, ${targetX + bow} ${targetY}, ${targetX} ${targetY}`;
    lx = Math.max(sourceX, targetX) + bow * 0.74; ly = (sourceY + targetY) / 2;
  } else if (data.step) [path, lx, ly] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 10 });
  else [path, lx, ly] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  return <>
    <path id={id} d={path} pathLength="1" className={`edge-base ${data.active ? 'hot' : data.done ? 'cool' : ''}`} markerEnd="url(#arrow)" />
    {data.active && <><path d={path} className="edge-flow" /><circle r="4.5" className="edge-dot"><animateMotion dur="0.9s" repeatCount="indefinite" path={path} /></circle></>}
    {data.label && <EdgeLabelRenderer><div className="elabel-pos" style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}>
      <div className={`elabel ${data.active ? 'live' : ''}`} title={data.title}>{data.label}</div></div></EdgeLabelRenderer>}
  </>;
}
// ---- the harness a project defines: who does what, in what order ---------------------------
export function harnessGraph(h, selected) {
  const A = h.agents, specs = A.specialists.length ? A.specialists : [{ capability: 'any capability', tier: A.fallback.tier, generic: true }];
  const mid = 60 + (specs.length - 1) * 52;
  const node = (id, x, y, data) => ({ id, type: 'task', position: { x, y }, data: { status: 'done', attempts: 0, selected: selected === id, ...data } });
  const nodes = [
    node('you', 0, mid, { kind: 'you', label: 'You', note: `Describe a ${h.building}. Judge the result` }),
    node('parameterizer', 250, mid, { kind: 'agent', model: A.parameterizer.tier, label: 'Parameterizer', note: 'Request to parameters. Tags user or ai' }),
    node('systems_engineer', 500, mid, { kind: 'agent', model: A.systems_engineer.tier, label: 'Systems engineer', note: 'Interfaces, review, long-term memory' }),
    ...specs.map((s, i) => node(`s:${s.capability}`, 770, 60 + i * 104, { kind: 'agent', model: s.tier, label: s.generic ? 'Generic specialist' : `${s.capability} specialist`, note: (s.tools || []).length ? `tools: ${s.tools.join(', ')}` : 'writes one function and its tests' })),
    node('gates', 1040, mid, { kind: 'function', label: 'Gates', note: 'Dictionary, tests, acceptance, conformance' }),
    node('built', 1290, mid, { kind: 'function', label: 'Build system', note: `${h.id_prefix}.* functions behind a registry. Runs with no AI` })];
  const edge = (a, b, extra = {}) => ({ id: `${a}>${b}`, source: a, target: b, type: 'drawn', data: { done: true, ...extra } });
  const edges = [edge('you', 'parameterizer'), edge('parameterizer', 'systems_engineer'),
    ...specs.flatMap(s => [edge('systems_engineer', `s:${s.capability}`), edge(`s:${s.capability}`, 'gates')]), edge('gates', 'built')];
  return { nodes, edges };
}

export const nodeTypes = { task: TaskNode, lane: LaneNode, element: ElementNode, hub: HubNode };
export const edgeTypes = { drawn: DrawnEdge };

// ---- orchestration: tasks and their states, rebuilt from the event log ------------------
export function taskGraph(events, selected, iteration, meta = {}) {
  const tasks = new Map();
  const get = id => { if (!tasks.has(id)) tasks.set(id, { id, deps: [], status: 'pending', kind: 'function', model: '', note: '', attempts: 0, parent: null }); return tasks.get(id); };
  const drill = meta && meta.kind === 'drilldown';
  const first = drill ? `i${iteration}.decompose` : iteration > 1 ? `i${iteration}.triage` : 'parameterize';
  const req = get('request'); Object.assign(req, { kind: 'you', status: events.length ? 'done' : 'running', note: drill ? `Drill down into ${meta.part}` : iteration > 1 ? 'A follow-up after testing' : 'Describe what you want built' });
  for (const e of events) {
    if (e.event === 'plan') e.tasks.forEach(get);
    if (!e.task) continue;
    const t = get(e.task);
    if (e.event === 'started') Object.assign(t, { status: 'running', kind: e.kind, model: e.model, deps: e.deps || [], attempts: 1, note: '' });
    else if (e.event === 'api_request') t.note = `API request ${e.turn} to ${e.model}`;
    else if (e.event === 'api_response') t.note = e.error ? 'API error' : `response ${e.turn}: ${e.stop_reason}`;
    else if (e.event === 'model_call') { t.note = e.offline ? 'replaying a saved answer' : `calling ${e.model}`; if (t.status === 'retry') t.status = 'running'; }
    else if (e.event === 'tool_call') t.note = `tool ${e.tool}()`;
    else if (e.event === 'shared_read') t.note = `read dictionary v${e.version}`;
    else if (e.event === 'shared_commit') t.note = `dictionary v${e.from_version} to v${e.version}`;
    else if (e.event === 'shared_conflict') t.note = `dictionary refused: ${e.conflicts.map(c => c.name).join(', ')}`;
    else if (e.event === 'memory_search') t.note = `recalled ${e.hits} lesson(s)`;
    else if (e.event === 'gate_output') t.note = `${e.check}: ${String(e.text).trim().split('\n').pop()}`.slice(0, 80);
    else if (e.event === 'gate_failed' || e.event === 'attempt_failed') { t.status = 'retry'; t.attempts += 1; t.note = e.where ? `crashed at ${e.where}` : 'sent back with feedback'; }
    else if (e.event === 'returned') t.note = e.note || '';
    else if (e.event === 'done') { t.status = 'done'; t.note = e.note || t.note; (e.spawned || []).forEach(id => { const s = get(id); if (!s.parent) s.parent = t.id; }); }
    else if (e.event === 'failed') { t.status = 'failed'; t.note = String(e.error).slice(0, 80); }
    else if (e.event === 'paused') { t.status = 'retry'; t.note = 'paused before the next attempt'; }
    else if (e.event === 'resumed_by_person') t.note = 'resumed';
    else if (e.event === 'tier_override') t.note = `retrying on the ${e.tier} tier`;
    else if (e.event === 'stopped_by_person') { t.status = 'failed'; t.note = 'stopped by you'; }
    else if (e.event === 'network_retry') t.note = `network trouble, retrying in ${e.wait_s} s`;
    else if (e.event === 'output_budget_raised') t.note = `answer did not fit, retrying with ${e.max_tokens} output tokens`;
    else if (e.event === 'resumed') { t.status = 'done'; t.note = 'kept from the earlier attempt'; (e.spawned || []).forEach(id => { const s = get(id); if (!s.parent) s.parent = t.id; }); }
  }
  if (tasks.has(first)) get(first).deps = ['request'];
  const columns = {}, nodes = [], edges = [];
  const at = n => ({ x: (n - 1) * 262, y: 0 });        // one row of stages, so a stage with many tasks only grows downward
  for (let n = 1; n <= 10; n++) nodes.push({ id: `lane${n}`, type: 'lane', position: at(n), data: { n, title: STEPS[n][0], who: STEPS[n][1] }, selectable: false, draggable: false });
  for (const t of tasks.values()) {
    const s = stepOf(t.id), row = (columns[s] = (columns[s] || 0) + 1) - 1;
    nodes.push({ id: t.id, type: 'task', position: { x: at(s).x, y: at(s).y + 74 + row * 104 },
      data: { ...t, label: bare(t.id), selected: selected === t.id } });
    const from = t.deps.length ? t.deps : t.parent ? [t.parent] : [];
    for (const d of from) if (tasks.has(d)) edges.push({ id: `${d}>${t.id}`, source: d, target: t.id, type: 'drawn',
      data: { active: t.status === 'running' || t.status === 'retry', done: t.status === 'done' } });
  }
  return { nodes, edges, tasks };
}

// ---- the build system: router, elements, exchanges. The same picture is the runtime ---------
const METRIC = r => { const k = Object.keys(r).find(k => k !== 'passes' && k !== 'usages' && typeof r[k] === 'number' && (/margin|headroom|rdm|reserve|slack/.test(k) || ['temperature_c'].includes(k)) && !/^required/.test(k)); return k ? `${k} = ${r[k]}${r.usages > 1 ? ` (worst of ${r.usages})` : ''}` : ''; };
export function systemGraph(state, design, result, live, versions, onVersion, selected) {
  const contracts = state.interfaces.contracts || [], exchanges = state.interfaces.exchanges || [];
  const structure = state.interfaces.structure || [];
  const byName = Object.fromEntries(structure.map(p => [p.name, p]));
  const rootOf = cap => (structure.find(p => p.capability === cap && !p.parent) || {}).name || '';
  const partOf = c => c.part || rootOf(c.capability) || c.capability;
  const partsIn = part => structure.filter(p => { let cur = p; const seen = new Set(); while (cur && !seen.has(cur.name)) { seen.add(cur.name); const parent = cur.parent || (cur.specializes && byName[cur.specializes] ? byName[cur.specializes].parent : ''); if (parent === part) return true; cur = byName[parent]; } return false; }).length;
  // the calc an exchange end lands on: a calc on that part, else on the nearest enclosing part, else the capability's root
  const calcOn = name => { const part = byName[name] ? name : rootOf(name); let cur = part; const seen = new Set();
    while (cur && !seen.has(cur)) { seen.add(cur); const hit = contracts.find(c => partOf(c) === cur); if (hit) return hit.function_id; cur = (byName[cur] || {}).parent || ((byName[cur] || {}).specializes ? (byName[byName[cur].specializes] || {}).parent : ''); }
    return null; };
  const user = Object.values(design?.tags || {}).filter(t => t === 'user').length, total = Object.keys(design?.values || {}).length;
  const nodes = [
    { id: 'inputs', type: 'hub', position: { x: 0, y: 250 }, data: { title: design ? design.name : 'Design inputs', text: `${total} values · ${user} from you · ${total - user} filled in by an agent`, live: live.kind === 'start' } },
    { id: 'router', type: 'hub', position: { x: 270, y: 250 }, data: { title: 'Registry', text: 'static identifier → active version', live: live.kind === 'call' } }];
  const nReq = (state.spec.requirements || []).length;
  const unmet = result ? result.findings.length + Object.keys(result.errors).length : 0;
  const verdict = live.playing || !result ? 'generating' : result.passes ? `all ${nReq} requirements satisfied` : `${unmet} of ${nReq} requirement(s) not satisfied`;
  nodes.push({ id: 'out', type: 'hub', position: { x: 1260, y: 250 }, data: { title: 'design.sysml', text: result ? `SysML v2 spec of this ${state.harness.building} · ${contracts.length} parts · ${verdict}` : `the SysML v2 spec, once the ${contracts.length} functions have run`, live: live.kind === 'done' } });
  const edges = [{ id: 'in>router', source: 'inputs', target: 'router', type: 'drawn', data: { active: live.kind === 'start', done: true } }];
  contracts.forEach((c, i) => {
    const entry = state.registry[c.function_id];
    if (!entry) return;
    const run = result?.functions?.[c.function_id], isLive = live.kind === 'call' && live.id === c.function_id;
    const part = partOf(c), usages = run && run.usages ? Object.keys(run.usages).length : 0;
    nodes.push({ id: c.function_id, type: 'element', position: { x: 590, y: 20 + i * 138 },
      data: { capability: c.capability, root: part, parts: partsIn(part), usages, functionId: c.function_id, entry, version: versions[c.function_id] || entry.active, onVersion, run: live.shown?.has(c.function_id) || !live.playing ? run : null,
              live: isLive, metric: run?.result ? METRIC(run.result) : '', selected: selected === c.function_id } });
    edges.push({ id: `router>${c.function_id}`, source: 'router', target: c.function_id, targetHandle: 'in', type: 'drawn',
      data: { active: isLive, done: true, label: isLive ? `${c.function_id.split('.').slice(1).join('.')} @ ${versions[c.function_id] || entry.active}${usages > 1 ? ` × ${usages}` : ''}` : '' } });
    const wrote = run && (!live.playing || live.shown?.has(c.function_id));
    edges.push({ id: `${c.function_id}>out`, source: c.function_id, sourceHandle: 'out', target: 'out', type: 'drawn',
      data: { active: isLive, done: !!wrote, label: isLive ? `writes ${part}` : '' } });
  });
  exchanges.forEach((x, i) => {
    const got = result?.exchanges?.filter(r => r.id === x.id) || [], isLive = live.kind === 'exchange' && live.id === x.id;
    const show = !live.playing || live.shown?.has(x.id);
    const from = calcOn(x.source_element), to = calcOn(x.target_element);
    if (!to || !nodes.some(n => n.id === to)) return;
    const fromEl = !!from && nodes.some(n => n.id === from);
    const shown = got.length === 1 ? got[0].value : got.length > 1 ? `${got.length} usages` : undefined;
    edges.push({ id: x.id, source: fromEl ? from : 'inputs', sourceHandle: 'xo', target: to, targetHandle: 'xi', type: 'drawn',
      data: { loop: fromEl, lane: i, active: isLive, done: true, title: `${x.source_element} → ${x.target_element}: ${x.target_input} = ${x.expression}\n${x.rationale}`,
              label: `${x.id} ${x.target_input}${show && shown !== undefined ? ` = ${shown}` : ''}` } });
  });
  return { nodes, edges };
}
