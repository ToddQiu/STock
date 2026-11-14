"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");

function ensureConfig(resources, profile) {
  fs.mkdirSync(profile, { recursive: true });
  const config = path.join(profile, "config.json");
  try { fs.copyFileSync(path.join(resources, "default-config.json"), config, fs.constants.COPYFILE_EXCL); }
  catch (error) { if (error.code !== "EEXIST") throw error; }
  return config;
}

async function startBackend(resources, profile) {
  const config = ensureConfig(resources, profile);
  let port = 0;
  const portFile = path.join(profile, "service-port.json");
  try {
    const saved = JSON.parse(fs.readFileSync(portFile, "utf8"));
    if (Number.isInteger(saved) && saved >= 1024 && saved <= 65535) port = saved;
  } catch { /* A first launch or damaged port hint gets a new available port. */ }
  const log = fs.createWriteStream(path.join(profile, "desktop-service.log"), { flags: "w" });
  const child = spawn(path.join(resources, "backend", "STockServer.exe"), ["--config", config, "--port", String(port)], {
    cwd: profile, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, STOCK_RESOURCE_ROOT: resources,
      STOCK_ASSISTANT_HOME: path.join(resources, ".stock-assistant"),
      STOCK_NODE_EXECUTABLE: path.join(resources, "runtime", "node.exe"), PYTHONUTF8: "1" },
  });
  child.stderr.pipe(log);
  child.stdin.on("error", () => {});
  log.on("error", error => console.error("STock service log:", error.message));
  let exited = false, stopPromise;
  const done = new Promise(resolve => {
    child.once("close", code => { exited = true; resolve(code); });
    child.once("error", () => { exited = true; resolve(1); });
  });
  const stop = () => stopPromise ||= (async () => {
    if (!exited) child.stdin.end();
    const timer = setTimeout(() => {
      if (!exited) {
        // Only this launcher's child tree; never another STock or Python process.
        const killer = spawn(path.join(process.env.SystemRoot || "C:\\Windows", "System32", "taskkill.exe"),
          ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
        killer.on("error", () => child.kill());
      }
    }, 45000);
    await done;
    clearTimeout(timer);
    log.end();
  })();
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Local service startup timed out")), 90000);
      const lines = createInterface({ input: child.stdout });
      lines.on("line", line => {
        log.write(line + "\n");
        try {
          const message = JSON.parse(line);
          if (message.type === "ready" && Number.isInteger(message.port) && message.port > 0 && message.port <= 65535) {
            // Keeping the origin stable preserves browser preferences and startup snapshots.
            fs.writeFileSync(portFile, JSON.stringify(message.port));
            clearTimeout(timer);
            resolve(`http://127.0.0.1:${message.port}/`);
          }
        } catch { /* Non-protocol diagnostic output stays in the log. */ }
      });
      child.once("error", error => { clearTimeout(timer); reject(error); });
      done.then(code => { clearTimeout(timer); reject(new Error(`Local service stopped (${code})`)); });
    });
    return { url, stop };
  } catch (error) {
    await stop();
    throw error;
  }
}

module.exports = { ensureConfig, startBackend };
