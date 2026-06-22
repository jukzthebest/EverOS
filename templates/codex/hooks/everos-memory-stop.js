#!/usr/bin/env node
// Codex Stop hook: asynchronously import stable Codex sessions into EverOS.

const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const BASE_URL = process.env.EVEROS_MEMORY_BASE_URL || "http://127.0.0.1:8000";
const REPO =
  process.env.EVEROS_REPO ||
  path.join(os.homedir(), "Documents/daily/everos-codex-oauth-poc");
const SESSIONS_DIR = path.join(os.homedir(), ".codex", "sessions");
const LOG = path.join(os.homedir(), ".codex", "log", "everos-memory-hook.log");
const LOCK = path.join(os.tmpdir(), "everos-memory-import.lock");

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
  checkApi((ready) => {
    if (!ready) process.exit(0);
    spawnImport(currentSessionId);
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
      res.resume();
      callback(res.statusCode >= 200 && res.statusCode < 300);
    }
  );
  req.on("error", () => callback(false));
  req.on("timeout", () => {
    req.destroy();
    callback(false);
  });
}

function spawnImport(currentSessionId) {
  if (!takeLock()) return;
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  const out = fs.openSync(LOG, "a");
  const err = fs.openSync(LOG, "a");
  const args = [
    "run",
    "everos",
    "import",
    "codex",
    "--sessions-dir",
    SESSIONS_DIR,
    "--base-url",
    BASE_URL,
    "--newest",
    "--limit",
    "1",
    "--chunk-messages",
    "40",
    "--min-age-seconds",
    "180",
    "--skip-existing",
    "--skip-low-value",
    "--continue-on-error",
  ];
  if (currentSessionId) args.push("--exclude-session-id", currentSessionId);
  const command = `uv ${args.map(shellQuote).join(" ")}; status=$?; rm -f ${shellQuote(LOCK)}; exit $status`;
  const child = spawn("/bin/sh", ["-c", command], {
    cwd: REPO,
    detached: true,
    stdio: ["ignore", out, err],
    env: {
      ...process.env,
      NO_PROXY: appendNoProxy(process.env.NO_PROXY || process.env.no_proxy || ""),
      no_proxy: appendNoProxy(process.env.NO_PROXY || process.env.no_proxy || ""),
      EVEROS_MEMORY_IMPORT_HOOK: "1",
    },
  });
  child.unref();
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

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}
