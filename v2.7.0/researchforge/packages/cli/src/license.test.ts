import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describeRestriction, verify } from "./license.js";

test("no license file falls back to community, not to a failure", () => {
  const p = path.join(os.tmpdir(), "rf-nonexistent-license.json");
  const s = verify(null, p);
  assert.equal(s.valid, true);
  assert.equal(s.license?.edition, "community");
  assert.ok(s.restricted.includes("experiment-engine"));
});

test("a malformed license file is invalid and says why", () => {
  const p = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "rf-")), "license.json");
  fs.writeFileSync(p, "{not json");
  const s = verify(null, p);
  assert.equal(s.valid, false);
  assert.match(s.reason!, /not valid JSON/);
});

test("a well-formed license cannot be accepted without a verification key", () => {
  const p = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "rf-")), "license.json");
  fs.writeFileSync(p, JSON.stringify({
    license: { licensee: "acme", edition: "site", expires: null,
               features: ["experiment-engine"], issued: "2026-01-01" },
    signature: "not-a-real-signature",
  }));
  const s = verify(null, p);
  assert.equal(s.valid, false, "an unsigned-but-plausible license must not unlock features");
});

test("restrictions are described in terms of what they gate", () => {
  assert.match(describeRestriction("experiment-engine"), /experiment/i);
});
