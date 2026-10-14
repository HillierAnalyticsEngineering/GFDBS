import React, { useMemo, useState } from 'react';

// A tornado chart per function: one bar per input, the margin at -10% on the left of the base
// and at +10% on the right, largest swing on top. Hover for the numbers. Click a half-bar to
// put that moved value into the inputs table, so the sweep leads straight to a run.
const DOWN = '#c4161c', UP = '#1f78b4';

const fmt = v => (v === null || v === undefined ? '?' : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toFixed(3));

export function Tornado({ data, onMove }) {
  const fids = Object.keys(data.tornado);
  const [fid, setFid] = useState(fids.find(f => data.tornado[f].rows.length) || fids[0]);
  const [hover, setHover] = useState(null);
  const [all, setAll] = useState(false);
  const t = data.tornado[fid];
  const rows = useMemo(() => (t ? (all ? t.rows : t.rows.filter(r => r.swing > 0).slice(0, 10)) : []), [t, all]);
  if (!t) return null;
  const W = 560, rowH = 26, left = 170, right = 40, top = 22;
  const H = top + rows.length * rowH + 28;
  const span = Math.max(...rows.map(r => Math.max(Math.abs(r.low - t.base), Math.abs(r.high - t.base))), 1e-9);
  const plot = W - left - right, mid = left + plot / 2, scale = (plot / 2) / span;
  const x = v => mid + (v - t.base) * scale;
  const pct = Math.round(data.step * 100);
  return <div className="tornado">
    <div className="chips">{fids.map(f => <button key={f} className={'chip ' + (f === fid ? 'on' : '')} onClick={() => setFid(f)}>
      {data.tornado[f].capability}<small>{fmt(data.tornado[f].base)}</small></button>)}</div>
    <p className="legend">Margin of <span className="mono">{fid}</span> when one input moves {pct}% down <i style={{ color: DOWN }}>■</i> or up <i style={{ color: UP }}>■</i>. Base {fmt(t.base)}. Inputs owned by another element are marked ⇄. Click a bar to apply that move.</p>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`Tornado chart of ${fid}`} onMouseLeave={() => setHover(null)}>
      <line x1={mid} x2={mid} y1={top - 6} y2={H - 22} stroke="#9c8f88" strokeWidth="1" />
      <text x={mid} y={H - 6} textAnchor="middle" className="axis">base {fmt(t.base)}</text>
      <text x={left} y={H - 6} textAnchor="start" className="axis">{fmt(t.base - span)}</text>
      <text x={left + plot} y={H - 6} textAnchor="end" className="axis">{fmt(t.base + span)}</text>
      {rows.map((r, i) => {
        const y = top + i * rowH;
        const hx = x(r.high), lx = x(r.low);
        const bar = (from, to, color, key, label) => {
          const a = Math.min(from, to), w = Math.max(Math.abs(to - from), 1.5);
          return <rect key={key} x={a} y={y + 5} width={w} height={rowH - 11} rx="3" fill={color} opacity={hover && hover.i === i && hover.k === key ? 1 : 0.82}
            style={{ cursor: 'pointer' }} onMouseEnter={() => setHover({ i, k: key, label })} onClick={() => onMove && onMove(r.input, r.value * (key === 'hi' ? 1 + data.step : 1 - data.step))}>
            <title>{label}</title></rect>;
        };
        return <g key={r.input}>
          <text x={left - 8} y={y + rowH / 2 + 4} textAnchor="end" className={'lab ' + (r.cross ? 'cross' : '')}>{r.cross ? '⇄ ' : ''}{r.input}</text>
          {bar(mid, lx, DOWN, 'lo', `${r.input} -${pct}% → ${fmt(r.low)}`)}
          {bar(mid, hx, UP, 'hi', `${r.input} +${pct}% → ${fmt(r.high)}`)}
          <text x={left + plot + 6} y={y + rowH / 2 + 4} className="axis">{fmt(r.swing)}</text>
        </g>;
      })}
    </svg>
    {hover && <p className="legend hov">{hover.label}</p>}
    {t.rows.length > rows.length && <button className="btn ghost small" onClick={() => setAll(a => !a)}>{all ? 'Top ten only' : `All ${t.rows.length} inputs`}</button>}
  </div>;
}

export function Interactions({ data }) {
  const items = data.interactions || [];
  if (!items.length) return <p className="legend">No pair of inputs moves a margin by more than the sum of their single moves. The functions behave additively at {Math.round(data.step * 100)}%.</p>;
  return <table className="t"><thead><tr><th>pair</th><th>on</th><th>single effects</th><th>together adds</th><th></th></tr></thead><tbody>
    {items.slice(0, 12).map((r, i) => <tr key={i}>
      <td className="mono">{r.inputs[0]} × {r.inputs[1]}<br /><span className="legend">{r.owners[0]} · {r.owners[1]}</span></td>
      <td className="mono">{r.function_id.split('.')[1]}</td>
      <td>{fmt(r.main_a)}, {fmt(r.main_b)}</td>
      <td><b>{r.interaction > 0 ? '+' : ''}{fmt(r.interaction)}</b> <span className="legend">({Math.round(r.relative * 100)}%)</span></td>
      <td>{r.cross_domain && <span className="badge bad">cross-element</span>}</td>
    </tr>)}</tbody></table>;
}
