/** End-to-end tests over the real issuing path.
 *
 * These deliberately shell out to tools/license/issue.mjs and verify with real
 * Ed25519 against the committed throwaway demo key rather than mocking crypto.
 * The bug this suite exists to catch is a mismatch between the bytes the issuer
 * signs and the bytes the verifier checks (see the signing-bytes note in
 * issue.mjs) — a mock signer would agree with any convention and would still be
 * green on the day every issued license stopped verifying.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verify } from "./license.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "../../..");
const DEMO = path.join(REPO_ROOT, "tools", "license", "demo");
const ISSUE = path.join(REPO_ROOT, "tools", "license", "issue.mjs");
const DEMO_PRIVATE = path.join(DEMO, "DEMO-THROWAWAY-do-not-use.private.pem");
const DEMO_PUBLIC = fs.readFileSync(path.join(DEMO, "DEMO-THROWAWAY-do-not-use.public.pem"), "utf8");

function tmp(name: string): string {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), "rf-lic-")), name);
}

/** Run the real issuer. Returns the path it wrote. */
function issue(args: string[], out: string): string {
  execFileSync(process.execPath, [ISSUE, ...args, "--key", DEMO_PRIVATE, "--out", out],
               { stdio: "pipe" });
  return out;
}

test("a license issued by issue.mjs verifies against the compiled-in public key", () => {
  const p = issue(["--licensee", "Acme Lab", "--edition", "team", "--expires", "2099-01-01"],
                  tmp("license.json"));
  const s = verify(DEMO_PUBLIC, p);
  assert.equal(s.valid, true, s.reason);
  assert.equal(s.license?.licensee, "Acme Lab");
  assert.equal(s.license?.edition, "team");
  assert.ok(s.license?.features.includes("experiment-engine"));
  assert.ok(!s.restricted.includes("experiment-engine"));
  // team is not site: the paid features it did not buy are still named as
  // restricted rather than silently unlocked.
  assert.ok(s.restricted.includes("release-gate"));
});

test("the shipped demo license verifies — the signing-bytes convention is stable", () => {
  // Guards the round trip through a file on disk, including the non-ASCII
  // characters in the demo licensee name: if anything in the issuer or the
  // verifier started escaping UTF-8 differently, the signed bytes would diverge
  // and this is where it shows up.
  const s = verify(DEMO_PUBLIC, path.join(DEMO, "demo-license.json"));
  assert.equal(s.valid, true, s.reason);
  assert.equal(s.license?.edition, "site");
  assert.deepEqual(s.restricted, []);
});

test("tampering with a signed license fails — you cannot self-promote to site", () => {
  const p = issue(["--licensee", "Acme Lab", "--edition", "team", "--expires", "2099-01-01",
                   "--features", "ingest"], tmp("license.json"));
  const blob = JSON.parse(fs.readFileSync(p, "utf8"));
  // The obvious attack: keep the valid signature, edit the terms it covers.
  blob.license.edition = "site";
  blob.license.features.push("experiment-engine", "release-gate");
  fs.writeFileSync(p, JSON.stringify(blob, null, 2));

  const s = verify(DEMO_PUBLIC, p);
  assert.equal(s.valid, false, "an edited license must not unlock the features it was edited to claim");
  assert.match(s.reason!, /signature/);
  assert.ok(s.restricted.includes("experiment-engine"));
});

test("tampering with the expiry date fails — an expired license cannot be extended", () => {
  const p = issue(["--licensee", "Acme Lab", "--edition", "team", "--expires", "2020-01-01",
                   "--allow-past-expiry"], tmp("license.json"));
  const blob = JSON.parse(fs.readFileSync(p, "utf8"));
  blob.license.expires = null; // "perpetual"
  fs.writeFileSync(p, JSON.stringify(blob, null, 2));
  assert.equal(verify(DEMO_PUBLIC, p).valid, false);
});

test("changing a single character of the licensee fails", () => {
  const p = issue(["--licensee", "Acme Lab", "--edition", "site", "--perpetual"], tmp("license.json"));
  const blob = JSON.parse(fs.readFileSync(p, "utf8"));
  blob.license.licensee = "Acme Lav"; // one byte
  fs.writeFileSync(p, JSON.stringify(blob, null, 2));
  assert.equal(verify(DEMO_PUBLIC, p).valid, false,
               "a license is not transferable by editing the name on it");
});

test("an expired license is refused even though its signature is genuine", () => {
  const p = issue(["--licensee", "Lapsed Lab", "--edition", "team", "--expires", "2020-01-01",
                   "--allow-past-expiry"], tmp("license.json"));
  const s = verify(DEMO_PUBLIC, p);
  assert.equal(s.valid, false);
  assert.match(s.reason!, /expired 2020-01-01/);
  // The terms are still reported: the user needs to know whose license lapsed.
  assert.equal(s.license?.licensee, "Lapsed Lab");
  assert.ok(s.restricted.includes("experiment-engine"));
});

test("a license signed by a different key fails — the rogue-issuer attack", () => {
  // Anyone can run keygen.mjs and sign a well-formed license with terms of their
  // choosing. The only thing that stops it is the verifier trusting exactly one
  // public key, so this must fail even though the signature is internally valid.
  const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
  const license = {
    licensee: "Attacker", edition: "site", expires: null,
    features: ["ingest", "literature", "reproduction", "innovation", "human-gate",
               "experiment-engine", "manuscript", "deck", "release-gate"],
    issued: "2026-01-01",
  };
  const signature = crypto.sign(null, Buffer.from(JSON.stringify(license), "utf8"), privateKey)
    .toString("base64");
  const p = tmp("license.json");
  fs.writeFileSync(p, JSON.stringify({ license, signature }, null, 2));

  // Sanity: the forgery is a real signature under its own key, so the test is
  // exercising key identity rather than a malformed blob.
  const rogueP = publicKey.export({ type: "spki", format: "pem" }) as string;
  assert.equal(verify(rogueP, p).valid, true);

  const s = verify(DEMO_PUBLIC, p);
  assert.equal(s.valid, false, "a license signed by anyone else must not be accepted");
  assert.match(s.reason!, /signature/);
});

test("community fallback still works with no license file and a key compiled in", () => {
  // The paid path must not have broken the unpaid one: an absent license file is
  // community edition, not an error, even once a verification key exists.
  const s = verify(DEMO_PUBLIC, path.join(os.tmpdir(), "rf-definitely-absent-license.json"));
  assert.equal(s.valid, true);
  assert.equal(s.license?.edition, "community");
  assert.ok(s.restricted.includes("experiment-engine"));
});

test("a garbage signature is reported, not thrown", () => {
  // Ed25519 verification returns false for a wrong-length signature, but a
  // license file is attacker-supplied input on the customer's own machine and a
  // crash here would take out an entire run.
  const p = tmp("license.json");
  fs.writeFileSync(p, JSON.stringify({
    license: { licensee: "x", edition: "site", expires: null, features: [], issued: "2026-01-01" },
    signature: "!!!not-base64!!!",
  }));
  const s = verify(DEMO_PUBLIC, p);
  assert.equal(s.valid, false);
});

test("the issuer refuses to emit an unverifiable license", () => {
  // The self-check is the safety net for the signing-bytes convention. Feed it a
  // key that is not the signing key's type and it must fail loudly rather than
  // write a file that no customer could ever validate.
  const rsa = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 })
    .privateKey.export({ type: "pkcs8", format: "pem" }) as string;
  const keyPath = tmp("rsa.pem");
  fs.writeFileSync(keyPath, rsa);
  const out = tmp("license.json");
  assert.throws(() => execFileSync(
    process.execPath, [ISSUE, "--licensee", "Acme", "--edition", "site", "--perpetual",
                       "--key", keyPath, "--out", out], { stdio: "pipe" }));
  assert.equal(fs.existsSync(out), false, "nothing may be written when signing is refused");
});

test("the issuer refuses an unknown feature id rather than shipping a dead license", () => {
  const out = tmp("license.json");
  assert.throws(() => execFileSync(
    process.execPath, [ISSUE, "--licensee", "Acme", "--edition", "team", "--perpetual",
                       "--features", "experiment-engien", "--key", DEMO_PRIVATE, "--out", out],
    { stdio: "pipe" }));
  assert.equal(fs.existsSync(out), false);
});
