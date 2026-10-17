"""Command line: ping | prepare-gold | run | history | eval."""

import argparse
import json
import logging

from triage import playbook as pbmod
from triage.config import REPORTS_DIR, RUNS_DIR, WORKERS
from triage.dataset import attach_gold, load_claims
from triage.log import setup_logging

log = logging.getLogger("triage.cli")


def cmd_ping(args):
    from openai import OpenAI

    from triage.llm import api_key, base_url, chat_model, model_name

    client = OpenAI(base_url=base_url(), api_key=api_key())
    print(f"Crusoe endpoint: {base_url()}")
    ids: list[str] = []
    try:
        ids = sorted(m.id for m in client.models.list())
        print("Models available to this key:\n  " + "\n  ".join(ids))
    except Exception as e:
        print(f"(could not list models: {e})")
    failed = False
    for kind in ("worker", "optimizer"):
        name = model_name(kind)
        if ids and name not in ids:
            print(f"{kind:9} {name} -> NOT AVAILABLE; set CRUSOE_{kind.upper()}_MODEL in .env to one of the ids above")
            failed = True
            continue
        try:
            reply = chat_model(kind).invoke("Reply with the single word OK.")
            print(f"{kind:9} {name} -> {str(reply.content).strip()[:40]!r}")
        except Exception as e:
            print(f"{kind:9} {name} -> FAILED: {type(e).__name__}: {e}")
            failed = True
    if failed:
        raise SystemExit(1)


def cmd_prepare_gold(args):
    from triage.gold import prepare_gold
    from triage.llm import USAGE, chat_model

    done, total = prepare_gold(load_claims(), chat_model("optimizer"), args.workers, args.force)
    print(f"Extracted structured gold for {done} of {total} claims -> data/gold_structured.jsonl")
    print("Review that file: it is the answer key the team is scored against. Usage:", USAGE.snapshot())


def cmd_run(args):
    from triage.agents import make_triage_fn
    from triage.llm import chat_model
    from triage.runs import history_report, run_once
    from triage.supervisor import make_propose_fn

    from triage.llm import model_name

    claims = attach_gold(load_claims())
    log.info("loaded %d claims | worker %s | supervisor %s | %d run(s) | threshold %.2f | k %d",
             len(claims), model_name("worker"), model_name("optimizer"), args.times, args.threshold, args.k)
    triage_fn = make_triage_fn(chat_model("worker"))
    propose_fn = make_propose_fn(chat_model("optimizer", temperature=0.3), args.k)
    for _ in range(args.times):
        run_once(claims, triage_fn, propose_fn, threshold=args.threshold, workers=args.workers)
    history_report()
    log.info("history: %s | dashboard: %s", RUNS_DIR / "history.csv", REPORTS_DIR / "dashboard.html")


def cmd_history(args):
    from triage.runs import history_report

    print(history_report())
    print(f"Dashboard: {REPORTS_DIR / 'dashboard.html'}  (open it in a browser)")


def cmd_eval(args):
    from triage.agents import make_triage_fn
    from triage.evaluate import evaluate, write_results_csv
    from triage.llm import USAGE, chat_model

    claims = attach_gold(load_claims())
    pb = pbmod.load(args.version if args.version is not None else pbmod.latest_version())
    report = evaluate(claims, pb, make_triage_fn(chat_model("worker")), args.workers)
    path = write_results_csv(claims, report, RUNS_DIR / f"eval_v{pb.version}.csv")
    print(json.dumps({"config": f"v{pb.version}", **report.summary(), "usage": USAGE.snapshot()}, indent=2))
    print(f"Scored CSV: {path}")


def cmd_dashboard(args):
    from triage.demo import serve

    serve(port=args.port, export_only=args.export_only)


def main(argv=None):
    p = argparse.ArgumentParser(prog="triage", description=__doc__)
    p.add_argument("--workers", type=int, default=WORKERS, help="parallel claims")
    p.add_argument("-v", "--verbose", action="store_true", help="also log every agent's output and each Crusoe call")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="check Crusoe access and list models").set_defaults(fn=cmd_ping)

    g = sub.add_parser("prepare-gold", help="structure the free-text gold column (cached)")
    g.add_argument("--force", action="store_true", help="re-extract every row")
    g.set_defaults(fn=cmd_prepare_gold)

    r = sub.add_parser("run", help="one improvement run on all claims: score, keep or roll back, propose next")
    r.add_argument("--times", type=int, default=1, help="number of runs in a row")
    r.add_argument("--k", type=int, default=3, help="max edits the supervisor proposes per run")
    r.add_argument("--threshold", type=float, default=0.02, help="score gain over the best run needed to keep")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("history", help="run-by-run table and chart -> reports/history.md").set_defaults(fn=cmd_history)

    d = sub.add_parser("dashboard", help="interactive demo of saved runs; no inference calls")
    d.add_argument("--port", type=int, default=8765)
    d.add_argument("--export-only", action="store_true", help="write reports/demo/index.html without starting a server")
    d.set_defaults(fn=cmd_dashboard)

    e = sub.add_parser("eval", help="score one config version on all claims (no learning)")
    e.add_argument("--version", type=int, help="config version (default: latest)")
    e.set_defaults(fn=cmd_eval)

    args = p.parse_args(argv)
    setup_logging(args.verbose)
    args.fn(args)


if __name__ == "__main__":
    main()
