#!/usr/bin/env node
/** researchforge — license signing keypair generator (ISSUING SIDE ONLY).
 *
 * Run this once, on a machine the customer never touches, to produce the keypair
 * that signs every ResearchForge license. The private key is the entire security
 * boundary of the commercial model: anyone holding it can mint a perpetual site
 * license for themselves, and because verification is offline there is no
 * revocation channel to undo it. Treat a leak as "rotate the key and re-issue
 * every outstanding license", not as "revoke one license".
 *
 * Ed25519 rather than RSA: a 64-byte signature and a 44-character public key fit
 * in a hand-pasted license file and in a source constant, key generation has no
 * parameter choices to get wrong (no key size, no exponent, no padding mode —
 * RSA's PKCS#1 v1.5 vs PSS confusion is a whole class of verifier bugs we simply
 * do not have), and signing is deterministic, so there is no per-signature
 * nonce whose reuse or bias would leak the private key the way ECDSA's does.
 *
 * Usage:
 *   node tools/license/keygen.mjs --out /secure/path/researchforge-signing.key
 *                                 [--pub /path/to/public.pem] [--force]
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const flags = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const eq = a.indexOf("=");
    if (eq > 0) flags[a.slice(2, eq)] = a.slice(eq + 1);
    else if (argv[i + 1] && !argv[i + 1].startsWith("--")) flags[a.slice(2)] = argv[++i];
    else flags[a.slice(2)] = true;
  }
  return flags;
}

/** Short, stable identifier for a public key.
 *
 * Printed here, by issue.mjs, and by the server's /health so an operator can
 * confirm the CLI shipped with the same key that signs licenses. A key mismatch
 * otherwise surfaces only as "signature does not verify" on a customer's
 * machine, which is indistinguishable from a forged license.
 */
export function publicKeyFingerprint(publicKeyPem) {
  const der = crypto.createPublicKey(publicKeyPem).export({ type: "spki", format: "der" });
  return crypto.createHash("sha256").update(der).digest("hex").slice(0, 16);
}

function main(argv) {
  const flags = parseArgs(argv);
  const out = flags.out;
  if (!out || out === true) {
    console.error("usage: keygen.mjs --out <private-key-path> [--pub <path>] [--force]");
    return 2;
  }
  const outPath = path.resolve(String(out));

  // Refuse to clobber without --force. Overwriting a signing key silently
  // orphans every license already in customers' hands, and the failure only
  // shows up later, on their machines, as an invalid license.
  if (fs.existsSync(outPath) && !flags.force) {
    console.error(`refusing to overwrite existing private key at ${outPath}`);
    console.error("if you truly mean to replace it, re-run with --force — every license");
    console.error("signed by the old key will stop verifying once the CLI ships the new one.");
    return 3;
  }

  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const privPem = privateKey.export({ type: "pkcs8", format: "pem" });
  const pubPem = publicKey.export({ type: "spki", format: "pem" });

  // 0700 on the directory and 0600 on the key: on a shared build host, a
  // world-readable signing key is the whole attack. Node applies `mode` only
  // when it creates the file, so chmod explicitly for the --force path.
  fs.mkdirSync(path.dirname(outPath), { recursive: true, mode: 0o700 });
  fs.writeFileSync(outPath, privPem, { mode: 0o600 });
  fs.chmodSync(outPath, 0o600);

  const fp = publicKeyFingerprint(pubPem);
  if (flags.pub && flags.pub !== true) {
    const pubPath = path.resolve(String(flags.pub));
    fs.mkdirSync(path.dirname(pubPath), { recursive: true });
    fs.writeFileSync(pubPath, pubPem);
    console.error(`public key written to ${pubPath}`);
  }

  console.error("");
  console.error("  ############################################################");
  console.error("  #  PRIVATE SIGNING KEY WRITTEN — THIS FILE IS THE PRODUCT  #");
  console.error("  ############################################################");
  console.error("");
  console.error(`  path        ${outPath}   (mode 0600)`);
  console.error(`  fingerprint ${fp}`);
  console.error("");
  console.error("  Anyone with this file can issue themselves a perpetual site license.");
  console.error("  Verification is offline, so there is no way to revoke one after the fact.");
  console.error("  Keep it off developer laptops, out of the repo, and out of CI logs.");
  console.error("");
  console.error("  Compile the public key below into the CLI: paste it into");
  console.error("  packages/cli/src/pubkey.ts (or have your build substitute it).");
  console.error("");

  // Public key to stdout so it can be piped; everything else went to stderr.
  process.stdout.write(pubPem);
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(main(process.argv.slice(2)));
}
