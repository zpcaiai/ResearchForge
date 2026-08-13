#!/usr/bin/env python3
"""Build-dependency chaser.

The failure class this study kept hitting is not "the dependencies are wrong". It
is that a package's setup.py imports something at BUILD time that is only declared
as a RUNTIME dependency, so pip's isolated build environment cannot satisfy it.
flash-attn importing torch is the canonical case and it appeared in 2 of the 20
sampled papers.

uv names the missing package in its error, which makes the fix mechanical: install
it, retry with --no-build-isolation, repeat. This tests whether that loop
CONVERGES or just uncovers an endless chain. If it converges in a couple of
rounds, it is worth building into result-reproducer; if it does not, it is a trap
that would burn the whole time box.
"""
import re, subprocess, sys, time
from pathlib import Path

MISSING = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=\s*\[\s*\"([A-Za-z0-9_.-]+)\"\s*\]\s*$", re.M)
VENV, TARGET = sys.argv[1], sys.argv[2]
MAX_ROUNDS = 5
installed, log = [], []

for rnd in range(1, MAX_ROUNDS + 1):
    t0 = time.time()
    p = subprocess.run(["uv", "pip", "install", "--python", f"{VENV}/bin/python",
                        "--no-build-isolation", TARGET],
                       capture_output=True, text=True, timeout=600)
    took = round(time.time() - t0, 1)
    if p.returncode == 0:
        log.append(f"round {rnd}: SUCCESS after installing {installed} ({took}s)")
        print("\n".join(log)); print(f"CONVERGED in {rnd} round(s)"); sys.exit(0)
    m = MISSING.findall(p.stderr + p.stdout)
    if not m:
        log.append(f"round {rnd}: FAILED with no nameable build dependency ({took}s)")
        log.append("   " + (p.stderr or p.stdout).strip().splitlines()[-1][:160])
        print("\n".join(log)); print("DID NOT CONVERGE — the error is not of this class"); sys.exit(1)
    pkg, need = m[0]
    if need in installed:
        log.append(f"round {rnd}: LOOP — {need} already installed, still demanded ({took}s)")
        print("\n".join(log)); print("DID NOT CONVERGE — cycle"); sys.exit(2)
    log.append(f"round {rnd}: {pkg} needs {need} at build time -> installing it ({took}s)")
    q = subprocess.run(["uv", "pip", "install", "--python", f"{VENV}/bin/python", need],
                       capture_output=True, text=True, timeout=600)
    if q.returncode != 0:
        log.append(f"   could not install {need}: {(q.stderr or '').strip().splitlines()[-1][:120]}")
        print("\n".join(log)); print("DID NOT CONVERGE — a build dependency is itself uninstallable"); sys.exit(3)
    installed.append(need)
print("\n".join(log)); print(f"DID NOT CONVERGE in {MAX_ROUNDS} rounds"); sys.exit(4)
