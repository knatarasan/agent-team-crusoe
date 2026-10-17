import csv
import json

from triage.demo import snapshot


def test_recording_keeps_rollback_and_unseen_status_explicit(tmp_path):
    data = snapshot(runs_dir=tmp_path)
    assert data["recorded"] is True
    assert data["held_out_evaluated"] is False
    assert [r["status"] for r in data["runs"]] == ["baseline", "kept", "rolled back", "rolled back"]
    retained = [r for r in data["runs"] if r["status"] != "rolled back"][-1]
    assert retained["version"] == 1
    assert retained["score"] == 0.957 and retained["denials"] == 0


def test_current_runs_replace_recording(tmp_path):
    runs_dir, books = tmp_path / "runs", tmp_path / "playbook"
    runs_dir.mkdir()
    books.mkdir()
    folder = runs_dir / "run_001"
    folder.mkdir()
    (folder / "summary.json").write_text(json.dumps({"claims": 50, "by_category": {"adjuster": .8}}))
    (runs_dir / "state.json").write_text(json.dumps({"next_version": 1}))
    with (runs_dir / "history.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, ["run", "config_version", "avg_score", "exact_match", "wrongful_denials", "status"])
        writer.writeheader()
        writer.writerow(dict(run=1, config_version=0, avg_score=.8, exact_match=.4, wrongful_denials=1, status="baseline"))
    data = snapshot(runs_dir, books)
    assert data["recorded"] is False
    assert len(data["runs"]) == 1
    assert data["runs"][0]["score"] == .8
    assert data["runs"][0]["categories"] == {"adjuster": .8}
    assert data["pending_version"] == 1
