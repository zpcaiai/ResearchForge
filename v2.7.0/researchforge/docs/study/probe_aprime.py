#!/usr/bin/env python3
"""A' arm — the same 20 papers, probed with the 'dependency time machine' this
study recommended building.

THIS IS NOT THE B ARM. The protocol's B arm is a skilled human with an 8-hour
budget; I am not that and will not report myself as that. What this measures is
narrower and more immediately useful: **does the engineering recommendation the
A arm produced actually work?**

Two of the four recommended levers are reachable from here:

  L1  historical index snapshot   uv --exclude-newer <repo's last commit date>
                                  resolves as though it were that date, so a pin
                                  that was satisfiable when the paper shipped is
                                  satisfiable again
  L2  multi-version interpreter   uv python 3.9/3.10/3.11/3.12, trying the version
                                  the repo declares before falling back

  L3  conda/mamba backend         miniforge is on GitHub releases: proxy-blocked
  L4  torch/CUDA wheel index      download.pytorch.org: proxy-blocked

So this is a partial test of the recommendation, and the lift it measures is a
LOWER bound on what the full four-lever version would achieve.

Crucially it records WHICH lever did the work, because "resolution improved" is
not actionable and "the date snapshot fixed 6 of 9" is.
"""
from __future__ import annotations

import json, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

PY_VERSIONS = ("3.10", "3.11", "3.9", "3.12")
DEP_NAMES = ("requirements.txt", "pyproject.toml", "setup.py")
REQ_PY = re.compile(r'requires[-_]python\s*=?\s*["\']?([><=~^!,\.\d\s]+)', re.I)
PY_HINT = re.compile(r'python\s*[>=~]*\s*3\.(\d+)', re.I)


def sh(cmd, timeout, cwd=None, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        return p.returncode, p.stdout[-3000:], p.stderr[-3000:]
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT"


def declared_python(repo: Path) -> str | None:
    for f in ("pyproject.toml", "setup.py", "setup.cfg"):
        p = repo / f
        if p.exists():
            m = REQ_PY.search(p.read_text(errors="replace"))
            if m:
                v = re.search(r"3\.(\d+)", m.group(1))
                if v:
                    return f"3.{v.group(1)}"
    pv = repo / ".python-version"
    if pv.exists():
        m = re.search(r"3\.\d+", pv.read_text(errors="replace"))
        if m:
            return m.group(0)
    return None


def specs(repo: Path) -> list[Path]:
    out = []
    for p in repo.rglob("*"):
        if p.is_file() and p.name in DEP_NAMES:
            rel = p.relative_to(repo)
            if len(rel.parts) <= 4 and ".git" not in rel.parts:
                out.append(p)
    # shallowest first: the top-level spec is the one a reader would try
    return sorted(out, key=lambda p: (len(p.relative_to(repo).parts), len(str(p))))[:4]


#: resolved once; uv installs these on demand from python-build-standalone
INTERPRETERS: dict[str, str] = {}


def interpreter(py: str) -> str | None:
    """A venv, not uv's managed interpreter.

    uv refuses to dry-run an install into an interpreter it manages, and rightly:
    a dry-run still resolves against the target environment's existing packages, so
    it has to be an environment the caller owns. One clean venv per version also
    means each attempt starts from the same empty state, which is what makes the
    attempts comparable to each other.
    """
    if py not in INTERPRETERS:
        v = Path(f"/home/claude/study2/.venv{py}/bin/python")
        INTERPRETERS[py] = str(v) if v.exists() else ""
    return INTERPRETERS[py] or None


def attempt(spec: Path, repo: Path, py: str, exclude_newer: str | None):
    """Install-resolve against a REAL interpreter of that version.

    `uv pip compile` was the obvious tool here and it is the wrong one: it resolves
    without enforcing `requires-python`, so it happily produces a lockfile that
    cannot be installed under the interpreter it was compiled for. Measuring lift
    with it would have credited the time machine for successes that do not exist.
    `pip install --dry-run` is what the A arm ran, so `uv pip install --dry-run`
    against a real interpreter is the only comparison that means anything.
    """
    exe = interpreter(py)
    if exe is None:
        return False, 127, f"no interpreter for {py}"
    cmd = ["uv", "pip", "install", "--dry-run", "--quiet", "--python", exe]
    if exclude_newer:
        cmd += ["--exclude-newer", exclude_newer]
    cmd += ["-r", str(spec)] if spec.name == "requirements.txt" else [str(spec.parent)]
    rc, so, se = sh(cmd, 150, cwd=str(repo))
    return rc == 0, rc, (se or so)[-400:]


def probe(entry: dict) -> dict:
    t0 = time.time()
    out = {**{k: entry[k] for k in ("sample_index", "arxiv_id", "title", "github_repo")},
           "resolve_ok": False, "lever": None, "attempts": [], "seconds": 0.0}
    if not entry.get("github_repo"):
        out["lever"] = "n/a — no code"
        return out
    tmp = Path(tempfile.mkdtemp(prefix="rf-ap-"))
    repo = tmp / "r"
    try:
        rc, _, _ = sh(["git", "clone", "--depth", "1", entry["github_repo"], str(repo)], 120)
        if rc != 0:
            out["lever"] = "n/a — clone failed"
            return out
        commit = sh(["git", "-C", str(repo), "log", "-1", "--format=%cI"], 20)[1].strip()[:10]
        out["last_commit"] = commit
        decl = declared_python(repo)
        out["declared_python"] = decl
        sp = specs(repo)
        out["specs"] = [str(p.relative_to(repo)) for p in sp]
        if not sp:
            out["lever"] = "n/a — no pip-resolvable spec anywhere"
            return out

        order = ([decl] if decl and decl in PY_VERSIONS else []) + \
                [v for v in PY_VERSIONS if v != decl]
        # Ladder, cheapest first. The order is the finding: whichever rung succeeds
        # names the lever that was actually needed.
        plan = [("baseline: today's index, py3.11", "3.11", None)]
        plan += [(f"L1 date snapshot @{commit}, py3.11", "3.11", commit)] if commit else []
        plan += [(f"L2 interpreter {v}", v, None) for v in order if v != "3.11"]
        plan += [(f"L1+L2 {v} @{commit}", v, commit) for v in order if commit]

        # Some failures are terminal on sight and re-trying levers against them only
        # burns the time box. A requirement pointing at a path on the authors' own
        # machine is the clearest case: no index and no interpreter can conjure it.
        HOPELESS = ("distribution not found at: file://", "distribution not found at: /",
                    "does not appear to be a python project")

        for s in sp[:2]:
            prev = None
            repeats = 0
            for label, py, newer in plan:
                ok, rc2, err = attempt(s, repo, py, newer)
                out["attempts"].append({"spec": str(s.relative_to(repo)), "lever": label,
                                        "ok": ok, "rc": rc2, "err": err[-200:] if not ok else ""})
                if ok:
                    out["resolve_ok"] = True
                    out["lever"] = label
                    return out
                low = err.lower()
                if any(h in low for h in HOPELESS):
                    out["terminal_reason"] = "requirement points at a path that only existed on the authors' machine"
                    break
                key = low[-120:]
                repeats = repeats + 1 if key == prev else 0
                prev = key
                # the lever changed nothing; two identical failures in a row is enough
                if repeats >= 1:
                    out["terminal_reason"] = "levers made no difference to the error"
                    break
        out["lever"] = "none of the reachable levers worked"
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        out["seconds"] = round(time.time() - t0, 1)


if __name__ == "__main__":
    sample = json.load(open("sample.json"))["sample"]
    only = set(sys.argv[1:])
    res = []
    for e in sample:
        if only and str(e["sample_index"]) not in only:
            continue
        r = probe(e)
        res.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
