"""
The repository root holds only the entries AGENTS.md §2 approves; anything else is a new top-level
file or folder that was added without approval.
"""
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def approved_entries():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    table = agents.split("Approved tracked root entries", 1)[1].split("\n\n", 2)[1]
    entries = set()
    for row in table.splitlines()[2:]:  # skip the header and the separator
        first_cell = row.split("|")[1]
        entries.update(name.rstrip("/") for name in re.findall(r"`([^`]+)`", first_cell))
    return entries


def tracked_root_entries():
    names = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return {name.split("/", 1)[0] for name in names}


def test_every_tracked_root_entry_is_approved():
    unapproved = tracked_root_entries() - approved_entries()

    assert unapproved == set(), (
        f"top-level entries not approved in AGENTS.md §2: {sorted(unapproved)} "
        ",  propose them and add them to the list, or move the work into an existing directory"
    )


def test_the_approved_list_is_read():
    assert {"api", "app", "docs", "AGENTS.md", "pyproject.toml"} <= approved_entries()


def test_no_em_dash_in_tracked_text():
    """ AGENTS.md section 16: no U+2014 in documents or code. """
    names = subprocess.run(["git", "grep", "-l", "\u2014", "--", ".", ":!api/src/main/webpack"],
                           cwd=ROOT, capture_output=True, text=True).stdout.split()
    text_files = [n for n in names if not n.endswith((".png", ".ico", ".icns", ".webp", ".wasm", ".jar"))]

    assert text_files == [], f"em-dash found in: {text_files}"
