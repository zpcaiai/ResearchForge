import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");

test("no release public key is committed in pubkey.ts", () => {
  /* The first version of release-build.mjs called process.exit on failure, which
   * skips the finally that restores the sentinel — so a failed release left a key
   * sitting in the source tree. That is exactly how a key reaches a commit. This
   * test is the backstop for that class of mistake, not for the specific bug. */
  const t = fs.readFileSync(path.join(SRC, "pubkey.ts"), "utf8");
  assert.ok(t.includes("__RESEARCHFORGE_LICENSE_PUBLIC_KEY_PEM__"),
            "the substitution sentinel is gone: a key may have been baked in");
  const pem = /-----BEGIN PUBLIC KEY-----[\s\S]*?-----END PUBLIC KEY-----/.exec(t);
  assert.equal(pem, null, `a PEM block is committed in pubkey.ts:\n${pem?.[0]}`);
});

test("no private key material is committed anywhere in the CLI source", () => {
  // Assembled from fragments so the pattern does not match this file. Written
  // literally, the first version of this test failed on its own source — funny,
  // but it would also have masked a real hit behind a known-noisy failure.
  const marker = ["-----BEGIN", "PRIVATE KEY-----"];
  const re = new RegExp(`${marker[0]}[^\\n]*${marker[1]}`);
  for (const f of fs.readdirSync(SRC)) {
    if (f.endsWith(".test.ts")) continue;
    const t = fs.readFileSync(path.join(SRC, f), "utf8");
    assert.ok(!re.test(t), `${f} contains private key material`);
  }
});
