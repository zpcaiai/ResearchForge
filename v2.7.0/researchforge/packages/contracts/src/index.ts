export * from "./generated.js";
import { ARTIFACTS, SKILLS, type ArtifactId, type SkillName } from "./generated.js";

/** Thrown when the orchestrator would violate the artifact contract.
 *
 * The TypeScript side gets its own check rather than relying on Python's: a
 * contract violation caught before the subprocess starts names the orchestration
 * bug, while one caught inside Python names only the symptom.
 */
export class ContractError extends Error {}

export function producerOf(a: ArtifactId): SkillName {
  const spec = ARTIFACTS[a];
  if (!spec) throw new ContractError(`unknown artifact '${a}'`);
  return spec.producer;
}

/** Which declared inputs of `skill` have not been produced yet. */
export function unmetInputs(skill: SkillName, produced: ReadonlySet<string>): ArtifactId[] {
  const c = SKILLS[skill];
  if (!c) throw new ContractError(`unknown skill '${skill}'`);
  // feedback reads are runtime loop reads, not build-order prerequisites
  return c.consumes.filter((a) => !produced.has(a));
}

export function isRunnable(skill: SkillName, produced: ReadonlySet<string>): boolean {
  return unmetInputs(skill, produced).length === 0;
}

/** Skills whose every declared input is satisfied, in build order. */
export function readySkills(
  produced: ReadonlySet<string>,
  done: ReadonlySet<string>,
  order: readonly SkillName[],
): SkillName[] {
  return order.filter((s) => !done.has(s) && isRunnable(s, produced));
}
