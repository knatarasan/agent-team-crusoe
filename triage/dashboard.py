"""Self-contained HTML dashboard of all runs: reports/dashboard.html (no server, no internet needed)."""

import csv
import json
from datetime import datetime
from html import escape
from pathlib import Path

import yaml

from triage.config import GOLD_CACHE, PLAYBOOK_DIR, REPORTS_DIR, RUNS_DIR

STATUS_COLOR = {"baseline": "var(--muted)", "kept": "var(--good)", "rolled back": "var(--bad)"}


def _read_csv(p: Path) -> list[dict]:
    return list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []


def _load_runs(runs_dir: Path) -> list[dict]:
    runs = []
    for row in _read_csv(runs_dir / "history.csv"):
        d = runs_dir / f"run_{int(row['run']):03d}"
        summary = json.loads((d / "summary.json").read_text()) if (d / "summary.json").exists() else {}
        results = {r["s.no"]: r for r in _read_csv(d / "results.csv")}
        runs.append({**row, "n": int(row["run"]), "summary": summary, "results": results})
    return runs


def _pct(v) -> float:
    return float(str(v).rstrip("%")) / (100 if str(v).endswith("%") else 1) if v not in ("", None) else 0.0


def _heat(v: float) -> str:
    """Score 0.5..1 -> red..green background."""
    t = max(0.0, min(1.0, (v - 0.5) / 0.5))
    return f"hsl({int(t * 120)} 65% var(--heat-l))"


# --- charts ------------------------------------------------------------------------------------------------

def _line_chart(runs: list[dict]) -> str:
    w, h, pl, pr, pt, pb = 760, 300, 48, 20, 16, 40
    exact = [_pct(r["exact_match"]) for r in runs]
    avg = [_pct(r["avg_score"]) for r in runs]
    best, b = [], 0.0
    for r, a in zip(runs, avg):
        if r["status"] != "rolled back":
            b = a
        best.append(b)
    lo = max(0.0, min(exact + avg) - 0.1)
    lo = int(lo * 10) / 10
    n = len(runs)

    def x(i):
        return pl + (w - pl - pr) * (i / max(1, n - 1)) if n > 1 else (w + pl - pr) / 2

    def y(v):
        return pt + (h - pt - pb) * (1 - (v - lo) / (1 - lo))

    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Score per run">']
    for k in range(int(lo * 10), 11):
        v = k / 10
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="{pl - 8}" y="{y(v) + 4:.1f}" class="axis" text-anchor="end">{v:.0%}</text>')
    for i, r in enumerate(runs):
        out.append(f'<text x="{x(i):.1f}" y="{h - 14}" class="axis" text-anchor="middle">run {r["n"]}</text>')

    def path(vals, cls, step=False):
        pts = []
        for i, v in enumerate(vals):
            if step and i:
                pts.append(f"{x(i):.1f},{y(vals[i - 1]):.1f}")
            pts.append(f"{x(i):.1f},{y(v):.1f}")
        return f'<polyline points="{" ".join(pts)}" class="{cls}"/>'

    out += [path(best, "l-best", step=True), path(avg, "l-avg"), path(exact, "l-exact")]
    for i, r in enumerate(runs):
        for vals, cls, label in ((avg, "p-avg", "avg score"), (exact, "p-exact", "exact match")):
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(vals[i]):.1f}" r="5" class="{cls} s-{r["status"].replace(" ", "-")}">'
                       f'<title>run {r["n"]} ({r["status"]}): {label} {vals[i]:.1%}</title></circle>')
    out.append("</svg>")
    return "".join(out)


def _category_heatmap(runs: list[dict]) -> str:
    cats = sorted({c for r in runs for c in r["summary"].get("by_category", {})})
    head = "".join(f"<th>run {r['n']}</th>" for r in runs)
    rows = []
    for c in cats:
        cells = []
        for r in runs:
            v = r["summary"].get("by_category", {}).get(c)
            cells.append("<td>—</td>" if v is None else f'<td class="heat" style="background:{_heat(v)}">{v:.0%}</td>')
        rows.append(f"<tr><th class='rowh'>{escape(c)}</th>{''.join(cells)}</tr>")
    return f"<table class='heatmap'><tr><th></th>{head}</tr>{''.join(rows)}</table>"


def _claim_grid(runs: list[dict], gold_routes: dict[str, str]) -> str:
    ids = sorted({cid for r in runs for cid in r["results"]}, key=lambda c: (gold_routes.get(c, "~"), int(c) if c.isdigit() else 0))
    head = "".join(f"<th>{r['n']}</th>" for r in runs)
    rows, prev_cat = [], None
    for cid in ids:
        cat = gold_routes.get(cid, "unknown")
        if cat != prev_cat:
            rows.append(f"<tr class='cat'><th colspan='{len(runs) + 2}'>{escape(cat)}</th></tr>")
            prev_cat = cat
        first = next((r["results"][cid] for r in runs if cid in r["results"]), {})
        cells, before = [], None
        for r in runs:
            res = r["results"].get(cid)
            if not res:
                cells.append("<td></td>")
                continue
            score, ok = _pct(res["evaluation"]), not res["mistakes"]
            change = "" if before is None or before == ok else (" fixed" if ok else " broke")
            before = ok
            tip = f"run {r['n']} · {res['evaluation']}\n{res['agent_outcome']}\n" + (
                f"mistakes: {res['mistakes'].replace(';', ', ')}" if res["mistakes"] else "exact match")
            cells.append(f'<td class="cell{change}" style="background:{_heat(score)}" title="{escape(tip)}">'
                         f'{"✓" if ok else res["evaluation"]}</td>')
        desc = escape(first.get("description", ""))
        rows.append(f"<tr><th class='rowh' title='{desc}'>#{cid} <span class='st'>{escape(first.get('state', ''))}</span></th>"
                    f"<td class='desc' title='{desc}'>{desc}</td>{''.join(cells)}</tr>")
    return (f"<table class='grid'><tr><th>claim</th><th>description</th>{head}</tr>{''.join(rows)}</table>")


def _timeline(runs: list[dict], state: dict) -> str:
    tested = {e["run"]: e for e in state.get("edit_history", [])}
    cards = []
    for r in reversed(runs):
        s = r["summary"]
        usage = s.get("usage", {})
        edits = tested.get(r["n"], {}).get("edits", [])
        items = "".join(f"<li><code>{escape(e['edit'])}</code><div class='why'>{escape(e.get('rationale', ''))}</div></li>"
                        for e in edits) or "<li class='muted'>No new edits (baseline or re-run of the best config).</li>"
        cards.append(f"""
        <article class="card">
          <header><h3>Run {r['n']} · config v{r['config_version']}</h3>
            <span class="badge" style="background:{STATUS_COLOR.get(r['status'], 'var(--muted)')}">{escape(r['status'])}</span></header>
          <p class="nums">avg <b>{_pct(r['avg_score']):.1%}</b> · exact <b>{_pct(r['exact_match']):.0%}</b> ·
             wrongful denials <b>{r['wrongful_denials']}</b> · {usage.get('calls', r.get('calls', '?'))} calls ·
             {int(usage.get('output_tokens', 0) or 0):,} out tokens</p>
          <h4>Edits this run tested</h4><ul>{items}</ul>
        </article>""")
    pending = state.get("pending_edits", [])
    if pending:
        items = "".join(f"<li><code>{escape(p['edit'])}</code><div class='why'>{escape(p.get('rationale', ''))}</div></li>"
                        for p in pending)
        cards.insert(0, f"""<article class="card next"><header><h3>Next run will test · config v{state.get('next_version')}</h3>
            <span class="badge" style="background:var(--accent)">pending</span></header><ul>{items}</ul></article>""")
    return "".join(cards)


def _best_config(state: dict, playbook_dir: Path) -> str:
    best = state.get("best")
    if not best:
        return ""
    p = playbook_dir / f"v{best['version']}.yaml"
    if not p.exists():
        return ""
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    rules = [r for r in cfg.get("rules", []) if r.get("status") == "active"]
    rows = "".join(f"<tr><td>{escape(r['id'])}</td><td>{escape(r['role'])}</td><td>{escape(r['state'])}</td>"
                   f"<td>{escape(r['text'])}</td></tr>" for r in rules) or "<tr><td colspan='4' class='muted'>No rules yet.</td></tr>"
    instr = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in cfg.get("role_instructions", {}).items())
    instr_html = f"<h4>Role prompt add-ons</h4><table class='list'>{instr}</table>" if instr else ""
    return (f"<h2>Best config: v{best['version']} (from run {best['run']})</h2>"
            f"<table class='list'><tr><th>id</th><th>role</th><th>state</th><th>rule</th></tr>{rows}</table>{instr_html}")


# --- page --------------------------------------------------------------------------------------------------

def build_dashboard(runs_dir: Path = RUNS_DIR, out: Path = REPORTS_DIR / "dashboard.html",
                    playbook_dir: Path = PLAYBOOK_DIR, gold_cache: Path = GOLD_CACHE) -> Path:
    runs = _load_runs(runs_dir)
    state = json.loads((runs_dir / "state.json").read_text()) if (runs_dir / "state.json").exists() else {}
    gold_routes = {}
    if gold_cache.exists():
        for line in gold_cache.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                gold_routes[e["claim_id"]] = e["gold"]["route"]

    if not runs:
        body = "<p class='muted'>No runs yet. Start with <code>uv run triage run</code>.</p>"
    else:
        first, best = runs[0], (state.get("best") or {})
        best_row = next((r for r in runs if r["n"] == best.get("run")), runs[-1])
        tiles = [
            ("Runs", str(len(runs)), f"{sum(r['status'] == 'kept' for r in runs)} kept · "
                                      f"{sum(r['status'] == 'rolled back' for r in runs)} rolled back"),
            ("Exact match", f"{_pct(first['exact_match']):.0%} → {_pct(best_row['exact_match']):.0%}", "baseline → best run"),
            ("Avg score", f"{_pct(first['avg_score']):.1%} → {_pct(best_row['avg_score']):.1%}", "cost-weighted, baseline → best"),
            ("Wrongful denials", f"{first['wrongful_denials']} → {best_row['wrongful_denials']}", "baseline → best run"),
            ("Best config", f"v{best.get('version', '?')}", f"from run {best.get('run', '?')}"),
        ]
        tiles_html = "".join(f"<div class='tile'><div class='lbl'>{a}</div><div class='val'>{b}</div><div class='sub'>{c}</div></div>"
                             for a, b, c in tiles)
        body = f"""
        <section class="tiles">{tiles_html}</section>
        <section><h2>Improvement curve</h2>
          <div class="legend"><span class="k k-exact"></span>exact match <span class="k k-avg"></span>avg cost-weighted score
            <span class="k k-best"></span>best so far <span class="dot s-kept"></span>kept <span class="dot s-rolled-back"></span>rolled back</div>
          {_line_chart(runs)}</section>
        <section><h2>Score by category</h2>{_category_heatmap(runs)}</section>
        <section><h2>Claim grid</h2>
          <p class="muted">Each cell is one claim in one run: ✓ = exact match, otherwise the claim's score. Hover for the decision and mistakes.
          <span class="fixed-k">green outline</span> = fixed vs the previous run, <span class="broke-k">red outline</span> = broke.</p>
          <div class="scroll">{_claim_grid(runs, gold_routes)}</div></section>
        <section class="two"><div><h2>Run timeline</h2>{_timeline(runs, state)}</div>
          <div>{_best_config(state, playbook_dir)}</div></section>"""

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Triage runs</title>
<style>
:root{{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1f;--muted:#8a8a8e;--line:#e3e3e0;--accent:#3b6fd8;--good:#2f9e5b;--bad:#d64545;
--exact:#3b6fd8;--avg:#e08a1e;--best:#9a9aa0;--heat-l:82%;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--panel:#1f1f22;--ink:#ececee;--muted:#8d8d93;--line:#333338;
--exact:#6f9cf0;--avg:#f0a54a;--best:#77777d;--heat-l:30%;color-scheme:dark}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,system-ui,Segoe UI,Roboto,sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:24px 16px 64px}} h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 10px}}
h3{{font-size:14px;margin:0}} h4{{font-size:12px;margin:10px 0 4px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
.muted{{color:var(--muted)}} code{{font:12px ui-monospace,Menlo,monospace;white-space:normal}}
section{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:4px 18px 18px;margin-top:16px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;background:none;border:0;padding:0}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}}
.lbl{{color:var(--muted);font-size:12px}} .val{{font-size:22px;font-weight:600;margin:4px 0}} .sub{{color:var(--muted);font-size:12px}}
.chart{{width:100%;height:auto}} .grid{{stroke:var(--line)}} .axis{{fill:var(--muted);font-size:11px}}
polyline{{fill:none;stroke-width:2.5}} .l-exact{{stroke:var(--exact)}} .l-avg{{stroke:var(--avg)}} .l-best{{stroke:var(--best);stroke-dasharray:6 5}}
circle{{stroke-width:2.5}} .p-exact{{fill:var(--exact)}} .p-avg{{fill:var(--avg)}} circle.s-rolled-back{{stroke:var(--bad)}} circle.s-kept{{stroke:var(--good)}}
circle.s-baseline{{stroke:var(--panel)}}
.legend{{color:var(--muted);font-size:12px;display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}}
.k{{display:inline-block;width:18px;height:3px;margin-right:4px;vertical-align:middle}} .k-exact{{background:var(--exact)}} .k-avg{{background:var(--avg)}}
.k-best{{background:repeating-linear-gradient(90deg,var(--best) 0 5px,transparent 5px 9px)}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;border:2.5px solid;margin-right:4px}} .dot.s-kept{{border-color:var(--good)}} .dot.s-rolled-back{{border-color:var(--bad)}}
table{{border-collapse:collapse}} .heatmap td,.heatmap th{{padding:6px 12px;text-align:center}} .heatmap td.heat{{border-radius:4px;border:2px solid var(--panel)}}
.rowh{{text-align:left;font-weight:500;white-space:nowrap}} .scroll{{overflow:auto;max-height:620px}}
.grid th{{position:sticky;top:0;background:var(--panel);font-weight:500;padding:4px 6px;font-size:12px}}
.grid td{{padding:0}} .grid .cell{{width:42px;height:24px;text-align:center;font-size:11px;border:2px solid var(--panel);cursor:default}}
.grid .cell.fixed{{outline:2px solid var(--good);outline-offset:-3px}} .grid .cell.broke{{outline:2px solid var(--bad);outline-offset:-3px}}
.grid tr.cat th{{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding-top:12px;position:static}}
.grid .desc{{max-width:360px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted);font-size:12px;padding:0 10px}}
.st{{color:var(--muted);font-size:11px}} .fixed-k{{outline:2px solid var(--good);padding:0 4px}} .broke-k{{outline:2px solid var(--bad);padding:0 4px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:24px}} @media (max-width:900px){{.two{{grid-template-columns:1fr}}}}
.card{{border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:10px}} .card.next{{border-color:var(--accent)}}
.card header{{display:flex;justify-content:space-between;align-items:center;gap:8px}} .nums{{margin:6px 0;color:var(--muted)}} .nums b{{color:var(--ink)}}
.badge{{color:#fff;font-size:11px;padding:2px 8px;border-radius:999px;white-space:nowrap}}
ul{{margin:0;padding-left:18px}} li{{margin:4px 0}} .why{{color:var(--muted);font-size:12px}}
.list td,.list th{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}}
</style></head><body><main>
<h1>Claims triage team · run-by-run improvement</h1>
<p class="muted">Every run scores the same {len(next(iter(runs), {}).get('results', {})) or 'full'} claims. Generated {datetime.now():%Y-%m-%d %H:%M} from <code>runs/</code>.</p>
{body}
</main></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
