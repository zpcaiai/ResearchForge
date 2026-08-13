#!/usr/bin/env node
/**
 * Release build: compile a licence public key into the CLI, then verify it works.
 *
 * `pubkey.ts` ships with a substitution sentinel, which means every build made
 * from a source checkout verifies nothing and silently runs as community. That is
 * the correct default for a checkout and the wrong one for a release, and nothing
 * in the repo previously closed the gap — so no shipped build could have verified
 * any licence at all.
 *
 * This script closes it, and refuses to finish unless it can prove the key it
 * substituted actually validates a licence signed by the matching private key.
 * A release that compiles but cannot verify is worse than no release: it looks
 * licensed and silently is not.
 *
 *   node tools/license/release-build.mjs --pubkey <path> [--verify-with <license.json>]
 */
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const PUBKEY_TS = path.join(ROOT, "packages/cli/src/pubkey.ts");
const SENTINEL = "__RESEARCHFORGE_LICENSE_PUBLIC_KEY_PEM__";

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
/** Throws rather than exiting.
 *
 * `process.exit` inside a try block skips the finally, so the first version of
 * this script left a release public key sitting in the source tree after every
 * failure — which is precisely how a key ends up in a commit. Failures must
 * unwind, not terminate.
 */
class ReleaseError extends Error {}
function die(msg) { throw new ReleaseError(msg); }

const pubPath = arg("pubkey");
if (!pubPath) die("--pubkey <path-to-public.pem> is required");
if (!fs.existsSync(pubPath)) die(`no such public key: ${pubPath}`);

const pem = fs.readFileSync(pubPath, "utf8");
// PEM parsers ignore leading text, but a key with a warning banner is a demo key.
// Shipping one is indistinguishable from shipping the private key, because the
// matching private key is in the repo.
if (/DEMO|THROWAWAY|do-not-use/i.test(pem)) {
  die("that is the demo key. Its private half is committed, so any build carrying it " +
      "lets anyone mint themselves a perpetual licence. Generate a release keypair with " +
      "tools/license/keygen.mjs and keep the private half off this machine.");
}
let keyObj;
try {
  keyObj = crypto.createPublicKey(pem);
} catch (e) { die(`not a usable public key: ${e.message}`); }
if (keyObj.asymmetricKeyType !== "ed25519") {
  die(`expected an ed25519 key, got ${keyObj.asymmetricKeyType}`);
}
const fingerprint = crypto.createHash("sha256")
  .update(keyObj.export({ type: "spki", format: "der" })).digest("hex").slice(0, 16);

const original = fs.readFileSync(PUBKEY_TS, "utf8");

// The sentinel appears more than once: the file documents its own substitution,
// so the string occurs in prose as well as in the assignment. A blind replace
// rewrites the comment and leaves the constant untouched — the build then carries
// no key while reporting success. Target the assignment and nothing else.
const ASSIGN = /(export const LICENSE_PUBLIC_KEY_PEM: string = )([^;]+);/;
if (!ASSIGN.test(original)) {
  die("cannot find the LICENSE_PUBLIC_KEY_PEM assignment in pubkey.ts");
}
if (!original.includes(SENTINEL)) {
  die("pubkey.ts no longer contains the substitution sentinel — a key is already " +
      "compiled in. Restore it from git before building a release with a different key.");
}
const escaped = pem.trim().replace(/\\/g, "\\\\").replace(/`/g, "\\`").replace(/\$\{/g, "\\${");
const substituted = original.replace(ASSIGN, (_m, lhs) => `${lhs}\`${escaped}\`;`);
if (substituted === original) die("substitution produced no change");
fs.writeFileSync(PUBKEY_TS, substituted);
console.log(`substituted public key ${fingerprint} into the LICENSE_PUBLIC_KEY_PEM assignment`);

let ok = false;
try {
  execFileSync("npx", ["tsc", "-b", "packages/contracts", "packages/cli"],
               { cwd: ROOT, stdio: "inherit" });

  // Proof, not assumption: doctor must report the fingerprint we just compiled in.
  const out = execFileSync("node", ["packages/cli/dist/cli.js", "doctor"],
                           { cwd: ROOT, encoding: "utf8" });
  const clean = out.replace(/\x1b\[[0-9;]*m/g, "");
  if (!clean.includes(fingerprint)) {
    die(`the build does not report the key it was given (${fingerprint}). ` +
        `Substitution or compilation silently did not take.`);
  }
  console.log(`doctor reports key ${fingerprint} — substitution took`);

  const licPath = arg("verify-with");
  if (licPath) {
    if (!fs.existsSync(licPath)) die(`no such licence: ${licPath}`);
    const before = process.env.RESEARCHFORGE_LICENSE;
    process.env.RESEARCHFORGE_LICENSE = path.resolve(licPath);
    const out2 = execFileSync("node", ["packages/cli/dist/cli.js", "doctor"],
                              { cwd: ROOT, encoding: "utf8",
                                env: { ...process.env, RESEARCHFORGE_LICENSE: path.resolve(licPath) } })
      .replace(/\x1b\[[0-9;]*m/g, "");
    if (/license\s+community/.test(out2)) {
      die("the licence did not verify against the compiled-in key — it reports community. " +
          "Either the licence was signed by a different private key, or it has expired.");
    }
    if (before === undefined) delete process.env.RESEARCHFORGE_LICENSE;
    console.log(`licence verified against the compiled-in key`);
  } else {
    console.log("\x1b[33mno --verify-with licence supplied: the build carries a key but " +
                "nothing proved it can validate a real licence.\x1b[0m");
  }
  ok = true;
} catch (e) {
  if (!(e instanceof ReleaseError)) throw e;
  console.error(`\x1b[31mrelease-build: ${e.message}\x1b[0m`);
} finally {
  // The sentinel goes back either way, including on failure. A checkout left
  // holding a release key is how a key ends up in a commit.
  fs.writeFileSync(PUBKEY_TS, original);
  console.log(ok ? "restored the sentinel in the source tree (dist keeps the key)"
                 : "restored the sentinel after a failed build");
}
process.exit(ok ? 0 : 1);
