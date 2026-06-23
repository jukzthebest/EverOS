#!/usr/bin/env node
// Codex Stop hook: asynchronously import Codex session chunks into EverOS.

const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const BASE_URL = process.env.EVEROS_MEMORY_BASE_URL || "http://127.0.0.1:8000";
const HELPER =
  process.env.EVEROS_MEMORY_HELPER ||
  path.join(os.homedir(), ".local", "bin", "everos-memory");
const SESSIONS_DIR = path.join(os.homedir(), ".codex", "sessions");
const LOG = path.join(os.homedir(), ".codex", "log", "everos-memory-hook.log");
const LOCK = path.join(os.tmpdir(), "everos-memory-import.lock");
const CHUNK_MESSAGES = process.env.EVEROS_MEMORY_CHUNK_MESSAGES || "40";
const STABLE_MIN_AGE_SECONDS =
  process.env.EVEROS_MEMORY_STABLE_MIN_AGE_SECONDS || "180";

let chunks = [];
let done = false;

process.stdin.on("data", (chunk) => chunks.push(chunk));
process.stdin.on("end", run);
setTimeout(run, 350);

function run() {
  if (done) return;
  done = true;
  const payload = readPayload();
  const currentSessionId = payload.session_id || payload.sessionId || "";
  checkApi((ready, health) => {
    if (!ready) process.exit(0);
    spawnImport(currentSessionId, health && health.memory_root);
    process.exit(0);
  });
}

function readPayload() {
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return {};
  }
}

function checkApi(callback) {
  const url = new URL("/api/v1/dashboard/health", BASE_URL);
  const req = http.get(
    {
      hostname: url.hostname,
      port: url.port || 80,
      path: url.pathname,
      timeout: 180,
    },
    (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        let body = {};
        try {
          body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        } catch {}
        callback(res.statusCode >= 200 && res.statusCode < 300, body);
      });
    }
  );
  req.on("error", () => callback(false));
  req.on("timeout", () => {
    req.destroy();
    callback(false);
  });
}

function spawnImport(currentSessionId, detectedMemoryRoot) {
  if (!takeLock()) return;
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  const out = fs.openSync(LOG, "a");
  const err = fs.openSync(LOG, "a");
  const repo = resolveRepo();
  if (!repo) {
    fs.appendFileSync(LOG, `[${new Date().toISOString()}] EverOS repo not found\n`);
    cleanupLock();
    return;
  }
  const memoryRoot =
    process.env.EVEROS_MEMORY_ROOT ||
    detectedMemoryRoot ||
    path.join(os.homedir(), ".everos", "memory");
  const currentSessionFile = findCurrentSessionFile(currentSessionId);
  const commands = [];
  if (currentSessionFile) {
    const currentArgs = [
      "run",
      "everos",
      "import",
      "codex-structured",
      "--session-path",
      currentSessionFile,
      "--output-root",
      memoryRoot,
      "--min-age-seconds",
      "0",
      "--chunk-messages",
      CHUNK_MESSAGES,
      "--complete-chunks-only",
      "--skip-existing",
    ];
    commands.push(`uv ${currentArgs.map(shellQuote).join(" ")}`);
  }
  const stableArgs = [
    "run",
    "everos",
    "import",
    "codex-structured",
    "--sessions-dir",
    SESSIONS_DIR,
    "--output-root",
    memoryRoot,
    "--newest",
    "--limit",
    "1",
    "--min-age-seconds",
    STABLE_MIN_AGE_SECONDS,
    "--chunk-messages",
    CHUNK_MESSAGES,
    "--skip-existing",
  ];
  if (currentSessionId) stableArgs.push("--exclude-session-id", currentSessionId);
  commands.push(`uv ${stableArgs.map(shellQuote).join(" ")}`);
  const importCmd = commands.join("; status=$?; if [ \"$status\" -ne 0 ]; then exit $status; fi; ");
  const cascadeCmd = "uv run everos cascade sync";
  const command = `${importCmd}; status=$?; if [ "$status" -eq 0 ]; then ${cascadeCmd}; status=$?; fi; rm -f ${shellQuote(LOCK)}; exit $status`;
  const child = spawn("/bin/sh", ["-c", command], {
    cwd: repo,
    detached: true,
    stdio: ["ignore", out, err],
    env: {
      ...process.env,
      NO_PROXY: appendNoProxy(process.env.NO_PROXY || process.env.no_proxy || ""),
      no_proxy: appendNoProxy(process.env.NO_PROXY || process.env.no_proxy || ""),
      EVEROS_MEMORY__ROOT: memoryRoot,
      EVEROS_MEMORY_IMPORT_HOOK: "1",
    },
  });
  child.unref();
}

function findCurrentSessionFile(sessionId) {
  const direct = directSessionPathFromEnv();
  if (direct) return direct;
  if (!sessionId) return "";
  const stack = [SESSIONS_DIR];
  let newest = "";
  let newestMtime = 0;
  while (stack.length) {
    const dir = stack.pop();
    let entries = [];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        stack.push(full);
        continue;
      }
      if (!entry.isFile() || !entry.name.endsWith(".jsonl")) continue;
      if (!entry.name.includes(sessionId)) continue;
      try {
        const stat = fs.statSync(full);
        if (stat.mtimeMs > newestMtime) {
          newest = full;
          newestMtime = stat.mtimeMs;
        }
      } catch {}
    }
  }
  return newest;
}

function directSessionPathFromEnv() {
  const value = process.env.EVEROS_MEMORY_SESSION_PATH || "";
  if (!value) return "";
  try {
    return fs.statSync(value).isFile() ? value : "";
  } catch {
    return "";
  }
}

function resolveRepo() {
  if (process.env.EVEROS_REPO && isRepo(process.env.EVEROS_REPO)) {
    return process.env.EVEROS_REPO;
  }
  try {
    return findRepoUpwards(path.dirname(fs.realpathSync(HELPER))) || "";
  } catch {
    return "";
  }
}

function findRepoUpwards(start) {
  let current = start;
  while (current && current !== path.dirname(current)) {
    if (isRepo(current)) return current;
    current = path.dirname(current);
  }
  return "";
}

function isRepo(candidate) {
  return (
    fs.existsSync(path.join(candidate, "pyproject.toml")) &&
    fs.existsSync(path.join(candidate, "src", "everos"))
  );
}

function appendNoProxy(value) {
  const required = ["127.0.0.1", "localhost"];
  const parts = String(value || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  for (const item of required) {
    if (!parts.includes(item)) parts.push(item);
  }
  return parts.join(",");
}

function takeLock() {
  try {
    const fd = fs.openSync(LOCK, "wx");
    fs.writeFileSync(fd, `${process.pid}\n${new Date().toISOString()}\n`);
    fs.closeSync(fd);
    return true;
  } catch {
    try {
      const stat = fs.statSync(LOCK);
      if (Date.now() - stat.mtimeMs > 60 * 60 * 1000) {
        fs.unlinkSync(LOCK);
        return takeLock();
      }
    } catch {}
    return false;
  }
}

function cleanupLock() {
  try {
    fs.unlinkSync(LOCK);
  } catch {}
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}
