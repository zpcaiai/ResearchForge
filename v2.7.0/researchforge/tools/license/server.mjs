#!/usr/bin/env node
/** researchforge — license issuing service (ISSUING SIDE ONLY).
 *
 * ##########################################################################
 * #  THIS IS THE SELLER'S SIDE. IT MUST RUN ON A MACHINE THE CUSTOMER      #
 * #  NEVER TOUCHES, AND IT MUST NEVER BE PART OF THE SHIPPED PRODUCT.      #
 * ##########################################################################
 *
 * It holds the Ed25519 private signing key. Anyone who can reach /issue with a
 * valid admin token, or who can read the key file, can mint themselves a
 * perpetual site license — and since the CLI verifies offline, there is no
 * revocation path to take it back. Deploy it on internal infrastructure,
 * loopback or a private network by default, behind whatever fronting you
 * already trust for admin surfaces.
 *
 * The customer's CLI never talks to this service. Verification is offline by
 * design: research environments treat outbound network access as a compliance
 * question their security office has to sign off on, and a six-hour run must not
 * fail at hour five because this process was being restarted. This service
 * exists only so that *we* can sign a license file that then travels to the
 * customer by ordinary means (email, download, config management).
 *
 *   POST /issue   { licensee, edition, expires|perpetual, features? }
 *                 Authorization: Bearer <RESEARCHFORGE_ADMIN_TOKEN>
 *   GET  /health  liveness + which key is loaded (fingerprint only)
 *
 * Environment:
 *   RESEARCHFORGE_ADMIN_TOKEN   required; refuses to start without it
 *   RESEARCHFORGE_LICENSE_KEY   required; path to the Ed25519 private key
 *   RESEARCHFORGE_LEDGER        append-only jsonl issuance log
 *   RESEARCHFORGE_ISSUER_HOST   default 127.0.0.1
 *   RESEARCHFORGE_ISSUER_PORT   default 8787
 */
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { IssueError, issueLicense } from "./issue.mjs";

const HOST = process.env.RESEARCHFORGE_ISSUER_HOST ?? "127.0.0.1";
const PORT = Number(process.env.RESEARCHFORGE_ISSUER_PORT ?? 8787);
const LEDGER = process.env.RESEARCHFORGE_LEDGER ??
               path.resolve("./researchforge-issuance.jsonl");

/** A POST body larger than this is not a license request. Reading an unbounded
 * request body into memory is a free memory-exhaustion DoS from an unauthenticated
 * caller — the cap is enforced before we even look at the token. */
const MAX_BODY_BYTES = 16 * 1024;

/** Per-IP fixed window. This is not a fairness knob; it is what stops an admin
 * token from being brute-forced (a 32-char token at 30 guesses/minute is
 * unreachable) and what stops a compromised token from being used to bulk-mint
 * licenses faster than a human notices the ledger growing. */
const RATE_WINDOW_MS = 60_000;
const RATE_MAX_PER_IP = 30;
const RATE_MAX_GLOBAL = 120;

const MIN_TOKEN_LENGTH = 24;

function fail(reason, code = 1) {
  console.error(`refusing to start: ${reason}`);
  process.exit(code);
}

/** Constant-time token comparison.
 *
 * A plain `===` on strings short-circuits at the first differing byte, which
 * leaks the length of the shared prefix to anyone who can time the response —
 * enough to recover the token byte by byte over enough requests. Hashing first
 * makes both inputs the same length so timingSafeEqual cannot throw on a
 * length mismatch (which would itself be a length oracle).
 */
function tokenMatches(presented, expected) {
  const a = crypto.createHash("sha256").update(String(presented)).digest();
  const b = crypto.createHash("sha256").update(String(expected)).digest();
  return crypto.timingSafeEqual(a, b);
}

class RateLimiter {
  #windows = new Map();
  #globalCount = 0;
  #globalReset = Date.now() + RATE_WINDOW_MS;

  check(ip) {
    const now = Date.now();
    if (now > this.#globalReset) { this.#globalCount = 0; this.#globalReset = now + RATE_WINDOW_MS; }
    if (++this.#globalCount > RATE_MAX_GLOBAL) {
      return { ok: false, retryAfter: Math.ceil((this.#globalReset - now) / 1000) };
    }
    let w = this.#windows.get(ip);
    if (!w || now > w.reset) { w = { count: 0, reset: now + RATE_WINDOW_MS }; this.#windows.set(ip, w); }
    // Bound the map itself: an attacker cycling source addresses would otherwise
    // turn the rate limiter into the memory leak it was meant to prevent.
    if (this.#windows.size > 10_000) {
      for (const [k, v] of this.#windows) if (now > v.reset) this.#windows.delete(k);
    }
    if (++w.count > RATE_MAX_PER_IP) {
      return { ok: false, retryAfter: Math.ceil((w.reset - now) / 1000) };
    }
    return { ok: true };
  }
}

/** Append-only issuance ledger.
 *
 * Every signature this key ever produced must be reconstructible from this file:
 * it is the only way to answer "did we issue this, and to whom" when a license
 * turns up somewhere it should not be, and the only way to know the blast radius
 * if the key leaks. It records the license terms and a fingerprint of the
 * signature — never the private key, never the signature itself in full, and
 * never the admin token.
 */
function appendLedger(entry) {
  fs.mkdirSync(path.dirname(LEDGER), { recursive: true });
  // 'a' — every write is an append; nothing here ever rewrites an earlier line.
  fs.appendFileSync(LEDGER, JSON.stringify(entry) + "\n", { mode: 0o600 });
}

function send(res, status, body, headers = {}) {
  const text = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json", ...headers });
  res.end(text);
}

class BodyTooLarge extends Error {}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let over = false;
    const chunks = [];
    req.on("data", (c) => {
      if (over) return;
      size += c.length;
      if (size > MAX_BODY_BYTES) {
        // Stop accumulating immediately — the point of the cap is that we never
        // hold the oversized body, not that we notice afterwards. The socket is
        // torn down by the handler once the 413 has actually been written.
        over = true;
        req.pause();
        reject(new BodyTooLarge());
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => { if (!over) resolve(Buffer.concat(chunks).toString("utf8")); });
    req.on("error", reject);
  });
}

export function createServer({ privateKeyPem, adminToken, keyFingerprint }) {
  const limiter = new RateLimiter();
  const startedAt = Date.now();

  return http.createServer(async (req, res) => {
    const ip = req.socket.remoteAddress ?? "unknown";
    const url = new URL(req.url ?? "/", "http://localhost");

    if (req.method === "GET" && url.pathname === "/health") {
      // Deliberately unauthenticated and deliberately boring: a liveness probe
      // must not need a credential, so it must not expose anything. The key
      // fingerprint is derived from the PUBLIC key and is safe to publish; it
      // lets an operator confirm which signing key this instance loaded.
      send(res, 200, { status: "ok", keyFingerprint, uptimeSeconds: Math.floor((Date.now() - startedAt) / 1000) });
      return;
    }

    if (url.pathname !== "/issue") { send(res, 404, { error: "not found" }); return; }
    if (req.method !== "POST") { send(res, 405, { error: "method not allowed" }, { allow: "POST" }); return; }

    const rate = limiter.check(ip);
    if (!rate.ok) {
      send(res, 429, { error: "rate limited" }, { "retry-after": String(rate.retryAfter) });
      return;
    }

    const auth = req.headers.authorization ?? "";
    const presented = auth.startsWith("Bearer ") ? auth.slice(7) : "";
    if (!presented || !tokenMatches(presented, adminToken)) {
      // No detail about why. "Token too short" or "unknown token prefix" would
      // hand an attacker a free oracle; the log line stays on our side.
      console.warn(`[issue] rejected unauthenticated request from ${ip}`);
      send(res, 401, { error: "unauthorized" });
      return;
    }

    let body;
    try {
      body = JSON.parse(await readBody(req) || "{}");
    } catch (e) {
      if (e instanceof BodyTooLarge) {
        send(res, 413, { error: `body exceeds ${MAX_BODY_BYTES} bytes` }, { connection: "close" });
        req.destroy();
        return;
      }
      send(res, 400, { error: "body must be JSON" });
      return;
    }

    let issued;
    try {
      issued = issueLicense({
        licensee: body.licensee,
        edition: body.edition,
        expires: body.expires,
        perpetual: Boolean(body.perpetual),
        features: Array.isArray(body.features) ? body.features.join(",") : body.features,
        allowPastExpiry: Boolean(body.allowPastExpiry),
        allowUnknownFeature: Boolean(body.allowUnknownFeature),
      }, privateKeyPem);
    } catch (e) {
      if (e instanceof IssueError) { send(res, 400, { error: e.message }); return; }
      // Unexpected failures are logged as a bare message and returned as a
      // generic error: a stack trace or an echoed exception is a plausible way
      // for key material or file paths to escape into a customer-visible reply.
      console.error(`[issue] signing failed: ${e.message}`);
      send(res, 500, { error: "issuance failed" });
      return;
    }

    // Ledger BEFORE responding. If the process dies between the two, we have a
    // record of a license that may not have been delivered — recoverable. The
    // other order loses the record of a license that is already in the wild.
    try {
      appendLedger({
        ts: new Date().toISOString(),
        licensee: issued.license.licensee,
        edition: issued.license.edition,
        expires: issued.license.expires,
        features: issued.license.features,
        signatureFingerprint: issued.signatureFingerprint,
        keyFingerprint: issued.keyFingerprint,
        requestedBy: ip,
      });
    } catch (e) {
      console.error(`[issue] ledger write failed: ${e.message}`);
      send(res, 500, { error: "issuance not recorded; refusing to return a license" });
      return;
    }

    console.log(`[issue] ${issued.license.edition} for ${JSON.stringify(issued.license.licensee)} ` +
                `sig=${issued.signatureFingerprint}`);
    // The response carries exactly what the customer's license.json needs and
    // nothing about the key that signed it.
    send(res, 200, JSON.parse(issued.text));
  });
}

function main() {
  const adminToken = process.env.RESEARCHFORGE_ADMIN_TOKEN;
  if (!adminToken) {
    fail("RESEARCHFORGE_ADMIN_TOKEN is not set. This service signs licenses with a " +
         "key that cannot be revoked once used; it will not run unauthenticated, " +
         "not even on loopback.");
  }
  if (adminToken.length < MIN_TOKEN_LENGTH) {
    fail(`RESEARCHFORGE_ADMIN_TOKEN is shorter than ${MIN_TOKEN_LENGTH} characters. ` +
         "Use `openssl rand -hex 32`; a guessable token is the same as no token.");
  }
  const keyPath = process.env.RESEARCHFORGE_LICENSE_KEY;
  if (!keyPath) fail("RESEARCHFORGE_LICENSE_KEY (path to the Ed25519 signing key) is not set.");

  let privateKeyPem;
  try {
    privateKeyPem = fs.readFileSync(path.resolve(keyPath), "utf8");
  } catch (e) {
    fail(`cannot read signing key at ${keyPath}: ${e.code ?? "error"}`);
  }
  let keyFingerprint;
  try {
    const key = crypto.createPrivateKey(privateKeyPem);
    if (key.asymmetricKeyType !== "ed25519") {
      fail(`signing key is ${key.asymmetricKeyType}, expected ed25519`);
    }
    const der = crypto.createPublicKey(key).export({ type: "spki", format: "der" });
    keyFingerprint = crypto.createHash("sha256").update(der).digest("hex").slice(0, 16);
  } catch (e) {
    fail(`signing key is unusable: ${e.message}`);
  }

  // Warn loudly if this is bound anywhere but loopback. Nothing stops a
  // deliberate deployment behind a trusted proxy; an accidental one should be
  // visible in the first line of the log.
  if (HOST !== "127.0.0.1" && HOST !== "::1" && HOST !== "localhost") {
    console.warn(`[issue] WARNING: binding ${HOST} — this service holds the license ` +
                 "signing key. Do not expose it to any network a customer can reach.");
  }

  createServer({ privateKeyPem, adminToken, keyFingerprint }).listen(PORT, HOST, () => {
    console.log(`[issue] listening on ${HOST}:${PORT}  key=${keyFingerprint}  ledger=${LEDGER}`);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
