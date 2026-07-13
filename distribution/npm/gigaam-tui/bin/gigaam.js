#!/usr/bin/env node
"use strict";

const { execFileSync, spawnSync } = require("child_process");
const { existsSync, mkdirSync } = require("fs");
const { homedir, platform } = require("os");
const { join } = require("path");

const root = process.env.GIGAAM_HOME || join(homedir(), ".local", "share", "gigaam-tui");
const repo = join(root, "repo");
const binary = join(repo, "tui", "target", "release", platform() === "win32" ? "gigaam-tui.exe" : "gigaam-tui");
const isWindows = platform() === "win32";
const python = join(repo, ".venv", isWindows ? "Scripts/python.exe" : "bin/python");
const systemPython = isWindows ? "python" : "python3";

function run(command, args, options = {}) {
  execFileSync(command, args, { stdio: "inherit", ...options });
}

function bootstrap() {
  console.log("Installing GigaAM TUI from source…");
  mkdirSync(root, { recursive: true });
  if (!existsSync(join(repo, ".git"))) {
    run("git", ["clone", "--depth", "1", "https://github.com/dubr1k/GigaAMGUI.git", repo]);
  } else {
    run("git", ["-C", repo, "pull", "--ff-only"]);
  }
  run("cargo", ["build", "--release", "--manifest-path", join(repo, "tui", "Cargo.toml")]);
  run(systemPython, ["-m", "venv", join(repo, ".venv")]);
  run(python, ["-m", "pip", "install", "--upgrade", "pip"]);
  run(python, ["-m", "pip", "install", "-r", join(repo, "requirements-tui.txt")]);
}

if (!existsSync(binary) || !existsSync(python)) bootstrap();
const result = spawnSync(binary, process.argv.slice(2), {
  stdio: "inherit",
  env: { ...process.env, GIGAAM_PROJECT_ROOT: repo, GIGAAM_PYTHON: python },
});
process.exit(result.status ?? 1);
