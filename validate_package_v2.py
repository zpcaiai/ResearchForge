#!/usr/bin/env python3
"""
ResearchForge package validator v2 — replaces tests/validate_package.py

Adds the checks v1 was missing:
  - dependency-graph reachability from the orchestrator entry point
  - Inputs/Outputs linkage (the artifact contract)
  - schema usage (are the schemas actually bound to skills?)
  - dependency cycles
  - agreement between machine-readable depends_on and the prose Procedure
  - honest reporting: says what it did NOT check

Exit 0 = structural checks pass. WARN items do not fail the build but are printed.
Place at tests/validate_package_v2.py and run:  python tests/validate_package_v2.py
"""
from pathlib import Path
import json, re, sys, collections

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "researchforge-orchestrator"
SECTIONS = ['## Objective','## Inputs','## Outputs','## Procedure',
            '## Hard gates','## Verification / tests','## Evidence contract']

errors, warns = [], []
cat = json.loads((ROOT/'manifests/skill-catalog.json').read_text(encoding='utf-8'))
skills = cat['skills']
names = {s['name'] for s in skills}

# ---------- 1. structure (v1 parity) ----------
bodies = {}
for item in skills:
    n = item['name']
    p = ROOT/'skills'/n/'SKILL.md'
    if not p.exists():
        errors.append(f"missing {p}"); continue
    t = p.read_text(encoding='utf-8'); bodies[n] = t
    for s in SECTIONS:
        if s not in t: errors.append(f"{n}: missing section {s}")
if cat['skill_count'] != len(skills):
    errors.append(f"skill_count {cat['skill_count']} != {len(skills)} entries")

# ---------- 2. dependency graph ----------
G = {s['name']: s.get('depends_on', []) for s in skills}
for n, ds in G.items():
    for d in ds:
        if d not in names: errors.append(f"{n}: unknown dependency {d}")

color, cycles = {}, []
def dfs(u, path):
    color[u] = 1; path.append(u)
    for v in G.get(u, []):
        if color.get(v) == 1: cycles.append(" -> ".join(path[path.index(v):]+[v]))
        elif color.get(v, 0) == 0: dfs(v, path)
    path.pop(); color[u] = 2
for n in G:
    if color.get(n, 0) == 0: dfs(n, [])
for c in cycles: errors.append(f"dependency cycle: {c}")

# ---------- 3. reachability ----------
if ENTRY in G:
    seen, stack = set(), [ENTRY]
    while stack:
        x = stack.pop()
        if x in seen: continue
        seen.add(x); stack += G.get(x, [])
    unreach = sorted(names - seen)
    if unreach:
        errors.append(f"{len(unreach)}/{len(names)} skills unreachable from '{ENTRY}': "
                      + ", ".join(unreach))
else:
    errors.append(f"entry point '{ENTRY}' not in catalog")

# ---------- 4. prose vs depends_on agreement ----------
for n, t in bodies.items():
    m = re.search(r'## Procedure\n(.*?)\n## Hard gates', t, re.S)
    if not m: continue
    mentioned = {o for o in names if o != n and re.search(rf'\b{re.escape(o)}\b', m.group(1))}
    undeclared = mentioned - set(G.get(n, []))
    if undeclared:
        warns.append(f"{n}: Procedure names {len(undeclared)} skill(s) absent from depends_on: "
                     + ", ".join(sorted(undeclared)))

# ---------- 5. I/O linkage contract ----------
def listed(t, head, nxt):
    m = re.search(rf'## {re.escape(head)}\n(.*?)\n## {re.escape(nxt)}', t, re.S)
    if not m: return []
    return [re.sub(r'^[-*]\s*', '', l).strip()
            for l in m.group(1).split('\n') if l.strip().startswith(('-', '*'))]

produced = collections.defaultdict(list)
consumed = collections.defaultdict(list)
for n, t in bodies.items():
    for a in listed(t, 'Outputs', 'Depends on'): produced[a].append(n)
    for a in listed(t, 'Inputs', 'Outputs'):     consumed[a].append(n)

EXTERNAL = re.compile(r'^\s*(external|user|env)\s*:', re.I)   # opt-out marker
dangling = {a: c for a, c in consumed.items()
            if a not in produced and not EXTERNAL.match(a)}
if dangling:
    errors.append(
        f"{len(dangling)}/{len(consumed)} declared inputs match no declared output "
        f"and are not marked 'external:' — the artifact contract is not closed.")
    for a in sorted(dangling)[:15]:
        errors.append(f"    dangling input '{a}'  (needed by {', '.join(dangling[a])})")
    if len(dangling) > 15: errors.append(f"    ... and {len(dangling)-15} more")

# ---------- 6. schema binding ----------
schema_files = sorted(p.stem.replace('.schema', '') for p in (ROOT/'schemas').glob('*.json'))
for p in (ROOT/'schemas').glob('*.json'):
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
        if '$schema' not in d: warns.append(f"schema {p.name}: no $schema declaration")
        if 'properties' not in d: warns.append(f"schema {p.name}: no properties")
    except Exception as e:
        errors.append(f"bad schema {p.name}: {e}")
allbody = "\n".join(bodies.values())
unused = [s for s in schema_files if s not in allbody]
if unused:
    errors.append(f"{len(unused)}/{len(schema_files)} schemas referenced by NO skill: "
                  + ", ".join(unused))

# ---------- 7. description trigger quality ----------
TRIG = re.compile(r'\b(use when|use this when|when the user|trigger|invoke when|call this when)\b', re.I)
notrig = [s['name'] for s in skills if not TRIG.search(s.get('description', ''))]
if notrig:
    warns.append(f"{len(notrig)}/{len(skills)} descriptions contain no when-to-use/trigger "
                 f"language; an agent cannot reliably route to them.")

# ---------- 8. spec density ----------
def sec(t, head, nxt):
    m = re.search(rf'## {re.escape(head)}\n(.*?)\n## {re.escape(nxt)}', t, re.S)
    return m.group(1) if m else ''
MIN_UNIQ = 120
thin = []
for n, t in bodies.items():
    d = next((s.get('description','') for s in skills if s['name'] == n), '')
    uniq = len((d + ' '
                + sec(t, 'Inputs', 'Outputs') + ' '
                + sec(t, 'Outputs', 'Depends on') + ' '
                + sec(t, 'Procedure', 'Hard gates') + ' '
                + sec(t, 'Hard gates', 'Verification / tests') + ' '
                + sec(t, 'Verification / tests', 'Evidence contract')).split())
    if uniq < MIN_UNIQ: thin.append((n, uniq))
if thin:
    thinnest = min(thin, key=lambda x: x[1])
    warns.append(f"{len(thin)}/{len(bodies)} skills carry <{MIN_UNIQ} words of "
                 f"non-boilerplate specification (thinnest: {thinnest[0]} @ "
                 f"{thinnest[1]} words). Spec density this low is design intent, "
                 f"not an implementable contract.")

# ---------- report ----------
print("=" * 72)
if errors:
    print("FAILED\n")
    for e in errors: print("  ERROR  " + e)
else:
    print("PASS — structural checks\n")
if warns:
    print()
    for w in warns: print("  WARN   " + w)
print()
print("-" * 72)
print("CHECKED : section presence, dep name resolution, dependency cycles,")
print("          reachability from entry point, prose/depends_on agreement,")
print("          Inputs->Outputs artifact linkage, schema parse + binding,")
print("          description trigger language, non-boilerplate spec density.")
print("NOT CHECKED : whether any procedure is correct, implementable, or")
print("          sufficient; whether the runtime exists; whether outputs are")
print("          scientifically valid. This validator says nothing about those.")
print("=" * 72)
sys.exit(1 if errors else 0)
