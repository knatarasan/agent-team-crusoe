"""Serve or export the demo UI using saved results. Never makes inference calls."""

import csv
import json
import shutil
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from triage.config import PLAYBOOK_DIR, REPORTS_DIR, ROOT, RUNS_DIR

ASSETS = ROOT / "dashboard"


def snapshot(runs_dir: Path = RUNS_DIR, playbook_dir: Path = PLAYBOOK_DIR) -> dict:
    """Prefer current run artifacts; otherwise use the explicitly labelled recording."""
    history = runs_dir / "history.csv"
    if not history.exists():
        data = json.loads((ASSETS / "recording.json").read_text(encoding="utf-8"))
        data["origin"] = "Archived report · commit 1455bf4"
        data["recorded"] = True
        return data
    with history.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    state_path = runs_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    edits = {int(e["run"]): e.get("edits", []) for e in state.get("edit_history", [])}
    runs = []
    for row in rows:
        n = int(row["run"])
        folder = runs_dir / f"run_{n:03d}"
        summary_path = folder / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        runs.append({"n": n, "version": int(row["config_version"]), "score": float(row["avg_score"]),
                     "exact": float(str(row["exact_match"]).rstrip("%")) / (100 if "%" in str(row["exact_match"]) else 1),
                     "denials": int(row["wrongful_denials"]), "status": row["status"],
                     "changes": row.get("changes_tested", ""), "categories": summary.get("by_category", {}),
                     "edits": edits.get(n, []), "claims": summary.get("claims"),
                     "errors": summary.get("errors"), "usage": summary.get("usage", {}),
                     "restored_summary_only": summary.get("restored_summary_only", False),
                     "recovery": summary.get("recovery")})
    configs = {}
    for file in playbook_dir.glob("v*.yaml"):
        config = yaml.safe_load(file.read_text(encoding="utf-8"))
        configs[str(config["version"])] = config
    claims = []
    dataset = ROOT / "data" / "dataset.csv"
    if dataset.exists():
        with dataset.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                claims.append({"id": row.get("s.no", ""), "state": row.get("state", ""),
                               "description": row.get("description", ""),
                               "expected": row.get("expected_outcome_gold", "")})
    restored = sum(run["restored_summary_only"] for run in runs)
    origin = (f"{restored} archived runs + {len(runs) - restored} fresh local runs" if restored
              else "Current local run artifacts")
    return {"schema": 1, "origin": origin, "recorded": False,
            "generated": datetime.now(timezone.utc).isoformat(), "claim_count": len(claims),
            "held_out_evaluated": False, "runs": runs, "configs": configs, "claims": claims,
            "pending_version": state.get("next_version"), "threshold": None}


def export_dashboard(out: Path = REPORTS_DIR / "demo") -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css", "app.js"):
        shutil.copyfile(ASSETS / name, out / name)
    data = snapshot()
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # Script data also permits opening index.html directly without a local server.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    (out / "data.js").write_text("window.DEMO_DATA = " + payload + ";", encoding="utf-8")
    return out / "index.html"


def serve(port: int = 8765, export_only: bool = False) -> Path:
    index = export_dashboard()
    print(f"Dashboard: {index}")
    if export_only:
        return index

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(index.parent), **kwargs)

        def do_GET(self):
            if self.path.split("?")[0] == "/api/data":
                try:
                    payload = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                except (OSError, ValueError, KeyError) as exc:
                    self.send_error(503, f"Run artifacts are being updated: {type(exc).__name__}")
            else:
                super().do_GET()

    print(f"Open http://localhost:{port} · Ctrl+C to stop. No model calls are made.")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return index
