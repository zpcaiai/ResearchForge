#!/usr/bin/env python3
"""Reproduction probe — the A arm of REPRO_STUDY_PROTOCOL.md, at the levels this
environment can honestly measure.

WHAT THIS MEASURES, AND WHAT IT CANNOT

There is no GPU here and no egress to dataset hosts, so RL2+ (matching the paper's
numbers) is not measurable and is never claimed. What is measurable:

  A  clone      the repository exists and is fetchable
  B  resolve    a dependency specification exists and pip can resolve it
  C  entrypoint the entry point runs to completion on a reduced input

RL1 requires C. B is reported separately because it is a NECESSARY condition for C:
you cannot run what you cannot install. Therefore

    P(RL>=1)  <=  P(B)

which makes P(B) a hard upper bound. That matters for the one decision rule this
study can settle: if even the upper bound falls below 0.40, the rule
"P(RL>=1) < 0.40 -> stop building the execution plane" fires definitively rather
than suggestively.

Every repo is cloned, probed and deleted before the next begins. Disk here is a
fixed allowance, and a study that dies half way through for lack of space produces
a biased sample of whichever repos happened to be probed first.
"""
from __future__ import annotations

import json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

CLONE_TIMEOUT = 120
RESOLVE_TIMEOUT = 240
RUN_TIMEOUT = 90
DEP_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "environment.yml",
             "environment.yaml", "Pipfile", "setup.cfg", "poetry.lock", "uv.lock")
ENTRY_HINTS = ("main.py", "run.py", "train.py", "demo.py", "inference.py", "app.py",
               "eval.py", "test.py", "predict.py", "cli.py")
REPRO_HEAD = re.compile(r"^#+.*\b(reproduc|replicat|results?|evaluation|quick\s*start|getting\s*started)\b",
                        re.I | re.M)


def sh(cmd, timeout, cwd=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout[-4000:], p.stderr[-4000:]
    except subprocess.TimeoutExpired:
        return 124, "", f"TIMEOUT after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 125, "", f"{type(e).__name__}: {e}"


def probe(entry: dict) -> dict:
    t0 = time.time()
    out = {**entry, "codes": [], "level": "RL0", "clone_ok": False, "resolve_ok": None,
           "entry_ok": None, "seconds": 0.0, "notes": {}}

    if not entry.get("github_repo"):
        out["codes"].append("NO_CODE")
        out["level_rationale"] = "the paper announced no public repository"
        out["seconds"] = round(time.time() - t0, 1)
        return out

    tmp = Path(tempfile.mkdtemp(prefix="rf-probe-"))
    repo = tmp / "r"
    try:
        rc, _, err = sh(["git", "clone", "--depth", "1", entry["github_repo"], str(repo)],
                        CLONE_TIMEOUT)
        if rc != 0:
            out["codes"].append("TIMEBOX_EXCEEDED" if rc == 124 else "NO_CODE")
            out["notes"]["clone_error"] = err[-300:]
            out["level_rationale"] = "clone failed; the announced repository is not fetchable"
            return out
        out["clone_ok"] = True

        rev, _, _ = sh(["git", "-C", str(repo), "rev-parse", "HEAD"], 20)[1], None, None
        out["notes"]["revision"] = sh(["git", "-C", str(repo), "rev-parse", "HEAD"], 20)[1].strip()
        out["notes"]["last_commit"] = sh(
            ["git", "-C", str(repo), "log", "-1", "--format=%cI"], 20)[1].strip()

        present = [f for f in DEP_FILES if (repo / f).exists()]
        out["notes"]["dependency_files"] = present
        readme = next((p for p in repo.iterdir()
                       if p.is_file() and p.name.lower().startswith("readme")), None)
        rtext = readme.read_text(errors="replace") if readme else ""
        out["notes"]["readme_bytes"] = len(rtext)
        out["notes"]["readme_has_repro_section"] = bool(REPRO_HEAD.search(rtext))
        out["notes"]["mentions_gpu"] = bool(re.search(r"\b(cuda|gpu|a100|h100|v100|nvidia)\b",
                                                      rtext, re.I))
        py = list(repo.rglob("*.py"))
        out["notes"]["python_files"] = len(py)
        entries = [f for f in ENTRY_HINTS if any(p.name == f for p in py)]
        out["notes"]["entry_candidates"] = entries

        if not present:
            out["codes"].append("CONFIG_AMBIGUOUS")
            out["resolve_ok"] = False
            out["level_rationale"] = ("no dependency specification of any kind; the environment "
                                      "cannot be reconstructed from the repository alone")
            return out

        # --- B: does pip resolve the declared dependencies? ---------------
        target = None
        if (repo / "requirements.txt").exists():
            target = ["-r", str(repo / "requirements.txt")]
        elif (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
            target = [str(repo)]
        if target is None:
            out["codes"].append("DEPENDENCY_UNRESOLVABLE")
            out["resolve_ok"] = False
            out["notes"]["resolve_note"] = ("only conda/Pipfile specs present; pip cannot resolve "
                                            "them and no conda is available here")
            out["level_rationale"] = "dependency spec is not pip-resolvable in this environment"
            return out

        rc, so, se = sh([sys.executable, "-m", "pip", "install", "--dry-run", "--quiet",
                         "--no-input", "--disable-pip-version-check", *target],
                        RESOLVE_TIMEOUT)
        out["resolve_ok"] = (rc == 0)
        out["notes"]["resolve_rc"] = rc
        if rc != 0:
            out["notes"]["resolve_error"] = (se or so)[-500:]
            blob = (se + so).lower()
            if rc == 124:
                out["codes"].append("TIMEBOX_EXCEEDED")
            elif "no matching distribution" in blob or "could not find a version" in blob:
                out["codes"].append("DEPENDENCY_UNRESOLVABLE")
            elif "conflict" in blob or "incompatible" in blob:
                out["codes"].append("DEPENDENCY_UNRESOLVABLE")
            elif "requires python" in blob:
                out["codes"].append("DEPENDENCY_UNRESOLVABLE")
            else:
                out["codes"].append("DEPENDENCY_UNRESOLVABLE")
            out["level_rationale"] = ("declared dependencies do not resolve; the environment "
                                      "cannot be built, so the entry point was never reachable")
            return out

        # --- C: entry point. Not attempted without data/compute -----------
        # Resolution succeeding does NOT make this RL1. RL1 requires the entry point
        # to complete, and completing it needs data and hardware this environment
        # does not have. Recording that honestly is the whole point.
        out["entry_ok"] = False
        out["codes"].append("HARDWARE_UNAVAILABLE" if out["notes"]["mentions_gpu"]
                            else "DATA_UNAVAILABLE")
        out["level_rationale"] = (
            "dependencies resolve, so the environment is constructible — but the entry point "
            "was not executed (no GPU, no dataset egress). This is RL0 by the protocol's "
            "definition: 'not reproduced' is not the same as 'not reproducible'.")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        out["seconds"] = round(time.time() - t0, 1)


def main():
    sample = json.load(open("sample.json"))["sample"]
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    results = []
    for e in sample:
        if only and str(e["sample_index"]) not in only:
            continue
        r = probe(e)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
    Path(f"results_{os.getpid()}.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results))


if __name__ == "__main__":
    main()
