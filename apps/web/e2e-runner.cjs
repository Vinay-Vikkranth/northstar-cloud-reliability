const { spawn } = require("node:child_process");
const path = require("node:path");

const child = spawn(
  process.execPath,
  [path.join(__dirname, "node_modules", "@playwright", "test", "cli.js"), "test", "--config", "playwright.config.ts"],
  {
    cwd: __dirname,
    stdio: "inherit",
    env: { ...process.env, NODE_PATH: path.join(__dirname, "node_modules") },
  },
);
child.on("exit", (code) => process.exit(code ?? 1));
