/** The language boundary.
 *
 * One subprocess per skill invocation. Skills run untrusted generated code and
 * can hang, exhaust memory, or die; process isolation turns "the skill crashed"
 * into a normal observable outcome rather than a corrupted shared runtime, and
 * makes every invocation reproducible from its request JSON alone.
 */
import { spawn } from "node:child_process";
import path from "node:path";
import type { SkillName } from "@researchforge/contracts";

export interface RunRequest {
  skill: SkillName;
  project: string;
  runId: string;
  mode: "guided" | "auto" | "analysis-only";
  model: string;
  offline: boolean;
  config?: Record<string, unknown>;
  quota?: Record<string, { max_calls?: number; max_usd?: number }>;
  fixtures?: string;
}

export type RunResponse =
  | { ok: true; skill: string; produced: string[]; warnings: string[]; synthetic: boolean;
      needs_human: unknown; next_state: string | null; detail: Record<string, unknown>;
      quota: unknown[] }
  | { ok: false; needs_human: true; prompt: string; options_artifact: string }
  | { ok: false; error: { kind: string; message: string; [k: string]: unknown } };

export class PythonBridge {
  constructor(
    private readonly repoRoot: string,
    private readonly python = process.env.RESEARCHFORGE_PYTHON ?? "python3",
  ) {}

  async run(req: RunRequest, timeoutMs = 4 * 3600 * 1000): Promise<RunResponse> {
    const args = [
      "-m", "researchforge.runner", "run",
      "--skill", req.skill,
      "--project", req.project,
      "--run-id", req.runId,
      "--mode", req.mode,
      "--model", req.model,
      "--schemas", path.join(this.repoRoot, "schemas"),
    ];
    if (req.offline) args.push("--offline");
    if (req.fixtures) args.push("--fixtures", req.fixtures);

    return await new Promise<RunResponse>((resolve) => {
      const child = spawn(this.python, args, {
        cwd: this.repoRoot,
        env: { ...process.env, PYTHONPATH: path.join(this.repoRoot, "python") },
        stdio: ["pipe", "pipe", "pipe"],
      });
      let out = "", err = "";
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
        resolve({ ok: false, error: { kind: "timeout",
          message: `skill '${req.skill}' exceeded ${Math.round(timeoutMs / 1000)}s and was killed. ` +
                   `The time box is enforced by the runtime, not by the skill's own judgment.` } });
      }, timeoutMs);

      child.stdout.on("data", (d) => (out += d));
      child.stderr.on("data", (d) => (err += d));
      child.on("error", (e) => {
        clearTimeout(timer);
        resolve({ ok: false, error: { kind: "spawn_failed", message: String(e) } });
      });
      child.on("close", () => {
        clearTimeout(timer);
        const line = out.trim().split("\n").filter(Boolean).pop();
        if (!line) {
          resolve({ ok: false, error: { kind: "no_output",
            message: `skill '${req.skill}' produced no JSON on stdout`, stderr: err.slice(-2000) } });
          return;
        }
        try {
          resolve(JSON.parse(line) as RunResponse);
        } catch {
          resolve({ ok: false, error: { kind: "bad_output",
            message: `skill '${req.skill}' returned unparseable output`,
            raw: line.slice(0, 1000), stderr: err.slice(-2000) } });
        }
      });
      child.stdin.end(JSON.stringify({
        project: req.project, config: req.config ?? {}, quota: req.quota ?? {},
      }));
    });
  }

  async contractDigest(): Promise<string> {
    const r = await new Promise<string>((resolve) => {
      const c = spawn(this.python, ["-m", "researchforge.runner", "contract"], {
        cwd: this.repoRoot,
        env: { ...process.env, PYTHONPATH: path.join(this.repoRoot, "python") },
      });
      let o = ""; c.stdout.on("data", (d) => (o += d)); c.on("close", () => resolve(o));
    });
    return (JSON.parse(r.trim()) as { digest: string }).digest;
  }
}
