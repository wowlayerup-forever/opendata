#!/usr/bin/env python3
"""Bad-data controls in tools/reconcile.py: exclusions and the quality report.

  python tests/test_reconcile.py

Four synthetic contributors loot the same creature. Two report the cloth at about 40%,
one at 100% (flagged), and one is excluded and brings an item nobody else saw (must
vanish). The report must use pseudonyms only.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / ".cache" / "tests" / "reconcile"
failures: list[str] = []
sys.path.insert(0, str(REPO / "tools"))
from reconcile import pseudonym  # noqa: E402


def check(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        failures.append(what)


def batch(contributor: str, bid: str, windows: int, rate: float, extra_item: int | None = None, t0: int = 1792000000) -> dict:
    rnd = random.Random(bid)
    events = []
    for n in range(windows):
        items = [{"item_id": 2589, "count": 1}] if rnd.random() < rate else []
        if extra_item:
            items.append({"item_id": extra_item, "count": 1})
        events.append({"type": "loot", "t": t0 + n, "source": {"kind": "npc", "id": 568, "spawn": f"{bid}-{n}"},
                       "items": items, "pos": {"map_id": 0, "ui_map_id": 1437, "x": 53.0, "y": 40.0}})
    return {"format": 1, "batch_id": bid, "contributor": contributor, "build": "1.60.1.70374", "events": events}


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "batches").mkdir(parents=True)
    print("reconcile controls")
    batches = [
        batch("github:alice", "a" * 24, 100, 0.4),
        batch("github:bob", "b" * 24, 100, 0.4),
        batch("github:mallory", "c" * 24, 60, 1.0, t0=4102444800),   # inflated drops, timestamps in 2100
        batch("github:trent", "d" * 24, 50, 0.4, extra_item=19019),  # excluded; brings an item nobody else saw
    ]
    for b in batches:
        (OUT / "batches" / f"{b['batch_id']}.json").write_text(json.dumps(b))
    (OUT / "exclusions.json").write_text(json.dumps({"contributors": {"github:trent": "test"}, "batches": {}}))

    def run(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "tools/reconcile.py", str(OUT / "batches"), "--trust-unknown",
                               "--out", str(OUT / "world"), *extra], cwd=REPO, capture_output=True, text=True)

    r = run("--exclude", str(OUT / "exclusions.json"), "--report", str(OUT / "report.md"))
    check(r.returncode == 0, "reconcile runs with --exclude and --report")
    check("skipped 1 batches: excluded contributor" in r.stderr, "excluded contributor's batch skipped")
    loot = [json.loads(line) for line in (OUT / "world" / "loot.jsonl").read_text().splitlines()]
    items = {row["item_id"] for row in loot}
    check(19019 not in items, "excluded contributor's data is gone from world/loot")
    cloth = next(row for row in loot if row["item_id"] == 2589)
    check(cloth["kills"] == 260, f"windows from the three included contributors only ({cloth['kills']})")

    report = (OUT / "report.md").read_text()
    check(pseudonym("github:mallory") in report and "+" in report.split(pseudonym("github:mallory"))[-1][:200],
          "inflated contributor flagged with a positive z")
    flagged = [line for line in report.splitlines()
               if any(line.startswith(f"| {pseudonym(h)} | npc") for h in ("github:alice", "github:bob"))]
    check(not flagged, f"neither honest contributor is flagged ({flagged})")
    check(not any(h in report for h in ("alice", "bob", "mallory", "trent", "github:")), "report contains no handles")
    check("timestamps before 2025 or in the future" in report, "implausible timestamps reported")
    check("1 exclusion entries applied" in report, "report notes the exclusion")

    # two contributors only: a disagreement, nobody blamed
    two = OUT / "two"
    (two / "batches").mkdir(parents=True)
    for b in (batch("github:alice", "e" * 24, 100, 0.4), batch("github:mallory", "f" * 24, 60, 1.0)):
        (two / "batches" / f"{b['batch_id']}.json").write_text(json.dumps(b))
    subprocess.run([sys.executable, "tools/reconcile.py", str(two / "batches"), "--trust-unknown", "--out", str(two / "world"),
                    "--report", str(two / "report.md")], cwd=REPO, capture_output=True, text=True)
    rep2 = (two / "report.md").read_text()
    check("Two contributors disagree" in rep2 and "far from the median contributor's (|z| >= 4, at least 20 windows each, three or more contributors): 0" in rep2,
          "with two contributors: reported as a disagreement, neither blamed")

    # salted pseudonyms: with a key, the plain sha256 of a handle no longer appears
    (OUT / "pseudonym.key").write_text("test-secret")
    run("--report", str(OUT / "salted.md"), "--pseudonym-key", str(OUT / "pseudonym.key"))
    salted = (OUT / "salted.md").read_text()
    check(pseudonym("github:mallory") not in salted, "with a pseudonym key, the guessable sha256 pseudonym is not used")

    r = run()
    loot = [json.loads(line) for line in (OUT / "world" / "loot.jsonl").read_text().splitlines()]
    check(19019 in {row["item_id"] for row in loot}, "without the exclusion file the item comes back (exclusion is the only filter)")

    print(f"\n{'all checks passed' if not failures else f'{len(failures)} checks failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
