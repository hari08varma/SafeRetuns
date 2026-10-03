import { spawn } from "node:child_process";
import { backendEnv } from "../playwright.config";

/** The worker (outbox relay + timers) has no port, so it is started here and stopped after. */
export default async function globalSetup() {
  const worker = spawn("uv", ["run", "--directory", "../../services", "python", "-m", "returns_agent.workers.run"], {
    env: { ...process.env, ...backendEnv },
    stdio: "inherit",
  });
  return async () => {
    worker.kill("SIGTERM");
  };
}
