/** The research state machine.
 *
 * Two properties matter more than the transition table itself.
 *
 * 1. **Reproduction precedes ideation.** `IDEAS_READY` is unreachable except
 *    through `REPRO_LEVEL_ESTABLISHED`. An idea whose feasibility was estimated
 *    against a code base nobody tried to run is not an estimate.
 * 2. **A later stage may never synthesize evidence an earlier one failed to
 *    produce.** Transitions are gated on artifacts existing, not on a skill
 *    reporting that it succeeded.
 */
import type { RunState, SkillName } from "@researchforge/contracts";

export interface Stage {
  readonly state: RunState;
  readonly skills: readonly SkillName[];
  /** Artifacts that must exist before the run may leave this stage. */
  readonly requires: readonly string[];
  readonly gate?: "human" | "reproduction";
  /** Skills that must re-run here even though their outputs already exist.
   *
   * Most skills are one-shot and are skipped on resume. A few are stores rather
   * than transforms — the evidence graph acquires support edges only once
   * experiments have run — and skipping them because their file exists is how a
   * claim never gets connected to the run that supports it. */
  readonly rerun?: readonly SkillName[];
}

export const PIPELINE: readonly Stage[] = [
  { state: "INGESTED", skills: ["project-repo-manager", "sandbox-provisioner", "paper-ingest"],
    requires: ["quest_repo", "normalized_text", "source_manifest", "sandbox_manifest"] },
  { state: "MODELED", skills: ["paper-model-builder"],
    requires: ["paper_model", "contribution_atoms"] },
  { state: "EVIDENCE_EXPANDED",
    skills: ["literature-provider-manager", "literature-search", "citation-resolver",
             "claim-evidence-graph"],
    requires: ["provider_registry", "coverage_report", "literature_candidates", "evidence_graph"] },
  { state: "SOURCE_REPRO_ATTEMPTED", skills: ["result-reproducer"],
    requires: ["source_repro_report", "repro_failure_taxonomy"], gate: "reproduction" },
  { state: "REPRO_LEVEL_ESTABLISHED", skills: ["reproduction-fallback-planner"],
    requires: ["comparison_mode", "idea_mode_constraints"] },
  { state: "IDEAS_READY",
    skills: ["idea-seed-miner", "idea-portfolio-generator", "idea-evaluator", "idea-ranker"],
    requires: ["idea_portfolio", "ranked_ideas", "novelty_report", "feasibility_report"] },
  { state: "HUMAN_SELECTION_REQUIRED", skills: ["user-feedback-gate"],
    requires: ["selected_direction"], gate: "human" },
  { state: "DIRECTION_SELECTED", skills: [], requires: ["selected_direction"] },
  { state: "BLUEPRINT_READY", skills: ["research-blueprint-compiler", "evaluator-builder"],
    requires: ["research_blueprint", "experiment_specs"] },
  { state: "BASELINE_ESTABLISHED", skills: [], requires: ["comparison_mode"] },
  { state: "EXPERIMENTING", skills: ["codebase-scaffolder", "experiment-runner"],
    requires: ["experiment_ledger"] },
  { state: "EVIDENCE_LOCKED",
    skills: ["data-analyst", "integrity-auditor", "finding-memory", "claim-evidence-graph"],
    requires: ["stats_audit", "findings", "evidence_graph"],
    rerun: ["claim-evidence-graph"] },
  { state: "WRITING", skills: ["manuscript-builder", "figure-factory"],
    requires: ["manuscript_draft"] },
  { state: "REVIEWING", skills: ["claim-citation-auditor", "review-simulator"],
    requires: ["claim_audit", "review_report"] },
  { state: "DEFENSE_READY", skills: ["deck-factory"], requires: ["defense_deck"] },
  { state: "RELEASED", skills: ["release-gate"], requires: ["release_manifest"] },
];

export function stageIndex(s: RunState): number {
  return PIPELINE.findIndex((p) => p.state === s);
}

/** Reproduction must be attempted before ideation — enforced structurally. */
export function assertOrdering(): void {
  const repro = stageIndex("SOURCE_REPRO_ATTEMPTED");
  const level = stageIndex("REPRO_LEVEL_ESTABLISHED");
  const ideas = stageIndex("IDEAS_READY");
  if (!(repro < level && level < ideas)) {
    throw new Error(
      "pipeline ordering violated: reproduction must precede ideation. " +
      "This is not a preference; ideation before reproduction produces feasibility " +
      "estimates with no basis.",
    );
  }
}
