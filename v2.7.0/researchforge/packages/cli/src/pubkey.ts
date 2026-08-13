/** The license verification key, compiled into the binary.
 *
 * This constant is the only thing standing between a paid edition and a text
 * editor. It is a public key, so there is nothing secret about its value — what
 * matters is that it is *fixed at build time* and cannot be pointed somewhere
 * else at run time.
 *
 * There is deliberately no environment variable, no config file and no CLI flag
 * that overrides it. Any of those would be a complete bypass rather than a
 * convenience: an attacker generates their own Ed25519 keypair with
 * tools/license/keygen.mjs, signs themselves a perpetual site license, points
 * the override at their own public key, and the verifier happily agrees. The
 * key has to be as hard to change as the code that checks it.
 *
 * HOW TO SET IT
 *   1. Generate the keypair once, on the issuing machine:
 *        node tools/license/keygen.mjs --out /secure/researchforge-signing.key
 *      It prints the public key PEM to stdout.
 *   2. Either paste that PEM in place of the placeholder below, or have the
 *      release build substitute it — the placeholder is a single sentinel
 *      string so a build step can replace it textually:
 *        sed -i "s|__RESEARCHFORGE_LICENSE_PUBLIC_KEY_PEM__|$(...)|" pubkey.ts
 *   3. Confirm with `researchforge doctor`, which prints the key fingerprint;
 *      it must match the one keygen.mjs and the issuing server report.
 *
 * Until it is set, the CLI still runs — it just cannot verify a license, so
 * every install is community edition. That is the correct failure direction: a
 * misconfigured build must not unlock paid features, and it must not stop a
 * community user from working either.
 */

import crypto from "node:crypto";

const PLACEHOLDER = "__RESEARCHFORGE_LICENSE_PUBLIC_KEY_PEM__";

/** Replaced at build time with an Ed25519 SPKI PEM. */
export const LICENSE_PUBLIC_KEY_PEM: string = PLACEHOLDER;

/** The compiled-in key, or null if this build never had one substituted. */
export function licensePublicKey(): string | null {
  const pem = LICENSE_PUBLIC_KEY_PEM;
  // A build that forgot the substitution leaves the sentinel in place. Return
  // null rather than the sentinel so verify() reports "no public key compiled
  // in" instead of a confusing OpenSSL parse failure.
  if (!pem || pem === PLACEHOLDER || !pem.includes("BEGIN PUBLIC KEY")) return null;
  return pem;
}

/** Short fingerprint of the compiled-in key, for `doctor` output.
 *
 * Lets an operator check that the CLI they installed and the key that signed
 * their license are the same one. Without it, a key-rotation mistake is
 * indistinguishable from a forged license: both say "signature does not verify".
 */
export function licensePublicKeyFingerprint(): string | null {
  const pem = licensePublicKey();
  if (!pem) return null;
  try {
    const der = crypto.createPublicKey(pem).export({ type: "spki", format: "der" });
    return crypto.createHash("sha256").update(der).digest("hex").slice(0, 16);
  } catch {
    return null;
  }
}
