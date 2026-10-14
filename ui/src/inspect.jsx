// Renderers for payloads: prompts, API round trips, code, tests, structured results.
import React from 'react';

const esc = s => String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
// Tokenizer. The first rule that matches at a position wins. Rules must not contain capturing groups.
function paint(text, rules) {
  const re = new RegExp(rules.map(r => `(${r[0].source})`).join('|'), 'g');
  let out = '', last = 0, m;
  while ((m = re.exec(text))) {
    if (!m[0]) { re.lastIndex++; continue; }
    const k = m.slice(1).findIndex(g => g !== undefined);
    out += esc(text.slice(last, m.index)) + `<span class="${rules[k][1]}">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}
const PY = [[/#[^\n]*/, 'c'], [/"""[\s\S]*?"""/, 's'], [/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/, 's'], [/@\w+(?:\.\w+)*/, 'o'],
  [/\b(?:def|class|return|if|elif|else|for|while|in|not|and|or|import|from|as|with|try|except|finally|raise|assert|lambda|pass|None|True|False|is)\b/, 'k'],
  [/\b(?:test_\w+|[A-Za-z_]\w*(?=\())/, 'fn'], [/-?\b\d+(?:\.\d+)?(?:e-?\d+)?\b/, 'n']];
const JS = [[/"(?:[^"\\]|\\.)*"(?=\s*:)/, 'key'], [/"(?:[^"\\]|\\.)*"/, 's'], [/-?\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\b/, 'n'], [/\b(?:true|false|null)\b/, 'k']];
const SYSML = [[/\/\*[\s\S]*?\*\/|\/\/[^\n]*/, 'c'], [/<'[^'\n]*'>/, 'o'], [/"[^"\n]*"/, 's'],
  [/\b(?:package|private|import|part|def|attribute|requirement|connection|connect|to|satisfy|by|doc)\b/, 'k'], [/:>>|::|:/, 'o'], [/-?\b\d+(?:\.\d+)?\b/, 'n'], [/\b[A-Z][A-Za-z0-9_]*\b/, 'fn']];
const TESTS = [[/\b(?:\d+ passed|passed|ok)\b/, 'good'], [/\b(?:\d+ failed|\d+ errors?|FAILED|failed|refused|AssertionError)\b|^E .*$/m, 'bad'], [/-?\b\d+(?:\.\d+)?\b/, 'n']];
const PROSE = [[/\b(?:V-[A-Z]+|R-\d{3}|X-\d+|cubesat\.[a-z_]+\.[a-z_]+)\b/, 'o'], [/-?\d+(?:\.\d+)?\s?(?:%|(?:percent|Wh|W|kg|g|dBi|dB|MHz|kbps|Mbps|km|mm|m2|degC|years|krad)\b)/, 'n']];

export const Code = ({ text, lang = 'py', dark = true, wrap = false }) =>
  <pre className={(dark ? 'dark ' : '') + (wrap ? 'wrap' : '')} dangerouslySetInnerHTML={{ __html: paint(String(text ?? ''), { py: PY, json: JS, sysml: SYSML, tests: TESTS, prose: PROSE }[lang]) }} />;
export const Json = ({ value }) => <Code text={JSON.stringify(value, null, 2)} lang="json" dark={false} />;
export const Prose = ({ text }) => <pre className="wrap" dangerouslySetInnerHTML={{ __html: paint(String(text ?? ''), PROSE) }} />;
export const Sec = ({ title, note, children }) => <><h3 className="sec">{title}{note && <small>{note}</small>}</h3>{children}</>;
export const Tag = ({ v }) => <span className={'badge ' + v}>{v}</span>;

function Messages({ messages }) {
  return (messages || []).map((m, i) => <div className="card quiet" key={i}><div className="t">{m.role || 'assistant'}</div>
    {(m.content || []).map((b, j) => b.text !== undefined ? <Prose key={j} text={b.text} />
      : b.toolUse ? <div key={j}><span className="badge">tool_use</span> <b>{b.toolUse.name}</b><Json value={b.toolUse.input} /></div>
      : b.toolResult ? <div key={j}><span className="badge ok">tool_result</span><Json value={b.toolResult.content} /></div>
      : <Json key={j} value={b} />)}</div>);
}

// Line diff by longest common subsequence. Small files only.
export function diffLines(a, b) {
  const A = a.split('\n'), B = b.split('\n'), n = A.length, m = B.length;
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let k = m - 1; k >= 0; k--) L[i][k] = A[i] === B[k] ? L[i + 1][k + 1] + 1 : Math.max(L[i + 1][k], L[i][k + 1]);
  const out = []; let i = 0, k = 0;
  while (i < n && k < m) { if (A[i] === B[k]) { out.push([' ', A[i]]); i++; k++; } else if (L[i + 1][k] >= L[i][k + 1]) out.push(['-', A[i++]]); else out.push(['+', B[k++]]); }
  while (i < n) out.push(['-', A[i++]]); while (k < m) out.push(['+', B[k++]]);
  return out;
}
export function Diff({ before, after }) {
  const rows = diffLines(before, after), changed = rows.filter(r => r[0] !== ' ').length;
  if (!changed) return <p className="legend">No differences.</p>;
  const keep = new Set(); rows.forEach((r, i) => { if (r[0] !== ' ') for (let d = -2; d <= 2; d++) keep.add(i + d); });
  return <pre>{rows.map((r, i) => keep.has(i) ? <span key={i} className={r[0] === '+' ? 'add' : r[0] === '-' ? 'del' : ''}>{r[0]} {r[1]}{'\n'}</span>
    : keep.has(i - 1) ? <span key={i} className="c">  ...{'\n'}</span> : null)}</pre>;
}

// What a task produced, by shape.
export function Result({ value }) {
  if (value === null || value === undefined) return <p className="legend">No saved output yet. A task is saved when it finishes and passes its gate.</p>;
  if (value.module_code !== undefined) return <>
    {value.dictionary && <Sec title="Shared dictionary">{value.dictionary.ok
      ? <p><span className="badge ok">committed</span> version {value.dictionary.from_version} to {value.dictionary.version}. Added {value.dictionary.added.length}, adopted {value.dictionary.adopted.length}.</p>
      : <p><span className="badge bad">refused</span> {value.dictionary.conflicts.map(c => `${c.name}: ${c.yours} against ${c.existing} from ${c.owner}`).join('; ')}</p>}</Sec>}
    <Sec title="Design notes from the specialist agent"><Prose text={value.design_notes} /></Sec>
    <Sec title="The function it wrote" note={`${value.function_id || ''} ${value.version || ''}`}><Code text={value.module_code} /></Sec>
    <details><summary>The tests it wrote</summary><Code text={value.test_code} /></details>
    <details><summary>Terms it declared</summary><Json value={value.terms} /></details></>;
  if (value.verdict !== undefined) return <>
    <p><span className={'badge ' + (value.verdict === 'accept' ? 'ok' : 'bad')}>{value.verdict}</span></p>
    <Sec title="Assessment"><Prose text={value.assessment} /></Sec>
    <Sec title="Emergent effects">{(value.emergent_effects || []).map((t, i) => <div className="card quiet" key={i}><Prose text={t} /></div>)}</Sec>
    <Sec title="Redesign requested">{(value.redesign || []).length ? value.redesign.map((r, i) => <div className="card" key={i}><div className="t mono">{r.function_id}</div><Prose text={r.instruction} /></div>) : <p className="legend">none</p>}</Sec>
    <Sec title="Lessons stored in long-term memory">{(value.lessons || []).map((t, i) => <div className="card ok" key={i}>{t}</div>)}</Sec></>;
  if (value.parameters && value.requirements) return <>
    <Sec title="Parameters" note={`${value.parameters.filter(p => p.source === 'ai').length} filled in by the agent`}>
      <table className="t"><tbody>{value.parameters.map(p => <tr key={p.name}><td className="mono">{p.name}</td><td>{String(p.value)} {p.unit}</td><td><Tag v={p.source} />{!p.parameterized && <span className="badge quiet">fixed</span>}</td><td>{p.rationale}</td></tr>)}</tbody></table></Sec>
    <Sec title="Requirements"><table className="t"><tbody>{value.requirements.map(r => <tr key={r.id}><td>{r.id}</td><td>{r.text}</td><td><Tag v={r.source} /></td></tr>)}</tbody></table></Sec>
    <Sec title="How it applied your instructions">{value.parameterization_notes.map((t, i) => <div className="card quiet" key={i}>{t}</div>)}</Sec>
    <Sec title="Questions for you">{value.open_questions.map((t, i) => <div className="card" key={i}>{t}</div>)}</Sec></>;
  if (value.contracts) return <>
    <Sec title="Proposed architecture"><Prose text={value.architecture} /></Sec>
    <Sec title="Exchanges between elements">{value.exchanges.map(x => <div className="card" key={x.id}><div className="t">{x.id} <small>{x.source_element} to {x.target_element}</small></div><code>{x.target_input} = {x.expression}</code><p className="legend">{x.rationale}</p></div>)}</Sec>
    <Sec title="Function contracts">{value.contracts.map(c => <details key={c.function_id}><summary className="mono">{c.function_id}</summary><p>{c.question}</p><Json value={{ inputs: c.inputs.map(t => `${t.name} [${t.unit}]${t.optional ? ' optional' : ''}`), outputs: c.outputs.map(t => `${t.name} [${t.unit}]`) }} /></details>)}</Sec></>;
  return <Json value={value} />;
}

// Every event of one task, in order: both directions of every call.
export function Timeline({ events }) {
  return events.map((e, i) => {
    const head = (label, cls = '') => <div className="t">{label} <small>t = {e.t} s</small>{cls && <span className={'badge ' + cls}>{cls === 'ok' ? 'pass' : 'sent back'}</span>}</div>;
    switch (e.event) {
      case 'started': return <div className="card quiet" key={i}>{head('Harness starts the task')}<p className="legend">{e.kind}{e.model ? `, ${e.model} tier` : ''}. Receives: {(e.deps || []).join(', ') || 'nothing'}</p></div>;
      case 'model_call': return e.offline ? <div className="card" key={i}>{head('Agent to model: replayed offline')}<details><summary>System prompt</summary><Prose text={e.system} /></details><details><summary>Prompt</summary><Prose text={e.prompt} /></details></div> : null;
      case 'api_request': return <div className="card" key={i}>{head(`Agent to Anthropic API: request ${e.turn}`)}<p className="legend">POST /v1/messages · {e.model} · tools offered: {(e.tools || []).join(', ') || 'none'}</p>
        {e.system && <details><summary>System prompt</summary><Prose text={e.system} /></details>}<details open={e.turn > 1}><summary>{e.turn === 1 ? 'Prompt' : 'Messages added: tool results going back'}</summary><Messages messages={e.new_messages} /></details></div>;
      case 'usage': return <div className="card quiet" key={i}>{head('Tokens for this agent turn')}<p className="legend">{e.model} · {e.input_tokens} in, {e.output_tokens} out</p></div>;
      case 'api_response': return <div className="card ok" key={i}>{head(`Anthropic API to agent: response ${e.turn}`)}{e.error ? <Prose text={e.error} /> : <><p className="legend">stop reason {e.stop_reason}{e.usage ? ` · ${e.usage.inputTokens} tokens in, ${e.usage.outputTokens} out` : ''}</p><Messages messages={[{ role: 'assistant', content: e.content }]} /></>}</div>;
      case 'model_return': return <div className="card ok" key={i}>{head('Model to agent: saved answer')}<p className="legend">{e.model}</p></div>;
      case 'tool_call': return <div className="card" key={i}>{head(`Agent calls ${e.tool}()`)}<Json value={e.input} /></div>;
      case 'tool_result': { let out = e.result; try { out = JSON.parse(out); } catch (x) { /* text */ } return <div className="card ok" key={i}>{head(`${e.tool}() returns`)}<Json value={out} /></div>; }
      case 'shared_read': return <div className="card quiet" key={i}>{head('Reads the shared dictionary')}<p className="legend">version {e.version}, {(e.terms || []).length} terms. No lock is held while the model works.</p></div>;
      case 'shared_commit': return <div className="card ok" key={i}>{head('Dictionary commit accepted')}<p className="legend">version {e.from_version} to {e.version}. Added {e.added.length}: {e.added.join(', ') || 'none'}. Adopted {e.adopted.length}.</p></div>;
      case 'shared_conflict': return <div className="card" key={i}>{head('Dictionary commit refused', 'bad')}{e.conflicts.map(c => <p key={c.name}><b className="mono">{c.name}</b>: this agent used {c.yours}. {c.owner} already defined it as {c.existing}.</p>)}</div>;
      case 'memory_search': return <div className="card quiet" key={i}>{head('Recalls lessons from long-term memory')}{(e.lessons || []).length ? e.lessons.map((l, k) => <p key={k}>{l}</p>) : <p className="legend">no stored lessons yet</p>}</div>;
      case 'memory_add': return <div className="card ok" key={i}>{head('Stores lessons in long-term memory')}{(e.lessons || []).map((l, k) => <p key={k}>{l}</p>)}</div>;
      case 'gate_output': return <div className="card quiet" key={i}>{head(`Gate runs: ${e.check}`)}<Code text={e.text} lang="tests" dark={false} wrap /></div>;
      case 'gate_failed': return <div className="card" key={i}>{head(`Gate fails attempt ${e.attempt}. Errors go back as feedback`, 'bad')}{(e.errors || []).map((x, k) => <Code key={k} text={x} lang="tests" dark={false} wrap />)}</div>;
      case 'gate_passed': return <div className="card ok" key={i}>{head('Gate passes', 'ok')}</div>;
      case 'attempt_failed': case 'failed': return <div className="card" key={i}>{head(e.event === 'failed' ? 'Task failed' : 'Attempt failed', e.where ? 'harness crash' : 'bad')}<Prose text={e.error} />
        {e.where && <p className="legend">Crashed in the harness at <code>{e.where}</code>. The agent did nothing wrong, so the attempt is not retried. Full traceback below.</p>}
        {e.trace && <details open={!!e.where}><summary>traceback</summary><pre className="trace" style={{ whiteSpace: 'pre-wrap', fontSize: 11 }}>{e.trace}</pre></details>}</div>;
      case 'returned': return <div className="card quiet" key={i}>{head('Task returns to the harness')}<p className="legend">{e.note}</p></div>;
      case 'done': return <div className="card ok" key={i}>{head('Saved by the harness')}{(e.spawned || []).length > 0 && <p className="legend">Added to the plan: {e.spawned.join(', ')}</p>}</div>;
      default: return null;
    }
  });
}
