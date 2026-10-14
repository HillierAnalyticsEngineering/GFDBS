"""Views of one design: a report and a SysML v2 model. Plain functions, no model calls."""
from __future__ import annotations

import html
import re

from .project import Project
from .sysml import sysml_text


def _ident(name: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", str(name)) if p]
    text = (parts[0].lower() + "".join(p[:1].upper() + p[1:] for p in parts[1:])) if parts else "x"
    return text if text[0].isalpha() else "x" + text


def _type(value) -> str:
    return "Boolean" if isinstance(value, bool) else "Real" if isinstance(value, (int, float)) else "String"


def _literal(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value) if isinstance(value, (int, float)) else f'"{value}"'


def sysml(project: Project, design: dict, result: dict) -> str:
    """SysML v2 textual notation. The build system itself renders this; see studio/sysml.py."""
    return sysml_text(project.spec(), project.interfaces(), project.registry(), project.harness()["building"], design, result, project.name)


def report(project: Project, design: dict, result: dict) -> str:
    spec, interfaces, registry = project.spec(), project.interfaces(), project.registry()
    e = lambda v: html.escape(str(v))
    tag = lambda t: f'<span class="tag {e(t)}">{e(t)}</span>'
    rows = "".join(f"<tr><td><code>{e(k)}</code></td><td>{e(v)}</td><td>{tag(design['tags'].get(k, 'ai'))}</td></tr>"
                   for k, v in sorted(design["values"].items()))
    xrows = "".join(f"<tr><td>{e(x['id'])}</td><td>{e(x['from'])} to {e(x['to'])}</td><td><code>{e(x['target_input'])} = {e(x.get('expression', ''))}</code></td>"
                    f"<td>{e(x.get('value', x.get('error', '')))}</td></tr>" for x in result["exchanges"])
    cards = ""
    for c in interfaces["contracts"]:
        run = result["functions"].get(c["function_id"], {})
        ok = run.get("result", {}).get("passes")
        usages = run.get("usages", {})
        if len(usages) > 1:                                 # a calc that ran on several usages: one row per usage
            body = "".join(f"<tr><td><code>{e(path)}</code></td><td>{'passes' if u.get('result', {}).get('passes') else e(u.get('error') or 'does not pass')}</td>"
                           f"<td>{', '.join(f'{e(k)} {e(v)}' for k, v in u.get('result', {}).items() if k != 'passes')}</td></tr>" for path, u in usages.items())
        else:
            body = "".join(f"<tr><td><code>{e(k)}</code></td><td>{e(v)}</td></tr>" for k, v in run.get("result", {}).items())
        gates = registry.get(c["function_id"], {}).get("versions", {}).get(run.get("version", ""), {})
        ev = gates.get("evidence", {})
        cards += (f'<div class="card {"ok" if ok else "bad"}"><h3>{e(run.get("part") or c.get("part") or c["capability"])} <small>{e(c["function_id"])} {e(run.get("version", ""))}{(" · " + str(len(usages)) + " usages") if len(usages) > 1 else ""}</small></h3>'
                  f'<p>{e(c["question"])}</p><p><b>{"Passes" if ok else e(run.get("error") or "Does not pass")}</b></p><table>{body}</table>'
                  + "".join(f'<p class="finding">{e(f["rule"])}{(" on " + e(f["usage"])) if f.get("usage") else ""}: {e(f["message"])}</p>' for f in run.get("findings", []))
                  + f'<p class="ev">Evidence behind this function: {ev.get("cases", 0)} worked cases ({ev.get("user_cases", 0)} from you), {ev.get("measures", 0)} measures.</p></div>')
    reqs = "".join(f"<tr><td>{e(r['id'])}</td><td>{e(r['text'])}</td><td>{tag(r['source'])}</td><td>v{r.get('version', 1)}</td></tr>" for r in spec["requirements"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(spec.get('mission_name', project.name))}: {e(design['name'])}</title>
<style>body{{font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif;background:#ffeadc;color:#2a231f;margin:0;padding:32px 48px;max-width:1100px}}
h1{{font:400 34px Georgia,serif;margin:0}}h2{{font:400 22px Georgia,serif;border-bottom:2px solid #ff0011;padding-bottom:4px;margin-top:34px}}
h3{{margin:0;font-size:17px}}h3 small{{font-weight:400;color:#951218;font-family:Consolas,monospace}}
table{{border-collapse:collapse;width:100%;background:#fff;margin:6px 0}}td,th{{padding:5px 10px;border-bottom:1px solid #f0ddd0;text-align:left;vertical-align:top}}
.card{{background:#fff;border-left:5px solid #2f6b3a;padding:12px 16px;margin:10px 0}}.card.bad{{border-color:#ff0011}}.finding{{color:#ff0011;font-weight:600}}.ev{{color:#a27a78;font-size:13px}}
.tag{{font-size:11px;letter-spacing:.08em;text-transform:uppercase;padding:1px 7px;color:#fff;background:#2a231f}}.tag.ai{{background:#951218}}.tag.exchange{{background:#a27a78}}
.verdict{{display:inline-block;padding:4px 14px;color:#fff;background:{'#2f6b3a' if result['passes'] else '#ff0011'};font-weight:700;letter-spacing:.08em;text-transform:uppercase}}</style></head><body>
<h1>{e(spec.get('mission_name', project.name))} · {e(design['name'])}</h1><p>{e(design.get('description', ''))}</p>
<p><span class="verdict">{'meets every requirement' if result['passes'] else f"{len(result['findings']) + len(result['errors'])} requirement(s) not met"}</span></p>
<p>Computed by the build system with no model call. Values tagged <span class="tag ai">ai</span> were filled in by an agent and should be confirmed.</p>
<h2>Assessments</h2>{cards}<h2>Exchanges between elements</h2><table><tr><th></th><th>Between</th><th>Expression</th><th>Value</th></tr>{xrows}</table>
<h2>Requirements</h2><table><tr><th></th><th>Text</th><th>Source</th><th>Version</th></tr>{reqs}</table>
<h2>Design inputs</h2><table><tr><th>Name</th><th>Value</th><th>Source</th></tr>{rows}</table></body></html>"""
