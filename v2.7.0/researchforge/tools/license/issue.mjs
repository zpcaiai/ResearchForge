#!/usr/bin/env node
/** researchforge — license issuer (ISSUING SIDE ONLY).
 *
 * Signs a license blob that packages/cli/src/license.ts can verify offline. This
 * script must only ever run on a machine that holds the private signing key,
 * which is not the customer's machine and not CI that customers can trigger.
 *
 * THE SIGNING BYTES — the one thing that must not drift.
 * The verifier does, verbatim:
 *     crypto.verify(<alg>, Buffer.from(JSON.stringify(blob.license)), pub, sig)
 * so the signed message is the UTF-8 bytes of `JSON.stringify(license)` as
 * produced *after the verifier has parsed the file*. That makes the message
 * depend on property order, because JSON.stringify walks own keys in insertion
 * order and JSON.parse preserves the order of the text it read. Two consequences
 * this file is built around:
 *   1. The license object is constructed in exactly the field order declared by
 *      the License interface (licensee, edition, expires, features, issued), and
 *      that same object is what gets serialised into the file. Whitespace and
 *      indentation are free — the verifier re-serialises from the parsed object,
 *      so pretty-printing cannot break it. Key order is not free.
 *   2. Nothing may be added to the license object after signing, and no field
 *      may be dropped: either changes the byte string and every issued license
 *      fails verification on the customer's machine with "signature does not
 *      verify" — indistinguishable, to them, from us accusing them of forgery.
 * The self-check at the bottom exists because that failure mode is invisible
 * here and expensive there: we round-trip the finished file through
 * parse -> stringify -> verify with the *public* key before writing anything.
 *
 * Ed25519, not RSA: see keygen.mjs. Short signatures that fit in a pasteable
 * file, no padding-mode footguns, deterministic (no nonce to leak the key).
 *
 * No phone-home is involved anywhere in this design. The customer's CLI never
 * contacts us to check a license: research environments treat outbound network
 * access as a compliance question, and a six-hour experiment run must not die at
 * hour five because our license server was unreachable.
 *
 * Usage:
 *   node tools/license/issue.mjs --licensee "Acme Lab" --edition team \
 *        --expires 2027-01-01 --features experiment-engine,manuscript \
 *        --key /secure/researchforge-signing.key --out license.json
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

export const EDITIONS = ["community", "team", "site"];

/** Feature ids the verifier knows about.
 *
 * Mirrors COMMUNITY.features + PAID_FEATURES in packages/cli/src/license.ts. A
 * typo here is the worst kind of bug in this system: it produces a perfectly
 * valid signature over a feature the CLI has never heard of, so the paying
 * customer is silently unlicensed and we only learn about it from a support
 * ticket. Hence unknown features are refused rather than warned about.
 */
export const KNOWN_FEATURES = [
  "ingest", "literature", "reproduction", "innovation", "human-gate",
  "experiment-engine", "manuscript", "deck", "release-gate",
];

/** Editions get a default feature set so the common case cannot be fat-fingered. */
const EDITION_FEATURES = {
  community: ["ingest", "literature", "reproduction", "innovation", "human-gate"],
  team: ["ingest", "literature", "reproduction", "innovation", "human-gate",
         "experiment-engine", "manuscript"],
  site: [...KNOWN_FEATURES],
};

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function isoDay(d = new Date()) {
  return d.toISOString().slice(0, 10);
}

export class IssueError extends Error {}

/** Validate the requested terms and normalise them into the License shape. */
function buildLicense(opts) {
  const licensee = String(opts.licensee ?? "").trim();
  if (!licensee) throw new IssueError("--licensee is required");
  if (licensee.length > 200) throw new IssueError("--licensee is implausibly long");

  const edition = String(opts.edition ?? "");
  if (!EDITIONS.includes(edition)) {
    throw new IssueError(`--edition must be one of ${EDITIONS.join("|")}`);
  }

  if (opts.perpetual && opts.expires) {
    throw new IssueError("--perpetual and --expires are mutually exclusive");
  }
  if (!opts.perpetual && !opts.expires) {
    // No default. A perpetual licence is a commercial decision, not a fallback
    // for a forgotten flag.
    throw new IssueError("specify --expires YYYY-MM-DD or --perpetual");
  }
  let expires = null;
  if (opts.expires) {
    expires = String(opts.expires);
    if (!DATE_RE.test(expires)) throw new IssueError("--expires must be YYYY-MM-DD");
    const t = Date.parse(expires);
    if (Number.isNaN(t)) throw new IssueError(`--expires ${expires} is not a real date`);
    // A date-only expiry is parsed by the verifier as UTC midnight, deliberately:
    // the same license file must expire at the same instant everywhere, or a
    // customer in UTC+13 loses a day they paid for and opens a ticket about it.
    if (t < Date.now() && !opts.allowPastExpiry) {
      throw new IssueError(
        `--expires ${expires} is already in the past; this license would be dead on ` +
        "arrival. Pass --allow-past-expiry only if you are deliberately producing " +
        "an expired license (e.g. for tests).");
    }
  }

  const features = opts.features
    ? String(opts.features).split(",").map((s) => s.trim()).filter(Boolean)
    : [...(EDITION_FEATURES[edition] ?? [])];
  const unknown = features.filter((f) => !KNOWN_FEATURES.includes(f));
  if (unknown.length && !opts.allowUnknownFeature) {
    throw new IssueError(
      `unknown feature(s): ${unknown.join(", ")}. The CLI gates on exact ids, so a ` +
      "typo ships a signed license that unlocks nothing. Check the list in " +
      "packages/cli/src/license.ts, or pass --allow-unknown-feature if you are " +
      "issuing ahead of a CLI release.");
  }
  const deduped = [...new Set(features)];

  // Field order here IS the signed byte order. Do not reorder, do not spread.
  return {
    licensee,
    edition,
    expires,
    features: deduped,
    issued: opts.issued ? String(opts.issued) : isoDay(),
  };
}

/** Sign a license and return the exact file text, self-checked.
 *
 * Exported so server.mjs signs through the same code path — two copies of the
 * "which bytes get signed" decision is exactly how one of them drifts.
 */
export function issueLicense(opts, privateKeyPem) {
  const license = buildLicense(opts);

  // The message. Everything downstream must reproduce this byte-for-byte.
  const message = Buffer.from(JSON.stringify(license), "utf8");
  const key = crypto.createPrivateKey(privateKeyPem);
  if (key.asymmetricKeyType !== "ed25519") {
    throw new IssueError(`signing key is ${key.asymmetricKeyType}, expected ed25519`);
  }
  // `null` algorithm: Ed25519 is PureEdDSA — it hashes the message internally
  // with SHA-512 as part of the scheme. Naming an external digest is not
  // "extra hashing", it is an error (OpenSSL rejects it outright).
  const signature = crypto.sign(null, message, key);

  const blob = { license, signature: signature.toString("base64") };
  const text = JSON.stringify(blob, null, 2) + "\n";

  // Self-check against the file we are about to write, not against the objects
  // in memory: this is the only step that catches a serialisation change (key
  // reordering, a stray field, an encoding surprise) before it reaches a
  // customer. Verify with the derived PUBLIC key, so we are exercising the same
  // path the CLI will.
  const publicKeyPem = crypto.createPublicKey(key).export({ type: "spki", format: "pem" });
  const roundTripped = JSON.parse(text);
  const reMessage = Buffer.from(JSON.stringify(roundTripped.license), "utf8");
  if (!reMessage.equals(message)) {
    throw new IssueError(
      "self-check failed: re-serialising the parsed license does not reproduce the " +
      "signed bytes. Refusing to emit a license that cannot verify.");
  }
  if (!crypto.verify(null, reMessage, publicKeyPem,
                     Buffer.from(roundTripped.signature, "base64"))) {
    throw new IssueError(
      "self-check failed: signature does not verify against its own public key. " +
      "Refusing to emit a license that cannot verify.");
  }

  const der = crypto.createPublicKey(key).export({ type: "spki", format: "der" });
  return {
    text,
    license,
    signature: blob.signature,
    publicKeyPem,
    // Fingerprints for the ledger and for operator sanity-checks. Both are
    // derived from public material; neither leaks anything about the key.
    signatureFingerprint: crypto.createHash("sha256").update(signature).digest("hex").slice(0, 16),
    keyFingerprint: crypto.createHash("sha256").update(der).digest("hex").slice(0, 16),
  };
}

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

function main(argv) {
  const f = parseArgs(argv);
  const keyPath = f.key ?? process.env.RESEARCHFORGE_LICENSE_KEY;
  if (!keyPath || keyPath === true) {
    console.error("usage: issue.mjs --licensee NAME --edition community|team|site");
    console.error("       (--expires YYYY-MM-DD | --perpetual) [--features a,b,c]");
    console.error("       --key <private-key-path> [--out FILE]");
    return 2;
  }
  let privateKeyPem;
  try {
    privateKeyPem = fs.readFileSync(path.resolve(String(keyPath)), "utf8");
  } catch (e) {
    // Report the path, never the contents — a key echoed into a terminal ends up
    // in shell history, scrollback and CI logs.
    console.error(`cannot read signing key at ${keyPath}: ${e.code ?? "error"}`);
    return 4;
  }

  let out;
  try {
    out = issueLicense({
      licensee: f.licensee, edition: f.edition, expires: f.expires === true ? undefined : f.expires,
      perpetual: Boolean(f.perpetual), features: f.features === true ? undefined : f.features,
      issued: f.issued === true ? undefined : f.issued,
      allowPastExpiry: Boolean(f["allow-past-expiry"]),
      allowUnknownFeature: Boolean(f["allow-unknown-feature"]),
    }, privateKeyPem);
  } catch (e) {
    console.error(e instanceof IssueError ? e.message : `failed to issue: ${e.message}`);
    return 5;
  }

  if (f.out && f.out !== true) {
    const p = path.resolve(String(f.out));
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, out.text);
    console.error(`license written to ${p}`);
  } else {
    process.stdout.write(out.text);
  }
  console.error(`  licensee    ${out.license.licensee}`);
  console.error(`  edition     ${out.license.edition}`);
  console.error(`  expires     ${out.license.expires ?? "perpetual"}`);
  console.error(`  features    ${out.license.features.join(", ")}`);
  console.error(`  signature   ${out.signatureFingerprint}`);
  console.error(`  signed by   ${out.keyFingerprint}  (public key fingerprint)`);
  console.error("  self-check  verified against the public key before writing");
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(main(process.argv.slice(2)));
}
