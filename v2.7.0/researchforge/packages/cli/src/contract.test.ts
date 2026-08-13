import test from "node:test";
import assert from "node:assert/strict";
import { ARTIFACTS, BUILD_ORDER, SKILLS, isRunnable, producerOf, unmetInputs,
         ContractError } from "@researchforge/contracts";
import { PIPELINE, assertOrdering, stageIndex } from "./state.js";

test("every artifact has exactly one producer, and that producer exists", () => {
  for (const [id, spec] of Object.entries(ARTIFACTS)) {
    assert.ok(SKILLS[spec.producer], `${id}: producer ${spec.producer} is not a skill`);
    assert.ok(SKILLS[spec.producer]!.produces.includes(id as never),
      `${id}: producer does not list it among its outputs`);
  }
});

test("every consumed artifact is produced by someone", () => {
  for (const [name, c] of Object.entries(SKILLS)) {
    for (const a of c.consumes) {
      assert.ok(ARTIFACTS[a], `${name} consumes '${a}' which has no producer`);
    }
  }
});

test("build order is a valid topological order of the dependency graph", () => {
  const seen = new Set<string>();
  for (const s of BUILD_ORDER) {
    for (const d of SKILLS[s]!.dependsOn) {
      assert.ok(seen.has(d), `${s} appears before its dependency ${d}`);
    }
    seen.add(s);
  }
  assert.equal(BUILD_ORDER.length, Object.keys(SKILLS).length);
});

test("reproduction precedes ideation in the pipeline", () => {
  assertOrdering();
  assert.ok(stageIndex("SOURCE_REPRO_ATTEMPTED") < stageIndex("IDEAS_READY"));
  assert.ok(stageIndex("REPRO_LEVEL_ESTABLISHED") < stageIndex("IDEAS_READY"));
});

test("the human gate sits between ranking and any expensive work", () => {
  const gate = stageIndex("HUMAN_SELECTION_REQUIRED");
  assert.ok(gate > stageIndex("IDEAS_READY"), "gate must follow ranking");
  assert.ok(gate < stageIndex("EXPERIMENTING"), "gate must precede experiments");
  assert.equal(PIPELINE[gate]!.gate, "human");
});

test("unmetInputs names what is missing rather than just refusing", () => {
  const missing = unmetInputs("idea-ranker", new Set(["idea_portfolio"]));
  assert.ok(missing.length > 0);
  assert.ok(missing.includes("novelty_report" as never));
  assert.equal(isRunnable("idea-ranker", new Set(missing)), false);
});

test("producerOf rejects an unknown artifact instead of returning undefined", () => {
  assert.throws(() => producerOf("not_an_artifact" as never), ContractError);
});

test("every pipeline stage requires only artifacts that exist in the contract", () => {
  for (const s of PIPELINE) {
    for (const a of s.requires) {
      assert.ok(ARTIFACTS[a as never], `stage ${s.state} requires unknown artifact '${a}'`);
    }
    for (const k of s.skills) {
      assert.ok(SKILLS[k], `stage ${s.state} names unknown skill '${k}'`);
    }
  }
});

test("a skill's declared outputs are unique to it across the whole contract", () => {
  const owner = new Map<string, string>();
  for (const [name, c] of Object.entries(SKILLS)) {
    for (const a of c.produces) {
      assert.ok(!owner.has(a), `${a} claimed by both ${owner.get(a)} and ${name}`);
      owner.set(a, name);
    }
  }
});
