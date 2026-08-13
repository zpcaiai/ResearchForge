#!/usr/bin/env python3
"""Measurement-error check on CONFIG_AMBIGUOUS.

The first pass looked for dependency files in the repository ROOT only. Plenty of
research repos put requirements.txt one level down. If that is what happened, my
CONFIG_AMBIGUOUS rate is a defect in my instrument, not in the repositories — and
a study that reports its own instrument error as a finding about the world is
worse than no study.

So: re-clone every repo the first pass graded CONFIG_AMBIGUOUS, search the whole
tree, and re-resolve against whatever is found.
"""
import json, shutil, subprocess, sys, tempfile, time
from pathlib import Path

DEP_NAMES = ("requirements.txt", "requirements-dev.txt", "requirements_all.txt",
             "pyproject.toml", "setup.py", "environment.yml", "environment.yaml",
             "Pipfile", "setup.cfg", "conda.yaml", "uv.lock", "poetry.lock")


def sh(cmd, timeout, cwd=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout[-3000:], p.stderr[-3000:]
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT"


results = []
targets = [r for r in (json.loads(l) for l in open("all_results.jsonl"))
           if "CONFIG_AMBIGUOUS" in r["codes"]]
for e in targets:
    tmp = Path(tempfile.mkdtemp(prefix="rf-recheck-"))
    repo = tmp / "r"
    rec = {"sample_index": e["sample_index"], "arxiv_id": e["arxiv_id"],
           "repo": e["github_repo"], "found": [], "resolve_ok": None, "note": ""}
    try:
        rc, _, _ = sh(["git", "clone", "--depth", "1", e["github_repo"], str(repo)], 120)
        if rc != 0:
            rec["note"] = "re-clone failed"
            results.append(rec); continue
        found = []
        for p in repo.rglob("*"):
            if p.is_file() and p.name in DEP_NAMES:
                rel = p.relative_to(repo)
                if len(rel.parts) <= 4 and ".git" not in rel.parts:
                    found.append(str(rel))
        rec["found"] = sorted(found)[:12]
        pip_targets = [p for p in found if p.endswith("requirements.txt")
                       or p.endswith("pyproject.toml") or p.endswith("setup.py")]
        if not pip_targets:
            rec["note"] = ("no pip-resolvable spec anywhere in the tree"
                           if not found else f"only non-pip specs: {sorted(set(found))[:4]}")
            rec["resolve_ok"] = False
            results.append(rec); continue
        # resolve against the shallowest requirements.txt, else the package itself
        pick = sorted(pip_targets, key=lambda s: (s.count("/"), len(s)))[0]
        arg = ["-r", str(repo / pick)] if pick.endswith("requirements.txt") else [str(repo / Path(pick).parent)]
        rc, so, se = sh([sys.executable, "-m", "pip", "install", "--dry-run", "--quiet",
                         "--no-input", "--disable-pip-version-check", *arg], 240)
        rec["resolve_ok"] = (rc == 0)
        rec["resolved_against"] = pick
        rec["note"] = "" if rc == 0 else (se or so)[-260:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    results.append(rec)
    print(json.dumps(rec, ensure_ascii=False), flush=True)

Path("recheck.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results))
