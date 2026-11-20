"""
The #74 breakdown benchmark (`benchmarks/batching_breakdown.py`, SPEC S1.12): the parts that need no
model. The measurement itself runs in CI only (`test-torchnative.yml`, `measure=true`).
"""
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "benchmarks" / "batching_breakdown.py"
WORKFLOW = ROOT / ".github" / "workflows" / "test-torchnative.yml"


def load_script():
    spec = importlib.util.spec_from_file_location("batching_breakdown", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_series_drops_its_warmup_and_reports_repetitions_median_and_min():
    summary = load_script().summarize([100.0, 5.0, 3.0, 4.0, 9.0], warmup=1)

    assert summary["repetitions"] == 4
    assert summary["median_ms"] == 4.5
    assert summary["min_ms"] == 3.0
    assert summary["samples_ms"] == [5.0, 3.0, 4.0, 9.0]


def test_an_empty_series_is_reported_as_empty_not_as_a_crash():
    summary = load_script().summarize([1.0], warmup=1)

    assert summary == {"repetitions": 0, "median_ms": None, "min_ms": None, "mean_ms": None, "samples_ms": []}


def test_the_dispatch_timer_clocks_the_operators_of_a_matmul():
    """ The fallback for a substrate without `torch.profiler` (torchnative): every operator the
    dispatcher sees is timed, nested operators are subtracted, the top ten by self time are kept. """
    import torch

    table = load_script().time_operators(lambda: torch.ones(8, 8) @ torch.ones(8, 8))

    assert table["available"] is True, table
    assert 0 < len(table["ops"]) <= 10
    assert all(isinstance(row["op"], str) and row["calls"] >= 1 for row in table["ops"])
    self_ms = [row["self_ms"] for row in table["ops"]]
    assert self_ms == sorted(self_ms, reverse=True)
    assert all(row["total_ms"] >= row["self_ms"] >= 0.0 for row in table["ops"])
    assert table["operators_seen"] >= len(table["ops"])


def test_the_profiler_table_is_either_a_top_ten_or_names_why_it_is_unavailable():
    """ `torch.profiler` works on upstream PyTorch and raises on torchnative: either way the JSON says. """
    import torch

    table = load_script().profile_operators(lambda: torch.ones(8, 8) @ torch.ones(8, 8))

    if table["available"]:
        assert 0 < len(table["ops"]) <= 10
        assert all(isinstance(row["op"], str) for row in table["ops"])
    else:
        assert table["reason"]


def test_help_runs_without_a_model():
    completed = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=ROOT, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    assert "--round" in completed.stdout
    assert "--tokens" in completed.stdout


def test_the_measure_run_uploads_the_breakdown_next_to_the_throughput():
    """ #246 wired `batching_throughput.py` into the `measure=true` path; the breakdown joins it in the
    same artifact, both substrates alternated in one job. """
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "benchmarks/batching_breakdown.py" in workflow
    assert "bench-74/batching-throughput.json" in workflow
    assert "bench-74/batching-breakdown.json" in workflow
    assert workflow.count("batching_breakdown.py --tokens") == 2, "torchnative and upstream, alternated in one loop"
