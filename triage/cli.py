"""Command line: ping | prepare-gold | eval | improve | compare."""

import argparse
import json

from triage import playbook as pbmod
from triage.config import REPORTS_DIR, WORKERS
from triage.dataset import attach_gold, load_claims, split


def _splits(seed: int):
    return split(attach_gold(load_claims()), seed)


def _claims_for(name: str, seed: int):
    return attach_gold(load_claims()) if name == "all" else _splits(seed)[name]


def cmd_ping(args):
    from openai import OpenAI

    from triage.llm import api_key, base_url, chat_model, model_name

    client = OpenAI(base_url=base_url(), api_key=api_key())
    print(f"Crusoe endpoint: {base_url()}")
    try:
        ids = sorted(m.id for m in client.models.list())
        print("Models available to this key:\n  " + "\n  ".join(ids))
    except Exception as e:
        print(f"(could not list models: {e})")
    for kind in ("worker", "optimizer"):
        reply = chat_model(kind).invoke("Reply with the single word OK.")
        print(f"{kind:9} {model_name(kind)} -> {reply.content.strip()[:40]!r}")


def cmd_prepare_gold(args):
    from triage.gold import prepare_gold
    from triage.llm import USAGE, chat_model

    done, total = prepare_gold(load_claims(), chat_model("optimizer"), args.workers, args.force)
    print(f"Extracted structured gold for {done} of {total} claims -> data/gold_structured.jsonl")
    print("Review that file: it is the answer key the team is scored against. Usage:", USAGE.snapshot())


def cmd_eval(args):
    from triage.agents import make_triage_fn
    from triage.evaluate import evaluate, write_outputs
    from triage.llm import chat_model

    claims = _claims_for(args.split, args.seed)
    pb = pbmod.load(args.version if args.version is not None else pbmod.latest_version())
    report = evaluate(claims, pb, make_triage_fn(chat_model("worker")), args.workers)
    csv_path, json_path = write_outputs(claims, report, pb, args.split)
    print(json.dumps({"playbook": f"v{pb.version}", "split": args.split, **report.summary()}, indent=2))
    print(f"Scored CSV: {csv_path}\nReport: {json_path}")


def cmd_improve(args):
    from triage.agents import make_triage_fn
    from triage.llm import USAGE, chat_model
    from triage.loop import improve
    from triage.supervisor import make_propose_fn

    s = _splits(args.seed)
    start = pbmod.load(args.start if args.start is not None else pbmod.latest_version())
    result = improve(s["train"], s["val"], start, make_triage_fn(chat_model("worker")),
                     make_propose_fn(chat_model("optimizer", temperature=0.3), args.k),
                     cycles=args.cycles, threshold=args.threshold, workers=args.workers)
    print(f"\nFinal playbook: v{result.final.version} | validation history {[round(x, 3) for x in result.val_history]}")
    print("Usage:", USAGE.snapshot(), f"\nLog: {REPORTS_DIR / 'improvement_log.json'}")


def cmd_compare(args):
    from triage.agents import make_triage_fn
    from triage.evaluate import evaluate, write_outputs
    from triage.llm import USAGE, chat_model

    claims = _claims_for(args.split, args.seed)
    triage_fn = make_triage_fn(chat_model("worker"))
    after_v = args.after if args.after is not None else pbmod.latest_version()
    rows = {}
    for v in (args.before, after_v):
        pb = pbmod.load(v)
        report = evaluate(claims, pb, triage_fn, args.workers)
        write_outputs(claims, report, pb, args.split)
        rows[f"v{v}"] = report.summary()
    before, after = rows[f"v{args.before}"], rows[f"v{after_v}"]
    lines = [f"# Before / after on `{args.split}` ({before['claims']} claims)", "",
             f"| Metric | v{args.before} | v{after_v} |", "|---|---|---|"]
    for k in ("cost_weighted_score", "exact_match", "wrongful_denials", "straight_through_rate", "siu_hit_rate",
              "subrogation_caught", "errors"):
        lines.append(f"| {k} | {before[k]} | {after[k]} |")
    lines += ["", "| Category (gold route) | before | after |", "|---|---|---|"]
    for cat in sorted(set(before["by_category"]) | set(after["by_category"])):
        lines.append(f"| {cat} | {before['by_category'].get(cat, '-')} | {after['by_category'].get(cat, '-')} |")
    lines += ["", f"Crusoe usage: {USAGE.snapshot()}"]
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"before_after_{args.split}.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines) + f"\n\nWritten to {out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="triage", description=__doc__)
    p.add_argument("--workers", type=int, default=WORKERS, help="parallel claims")
    p.add_argument("--seed", type=int, default=42, help="split seed")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="check Crusoe access and list models").set_defaults(fn=cmd_ping)

    g = sub.add_parser("prepare-gold", help="structure the free-text gold column (cached)")
    g.add_argument("--force", action="store_true", help="re-extract every row")
    g.set_defaults(fn=cmd_prepare_gold)

    e = sub.add_parser("eval", help="score one playbook version on a split")
    e.add_argument("--version", type=int, help="playbook version (default: latest)")
    e.add_argument("--split", choices=["train", "val", "test", "all"], default="test")
    e.set_defaults(fn=cmd_eval)

    i = sub.add_parser("improve", help="run the self-improvement loop")
    i.add_argument("--start", type=int, help="starting playbook version (default: latest)")
    i.add_argument("--cycles", type=int, default=3)
    i.add_argument("--k", type=int, default=3, help="candidate edits per cycle")
    i.add_argument("--threshold", type=float, default=0.01, help="minimum validation gain to accept an edit")
    i.set_defaults(fn=cmd_improve)

    c = sub.add_parser("compare", help="before/after report for two playbook versions")
    c.add_argument("--before", type=int, default=0)
    c.add_argument("--after", type=int, help="default: latest")
    c.add_argument("--split", choices=["train", "val", "test", "all"], default="test")
    c.set_defaults(fn=cmd_compare)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
