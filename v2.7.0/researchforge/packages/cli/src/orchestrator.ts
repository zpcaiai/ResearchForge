/** Drives the pipeline, gates the transitions, and resumes without inventing state. */
import fs from "node:fs";
import path from "node:path";
import { ARTIFACTS, SKILLS, type RunState, type SkillName } from "@researchforge/contracts";
import { PythonBridge, type RunResponse } from "./bridge.js";
import { PIPELINE, assertOrdering, stageIndex } from "./state.js";

export interface RunOptions {
  project: string;
  repoRoot: string;
  runId: string;
  mode: "guided" | "auto" | "analysis-only";
  model: string;
  offline: boolean;
  config: Record<string, unknown>;
  until?: RunState;
  onEvent?: (e: RunEvent) => void;
}

export type RunEvent =
  | { kind: "stage"; state: RunState }
  | { kind: "skill"; skill: SkillName; status: "start" }
  | { kind: "skill-ok"; skill: SkillName; produced: string[]; warnings: string[]; synthetic: boolean }
  | { kind: "skill-stub"; skill: SkillName; batch: string; missing: string }
  | { kind: "skill-fail"; skill: SkillName; kind2: string; message: string }
  | { kind: "human"; prompt: string; artifact: string }
  | { kind: "gate"; gate: string; message: string };

export interface RunOutcome {
  finalState: RunState;
  reachedHumanGate: boolean;
  humanPrompt?: string;
  blockedBy?: { skill: SkillName; kind: string; message: string };
  produced: string[];
  warnings: { skill: SkillName; warning: string }[];
  synthetic: boolean;
}

/** State is derived from artifacts on disk, never from a resumed in-memory flag.
 *
 * This is what makes resume safe: a run killed mid-stage restarts from what
 * actually exists rather than from what it last believed. */
function producedSet(project: string): Set<string> {
  const out = new Set<string>();
  for (const [id, spec] of Object.entries(ARTIFACTS)) {
    const p = path.join(project, spec.path.split("|")[0]!);
    if (fs.existsSync(p)) out.add(id);
  }
  return out;
}

export async function run(opts: RunOptions): Promise<RunOutcome> {
  assertOrdering();
  const bridge = new PythonBridge(opts.repoRoot);
  const emit = opts.onEvent ?? (() => {});
  const warnings: RunOutcome["warnings"] = [];
  let synthetic = false;
  let finalState: RunState = "INGESTED";
  const stopAt = opts.until ? stageIndex(opts.until) : PIPELINE.length - 1;

  for (let i = 0; i <= stopAt; i++) {
    const stage = PIPELINE[i]!;
    emit({ kind: "stage", state: stage.state });

    for (const skill of stage.skills) {
      const have = producedSet(opts.project);
      // idempotent resume: a skill whose outputs all exist is not re-run
      const outs = SKILLS[skill]!.produces;
      const mustRerun = (stage.rerun ?? []).includes(skill);
      if (!mustRerun && outs.length > 0 && outs.every((a) => have.has(a))) continue;

      emit({ kind: "skill", skill, status: "start" });
      const res: RunResponse = await bridge.run({
        skill, project: opts.project, runId: opts.runId, mode: opts.mode,
        model: opts.model, offline: opts.offline, config: opts.config,
      });

      if ("ok" in res && res.ok) {
        if (res.synthetic) synthetic = true;
        for (const w of res.warnings) warnings.push({ skill, warning: w });
        emit({ kind: "skill-ok", skill, produced: res.produced, warnings: res.warnings,
               synthetic: res.synthetic });
        continue;
      }
      if ("needs_human" in res && res.needs_human) {
        emit({ kind: "human", prompt: res.prompt, artifact: res.options_artifact });
        return { finalState: "HUMAN_SELECTION_REQUIRED", reachedHumanGate: true,
                 humanPrompt: res.prompt, produced: [...producedSet(opts.project)],
                 warnings, synthetic };
      }
      const err = (res as { error: { kind: string; message: string; batch?: string;
                                     missing?: string } }).error;
      if (err.kind === "not_implemented") {
        emit({ kind: "skill-stub", skill, batch: err.batch ?? "?", missing: err.missing ?? "?" });
      } else {
        emit({ kind: "skill-fail", skill, kind2: err.kind, message: err.message });
      }
      return { finalState, reachedHumanGate: false,
               blockedBy: { skill, kind: err.kind, message: err.message },
               produced: [...producedSet(opts.project)], warnings, synthetic };
    }

    // gate: the stage may only be left once its required artifacts actually exist
    const have = producedSet(opts.project);
    const missing = stage.requires.filter((a) => !have.has(a));
    if (missing.length > 0) {
      const msg =
        `cannot leave ${stage.state}: required artifact(s) ${missing.join(", ")} do not exist. ` +
        `A later stage may not synthesize evidence an earlier one failed to produce.`;
      emit({ kind: "gate", gate: stage.state, message: msg });
      return { finalState, reachedHumanGate: false,
               blockedBy: { skill: (stage.skills[0] ?? "researchforge-orchestrator") as SkillName,
                            kind: "stage_gate", message: msg },
               produced: [...have], warnings, synthetic };
    }
    finalState = stage.state;
    await bridge.run({ skill: "researchforge-orchestrator", project: opts.project,
                       runId: opts.runId, mode: opts.mode, model: opts.model,
                       offline: opts.offline,
                       config: { state: finalState, history: PIPELINE.slice(0, i + 1).map(s => s.state) } });
  }
  return { finalState, reachedHumanGate: false, produced: [...producedSet(opts.project)],
           warnings, synthetic };
}
