#!/usr/bin/env node
/** researchforge — self-hosted research runtime. */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import readline from "node:readline/promises";
import { ARTIFACTS, SKILLS, STATES, type RunState } from "@researchforge/contracts";
import { PythonBridge } from "./bridge.js";
import { run, type RunEvent } from "./orchestrator.js";
import { PIPELINE } from "./state.js";
import { describeRestriction, verify } from "./license.js";
import { licensePublicKey, licensePublicKeyFingerprint } from "./pubkey.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "../../..");

const C = {
  dim: (s: string) => `\x1b[2m${s}\x1b[0m`, b: (s: string) => `\x1b[1m${s}\x1b[0m`,
  g: (s: string) => `\x1b[32m${s}\x1b[0m`, y: (s: string) => `\x1b[33m${s}\x1b[0m`,
  r: (s: string) => `\x1b[31m${s}\x1b[0m`, c: (s: string) => `\x1b[36m${s}\x1b[0m`,
};

function parse(argv: string[]) {
  const [cmd, ...rest] = argv;
  const flags: Record<string, string | boolean> = {};
  const sets: Record<string, unknown> = {};
  const pos: string[] = [];
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i]!;
    if (a === "--set") {
      // repeatable: --set key=value. Externals are the inputs that legitimately
      // enter from outside the artifact graph, so they need an explicit door.
      const kv = rest[++i] ?? "";
      const eq = kv.indexOf("=");
      if (eq > 0) {
        const k = kv.slice(0, eq), raw = kv.slice(eq + 1);
        try { sets[k] = JSON.parse(raw); } catch { sets[k] = raw; }
      }
      continue;
    }
    if (a.startsWith("--")) {
      const [k, v] = a.slice(2).split("=");
      if (v !== undefined) flags[k!] = v;
      else if (rest[i + 1] && !rest[i + 1]!.startsWith("--")) flags[k!] = rest[++i]!;
      else flags[k!] = true;
    } else pos.push(a);
  }
  return { cmd, pos, flags, sets };
}

function printEvent(e: RunEvent) {
  switch (e.kind) {
    case "stage": console.log(`\n${C.b("▸ " + e.state)}`); break;
    case "skill": process.stdout.write(C.dim(`    ${e.skill} … `)); break;
    case "skill-ok":
      console.log(C.g("ok") + C.dim(` (${e.produced.length} artifacts)`) +
                  (e.synthetic ? " " + C.y("[SYNTHETIC]") : ""));
      for (const w of e.warnings) console.log(C.y(`      ! ${w}`));
      break;
    case "skill-stub":
      console.log(C.y("not implemented"));
      console.log(C.y(`      would be built in: ${e.batch}`));
      console.log(C.y(`      requires: ${e.missing}`));
      break;
    case "skill-fail":
      console.log(C.r(`failed (${e.kind2})`));
      console.log(C.r(`      ${e.message.split("\n").join("\n      ")}`));
      break;
    case "gate": console.log(C.r(`  ✖ ${e.message}`)); break;
    case "human": console.log(`\n${C.c("⏸  " + e.prompt)}`); break;
  }
}

async function cmdRun(pos: string[], flags: Record<string, string | boolean>,
                      sets: Record<string, unknown>) {
  const locator = pos[0];
  if (!locator) { console.error("usage: researchforge run <paper-url-or-file> [--project DIR]"); return 2; }
  const project = path.resolve(String(flags.project ?? "./research-project"));
  const mode = (String(flags.mode ?? "guided")) as "guided" | "auto" | "analysis-only";
  const offline = Boolean(flags.offline);
  const model = String(flags.model ?? (offline ? "offline" : "anthropic"));

  // No license file at all still means community edition — verify() answers that
  // before it ever looks at the key, so an unlicensed user is never blocked and
  // never sees a key-configuration error that is ours, not theirs.
  const lic = verify(licensePublicKey());
  if (!lic.valid) { console.error(C.r(`license: ${lic.reason}`)); return 3; }
  console.log(C.dim(`license: ${lic.license?.edition ?? "unknown"} edition`));
  if (lic.restricted.length)
    console.log(C.dim(`  not included: ${lic.restricted.map(describeRestriction).join("; ")}`));
  if (offline)
    console.log(C.y("offline: model calls return clearly-marked synthetic output. " +
                    "Nothing produced in this mode is research."));

  // --redo <skills>: delete those skills' outputs so the resumable run re-executes
  // them. This is the documented path out of a stage gate that told you to fix
  // something and try again; without it the only remedy is deleting the project.
  if (flags.redo) {
    const { SKILLS: SK } = await import("@researchforge/contracts");
    for (const name of String(flags.redo).split(",")) {
      const c = (SK as Record<string, { produces: readonly string[] }>)[name.trim()];
      if (!c) { console.error(C.r(`--redo: unknown skill '${name}'`)); return 2; }
      for (const a of c.produces) {
        const p = path.join(project, ARTIFACTS[a as keyof typeof ARTIFACTS]!.path.split("|")[0]!);
        if (fs.existsSync(p)) fs.rmSync(p, { recursive: true, force: true });
      }
      console.log(C.dim(`redo: cleared ${c.produces.length} outputs of ${name.trim()}`));
    }
  }

  fs.mkdirSync(project, { recursive: true });
  const runId = `run-${Date.now().toString(36)}`;
  console.log(C.dim(`project: ${project}\nrun: ${runId}  mode: ${mode}  model: ${model}`));

  const outcome = await run({
    project, repoRoot: REPO_ROOT, runId, mode, model, offline,
    until: flags.until ? (String(flags.until) as RunState) : undefined,
    config: {
      paper_locator: locator,
      project_intent: String(flags.intent ?? "paper-to-innovation run"),
      search_objective: flags["search-objective"] ? String(flags["search-objective"]) : undefined,
      reference_strings: [],
      repro_timebox_seconds: Number(flags["repro-timebox"] ?? 4 * 3600),
      user_feedback: flags.select ? { selected: String(flags.select).split(",") } : undefined,
      ...sets,
    },
    onEvent: printEvent,
  });

  console.log("\n" + "─".repeat(64));
  console.log(`${C.b("state")}      ${outcome.finalState}`);
  console.log(`${C.b("artifacts")}  ${outcome.produced.length}/${Object.keys(ARTIFACTS).length}`);
  if (outcome.synthetic) console.log(C.y("output includes SYNTHETIC artifacts — not research"));
  if (outcome.reachedHumanGate) {
    console.log(C.c(`\n${outcome.humanPrompt}`));
    console.log(C.dim(`\n  researchforge select --project ${project} --ids I-001[,I-002]`));
    return 10;
  }
  if (outcome.blockedBy) {
    console.log(C.y(`\nstopped at ${outcome.blockedBy.skill} (${outcome.blockedBy.kind})`));
    return 11;
  }
  return 0;
}

async function cmdSelect(flags: Record<string, string | boolean>) {
  const project = path.resolve(String(flags.project ?? "./research-project"));
  const ranked = path.join(project, ARTIFACTS["ranked_ideas"]!.path);
  if (!fs.existsSync(ranked)) { console.error("no ranked_ideas yet; run first"); return 2; }
  const data = JSON.parse(fs.readFileSync(ranked, "utf8")) as
    { ranking: { rank: number; idea_id: string; title: string; mode?: string;
                 composite: number; why_not_higher: string }[] };
  console.log(C.b("\nranked directions\n"));
  for (const r of data.ranking) {
    console.log(`  ${C.c(r.idea_id)}  ${r.title}`);
    console.log(C.dim(`      mode=${r.mode ?? "?"}  composite=${r.composite}  ${r.why_not_higher}`));
  }
  let ids = flags.ids ? String(flags.ids).split(",") : [];
  if (ids.length === 0) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const a = await rl.question("\nselect (comma-separated ids, or 'reject'): ");
    rl.close();
    ids = a.split(",").map((s) => s.trim()).filter(Boolean);
  }
  const bridge = new PythonBridge(REPO_ROOT);
  const res = await bridge.run({
    skill: "user-feedback-gate", project, runId: `sel-${Date.now().toString(36)}`,
    mode: "guided", model: "offline", offline: true,
    config: { user_feedback: { selected: ids, note: "cli selection" } },
  });
  if ("ok" in res && res.ok) {
    console.log(C.g(`\nselected: ${ids.join(", ")}`));
    console.log(C.dim("rejected candidates are retained, not deleted"));
    return 0;
  }
  console.error(C.r(JSON.stringify(res, null, 2)));
  return 1;
}

async function cmdStatus(flags: Record<string, string | boolean>) {
  const project = path.resolve(String(flags.project ?? "./research-project"));
  const have = new Set(Object.entries(ARTIFACTS)
    .filter(([, s]) => fs.existsSync(path.join(project, s.path.split("|")[0]!)))
    .map(([id]) => id));
  console.log(C.b(`\n${project}\n`));
  for (const stage of PIPELINE) {
    const missing = stage.requires.filter((a) => !have.has(a));
    const mark = stage.requires.length === 0 ? C.dim("·")
      : missing.length === 0 ? C.g("✔") : missing.length === stage.requires.length ? C.dim("·") : C.y("◐");
    console.log(`  ${mark} ${stage.state}${missing.length && missing.length < stage.requires.length
      ? C.dim(`   missing: ${missing.join(", ")}`) : ""}`);
  }
  console.log(C.dim(`\n${have.size}/${Object.keys(ARTIFACTS).length} artifacts on disk`));
  return 0;
}

async function cmdDoctor() {
  const bridge = new PythonBridge(REPO_ROOT);
  console.log(C.b("\nresearchforge doctor\n"));
  const digest = await bridge.contractDigest().catch(() => null);
  console.log(`  python bridge     ${digest ? C.g("ok") : C.r("FAILED")}`);
  console.log(`  contract digest   ${digest ?? "-"}`);
  console.log(`  skills            ${Object.keys(SKILLS).length}`);
  console.log(`  artifacts         ${Object.keys(ARTIFACTS).length}`);
  console.log(`  states            ${STATES.length}`);
  const lic = verify(licensePublicKey());
  console.log(`  license           ${lic.valid ? C.g(lic.license?.edition ?? "?") : C.r(lic.reason ?? "invalid")}`);
  // Printed so a support conversation can distinguish "your license is forged"
  // from "this build shipped the wrong verification key".
  console.log(`  license key       ${licensePublicKeyFingerprint() ?? C.dim("none compiled in (community only)")}`);
  for (const k of ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SEMANTIC_SCHOLAR_API_KEY",
                   "RESEARCHFORGE_CONTACT_EMAIL"]) {
    console.log(`  ${k.padEnd(30)} ${process.env[k] ? C.g("set") : C.dim("unset (BYOK)")}`);
  }
  return digest ? 0 : 1;
}

const HELP = `
researchforge — self-hosted research runtime

  run <paper>        ingest a paper and drive the pipeline to the human gate
      --project DIR  --mode guided|auto  --offline  --until STATE  --select IDS
      --set key=value   supply a declared external input (repeatable)
      --redo SKILLS     clear those skills' outputs and re-run them
  select             review ranked directions and choose
  status             what exists on disk, by stage
  doctor             environment, contract digest, license, keys

Reproduction runs BEFORE ideation. If the source paper cannot be reproduced the
run does not stop — the comparison mode narrows and the admissible innovation
modes narrow with it.
`;

const { cmd, pos, flags, sets } = parse(process.argv.slice(2));
const code = await (async () => {
  switch (cmd) {
    case "run": return cmdRun(pos, flags, sets);
    case "select": return cmdSelect(flags);
    case "status": return cmdStatus(flags);
    case "doctor": return cmdDoctor();
    default: console.log(HELP); return cmd ? 2 : 0;
  }
})();
process.exit(code);
