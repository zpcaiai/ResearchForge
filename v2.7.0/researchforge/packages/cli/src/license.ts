/** Self-hosted licensing.
 *
 * The commercial model is self-hosted with a license key: the user's papers,
 * data and credentials never leave their machine, which for research data is the
 * difference between a product they can adopt and one their institution forbids.
 *
 * The key is verified offline against a public key. There is no phone-home, both
 * because it would be a data-egress question in exactly the environments this is
 * meant for, and because a research run that dies at hour six because a license
 * server was unreachable is worse than one that was never licensed.
 */
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export interface License {
  licensee: string;
  edition: "community" | "team" | "site";
  expires: string | null;   // ISO date; null = perpetual
  features: string[];
  issued: string;
}

export interface LicenseStatus {
  valid: boolean;
  reason?: string;
  license?: License;
  /** Features unavailable under this license, with what each gates. */
  restricted: string[];
}

const COMMUNITY: License = {
  licensee: "community", edition: "community", expires: null,
  features: ["ingest", "literature", "reproduction", "innovation", "human-gate"],
  issued: "1970-01-01",
};

/** Features the community edition does not include. */
const PAID_FEATURES: Record<string, string> = {
  "experiment-engine": "sandboxed multi-branch experiment execution",
  "manuscript": "evidence-bound manuscript drafting and citation audit",
  "deck": "native editable defense deck generation",
  "release-gate": "release packaging with full provenance verification",
};

export function licensePath(): string {
  return process.env.RESEARCHFORGE_LICENSE ??
         path.join(os.homedir(), ".researchforge", "license.json");
}

export function verify(publicKeyPem: string | null, p = licensePath()): LicenseStatus {
  if (!fs.existsSync(p)) {
    return { valid: true, license: COMMUNITY, reason: "no license file; community edition",
             restricted: Object.keys(PAID_FEATURES) };
  }
  let blob: { license: License; signature: string };
  try {
    blob = JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return { valid: false, reason: `license file at ${p} is not valid JSON`,
             restricted: Object.keys(PAID_FEATURES) };
  }
  if (!publicKeyPem) {
    return { valid: false, reason: "no public key compiled in; cannot verify a license offline",
             restricted: Object.keys(PAID_FEATURES) };
  }
  // The signed message is the re-serialisation of the parsed license, so the
  // issuer must emit fields in this interface's declared order (tools/license/issue.mjs
  // pins that and self-checks it before writing).
  //
  // The digest argument is `null` because Ed25519 is PureEdDSA: it hashes the
  // message internally as part of the scheme, and OpenSSL rejects an externally
  // named digest outright rather than ignoring it.
  //
  // Wrapped because verify() throws — it does not return false — on a malformed
  // compiled-in key or an unparseable one. A build that shipped a corrupt key
  // constant would otherwise crash every command with a stack trace instead of
  // saying that the license could not be verified.
  let ok = false;
  try {
    ok = crypto.verify(
      null, Buffer.from(JSON.stringify(blob.license), "utf8"),
      publicKeyPem, Buffer.from(blob.signature ?? "", "base64"),
    );
  } catch {
    return { valid: false, reason: "license signature could not be checked (bad key or signature encoding)",
             restricted: Object.keys(PAID_FEATURES) };
  }
  if (!ok) {
    return { valid: false, reason: "license signature does not verify",
             restricted: Object.keys(PAID_FEATURES) };
  }
  if (blob.license.expires && new Date(blob.license.expires) < new Date()) {
    return { valid: false, reason: `license expired ${blob.license.expires}`, license: blob.license,
             restricted: Object.keys(PAID_FEATURES) };
  }
  const restricted = Object.keys(PAID_FEATURES).filter((f) => !blob.license.features.includes(f));
  return { valid: true, license: blob.license, restricted };
}

export function describeRestriction(feature: string): string {
  return PAID_FEATURES[feature] ?? feature;
}
