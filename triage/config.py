import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_CSV = ROOT / "data" / "dataset.csv"
GOLD_CACHE = ROOT / "data" / "gold_structured.jsonl"
PLAYBOOK_DIR = ROOT / "playbook"
REPORTS_DIR = ROOT / "reports"
RUNS_DIR = ROOT / "runs"

WORKERS = int(os.environ.get("TRIAGE_WORKERS", "8"))
