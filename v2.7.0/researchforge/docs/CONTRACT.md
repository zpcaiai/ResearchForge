# The artifact contract

`manifests/artifact-graph.json` is the only source of truth. Everything else is generated from it or
checked against it.

## Rules

1. **Exactly one producer per artifact.** Two writers means no one owns whether it is correct.
2. **Every input resolves.** An input is an artifact id, an `external:` input, or a declared
   `feedback:` read. There is no fourth option and no implicit input.
3. **`depends_on` is derived, never written.** It is the set of producers of a skill's build inputs.
4. **The build graph is acyclic.** Genuine runtime loops — tree search reading accumulated findings,
   the integrity gate informing the reviewer — are declared as `feedback:` edges. They are validated
   to name real artifacts and excluded from build ordering.
5. **Internal artifacts leave the public contract but not the disk.** An artifact whose producer and
   only consumer are the same skill is internal: still written, still provenanced, but no other
   skill may depend on it.

## Changing the contract

Editing `generated.ts` or `generated.py` does nothing; they are overwritten. Edit the manifest, run
`npm run codegen`, and run both test suites. A change that breaks a consumer will break a test,
which is the point.

## What the runtime enforces, and where

| rule | enforced in |
|---|---|
| producer ownership on write | `ArtifactStore._may_write` |
| declared inputs on read | `ArtifactStore._may_read` |
| schema validity | `ArtifactStore._validate`, on write |
| a skill produced what it claims | `Skill._verify_outputs`, after execute |
| a stage's required artifacts exist | orchestrator, before leaving the stage |
| reproduction precedes ideation | `state.ts:assertOrdering`, asserted at startup |

The last two matter most. A skill that returns success without writing its artifacts is caught
immediately rather than three stages later, and a stage cannot be left on the strength of a skill's
own report that it succeeded — only on the artifacts actually existing.
