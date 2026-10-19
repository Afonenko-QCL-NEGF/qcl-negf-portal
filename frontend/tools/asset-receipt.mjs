/** Derived asset receipt. No dependency install or compiler work at import time. */
import {createHash, randomBytes} from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import {spawn} from "node:child_process";
import {fileURLToPath, pathToFileURL} from "node:url";

const STAMP = ".qcl-negf-frontend-v1.json";
const SCRIPT = "node tools/asset-receipt.mjs build";
const COMMANDS = [
  ["node", "node_modules/typescript/bin/tsc", "--noEmit"],
  ["node", "node_modules/vite/bin/vite.js", "build", "--mode", "production"],
];
const REQUIRED = ["package.json", "package-lock.json", "index.html", "tsconfig.json",
  "vite.config.ts", "tools/asset-receipt.mjs", "src/main.tsx"];
const FIELDS = ["format", "mode", "recipe", "recipe_sha256", "toolchain", "inputs",
  "inputs_sha256", "outputs", "outputs_sha256"];
const SEMVER = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;
const canonical = value => Buffer.from(JSON.stringify(value), "utf8");
const sha = bytes => createHash("sha256").update(bytes).digest("hex");
const equal = (left, right) => canonical(left).equals(canonical(right));

function invalid(reason) {
  return new Error(`QCL_FRONTEND_RECEIPT_INVALID:${reason}; run npm --prefix frontend run build with prepared locked dependencies`);
}
function fail(reason) {throw invalid(reason);}
function keys(value, expected) {
  if (value === null || Array.isArray(value) || typeof value !== "object"
    || !equal(Object.keys(value), expected)) fail("schema");
}
function safePath(value, reason = "unsafe-path") {
  if (typeof value !== "string" || !value || !value.isWellFormed()
    || /[\\\x00-\x1f\x7f]/.test(value)
    || value.split("/").some(part => ["", ".", ".."].includes(part))) fail(reason);
}
function validVersion(value) {
  return typeof value === "string" && !/[\r\n]/.test(value) && SEMVER.test(value);
}
function validHash(value) {
  if (typeof value !== "string" || value.length !== 64 || !/^[0-9a-f]{64}$/.test(value)) fail("schema");
}
function rows(value) {
  if (!Array.isArray(value) || !value.length) fail("schema");
  let previous;
  for (const row of value) {
    keys(row, ["path", "bytes", "sha256"]);
    safePath(row.path, "schema");
    if (!Number.isSafeInteger(row.bytes) || row.bytes < 0) fail("schema");
    validHash(row.sha256);
    const encoded = Buffer.from(row.path);
    if (previous && Buffer.compare(previous, encoded) >= 0) fail("schema");
    previous = encoded;
  }
}
async function inspect(location, device, kind) {
  const entry = await fs.lstat(location);
  if (entry.isSymbolicLink() || (!entry.isDirectory() && !entry.isFile())
    || (device !== undefined && entry.dev !== device)
    || (kind === "directory" && !entry.isDirectory())
    || (kind === "file" && !entry.isFile())) fail("unsafe-path");
  return entry;
}
async function optionalInspect(location, device, kind) {
  try {return await inspect(location, device, kind);} catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}
async function readJson(location, reason = "schema") {
  try {
    const bytes = await fs.readFile(location);
    const decoded = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
    return JSON.parse(decoded);
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError) fail(reason);
    throw error;
  }
}
function recipe(mode) {
  return {id: "qcl-negf.frontend-build.v1", commands: COMMANDS,
    output_root: mode === "source-static" ? "../src/qcl_negf_api/static" : "dist",
    node_env: "production", output_reset: "clean-selected-root-v1"};
}
async function project(frontendRoot) {
  const frontend = path.resolve(frontendRoot);
  const device = (await inspect(frontend, undefined, "directory")).dev;
  const packageInfo = await readJson(path.join(frontend, "package.json"));
  if (packageInfo?.name !== "qcl-negf-portal" || packageInfo.private !== true
    || !packageInfo.scripts || typeof packageInfo.scripts !== "object"
    || Array.isArray(packageInfo.scripts)
    || packageInfo.scripts.build !== SCRIPT || "prebuild" in packageInfo.scripts
    || "postbuild" in packageInfo.scripts) fail("recipe");
  for (const relative of REQUIRED) {
    let location = frontend;
    const segments = relative.split("/");
    for (let index = 0; index < segments.length; index++) {
      location = path.join(location, segments[index]);
      await inspect(location, device, index === segments.length - 1 ? "file" : "directory");
    }
  }
  const config = await fs.readFile(path.join(frontend, "vite.config.ts"));
  const token = Buffer.from("../src/qcl_negf_api/static");
  const sourceCount = config.toString("latin1").split(token.toString("latin1")).length - 1;
  const distCount = config.toString("latin1").split('outDir: "dist"').length - 1;
  let mode;
  if (sourceCount === 1) mode = "source-static";
  else if (sourceCount === 0 && distCount === 1) mode = "nix-dist";
  else fail("mode");
  let owner = frontend;
  let output;
  if (mode === "source-static") {
    if (path.basename(frontend) !== "frontend") fail("unsafe-path");
    owner = path.dirname(frontend);
    await inspect(owner, device, "directory");
    await inspect(path.join(owner, "pyproject.toml"), device, "file");
    await inspect(path.join(owner, "src"), device, "directory");
    await inspect(path.join(owner, "src/qcl_negf_api"), device, "directory");
    await inspect(path.join(owner, "src/qcl_negf_api/__init__.py"), device, "file");
    output = path.join(owner, "src/qcl_negf_api/static");
  } else output = path.join(frontend, "dist");
  return {frontend, output, mode, owner, device};
}
async function names(directory) {
  const result = [];
  for (const bytes of await fs.readdir(directory, {encoding: "buffer"})) {
    const name = bytes.toString("utf8");
    if (!Buffer.from(name).equals(bytes)) fail("unsafe-path");
    safePath(name);
    if (name === ".git") fail("unsafe-path");
    result.push(name);
  }
  return result;
}
async function inventory(root, inputs = false) {
  const device = (await inspect(root, undefined, "directory")).dev;
  const result = [];
  async function walk(directory, prefix = "") {
    for (const name of await names(directory)) {
      const relative = prefix + name;
      const location = path.join(directory, name);
      const entry = await inspect(location, device);
      if (inputs && !prefix && ["node_modules", "dist"].includes(name) && entry.isDirectory()) continue;
      if (inputs && (name === ".env" || name.startsWith(".env."))) fail("unsafe-path");
      if (entry.isDirectory()) await walk(location, relative + "/");
      else if (inputs || relative !== STAMP) {
        const bytes = await fs.readFile(location);
        if (!Number.isSafeInteger(bytes.length)) fail("schema");
        result.push({path: relative, bytes: bytes.length, sha256: sha(bytes)});
      }
    }
  }
  await walk(root);
  result.sort((left, right) => Buffer.compare(Buffer.from(left.path), Buffer.from(right.path)));
  return result;
}
function payload(outputs) {
  if (!outputs.some(row => row.path === "index.html" && row.bytes > 0)
    || !outputs.some(row => /^assets\/[^/]+\.js$/.test(row.path) && row.bytes > 0)) fail("outputs");
}
async function checkTree(root, device) {
  const entry = await optionalInspect(root, device, "directory");
  if (!entry) return;
  for (const name of await names(root)) {
    const location = path.join(root, name);
    const child = await inspect(location, device);
    if (child.isDirectory()) await checkTree(location, device);
  }
}
function checkEnvironment() {
  if (Object.keys(process.env).some(key => key === "NODE_OPTIONS" || key.startsWith("VITE_"))) {
    fail("inputs");
  }
}
async function dependencyVersions(frontend) {
  const lock = await readJson(path.join(frontend, "package-lock.json"), "toolchain");
  const versions = {};
  for (const name of ["typescript", "vite"]) {
    const metadata = await readJson(path.join(frontend, "node_modules", name, "package.json"), "toolchain");
    const version = metadata?.version;
    if (!validVersion(version)
      || lock?.packages?.[`node_modules/${name}`]?.version !== version) fail("toolchain");
    versions[name] = version;
  }
  return versions;
}
async function toolchain(frontend, runner, context) {
  if (!/^24\.[0-9]+\.[0-9]+$/.test(process.versions.node)) fail("toolchain");
  const result = await runner(["npm", "--version"], context);
  if (result.exitCode !== 0 || typeof result.stdout !== "string") fail("toolchain");
  const npm = result.stdout.endsWith("\n") ? result.stdout.slice(0, -1) : result.stdout;
  if (!validVersion(npm)) fail("toolchain");
  return {node: process.versions.node, npm, ...await dependencyVersions(frontend)};
}
async function defaultRunner(argv, context) {
  return await new Promise((resolve, reject) => {
    const child = spawn(argv[0], argv.slice(1), {
      cwd: context.cwd, env: context.env, shell: false, stdio: ["ignore", "pipe", "inherit"],
    });
    let stdout = "";
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", chunk => {
      if (argv[0] === "npm") stdout += chunk;
      else process.stdout.write(chunk);
    });
    child.once("error", reject);
    child.once("close", (exitCode, signal) => {
      if (signal) reject(new Error("frontend command terminated by signal"));
      else resolve({exitCode, stdout});
    });
  });
}
function commandFailure(exitCode) {
  const error = new Error(`frontend command failed with exit status ${exitCode}`);
  error.exitCode = Number.isInteger(exitCode) && exitCode > 0 && exitCode <= 255 ? exitCode : 1;
  return error;
}
async function safeRemoveStamp(projectInfo, name) {
  const current = await project(projectInfo.frontend);
  if (current.output !== projectInfo.output || current.mode !== projectInfo.mode) fail("unsafe-path");
  await inspect(current.output, current.device, "directory");
  const location = path.join(current.output, name);
  const entry = await optionalInspect(location, current.device, "file");
  if (entry) await fs.unlink(location);
}
function publicError(error) {
  if (String(error.message).startsWith("QCL_FRONTEND_RECEIPT_INVALID:")
    || Number.isInteger(error.exitCode)) return error;
  if (error.code === "ENOENT") return invalid("missing");
  return invalid("io");
}

/** Import-only runner injection; CLI has no environment or argument bypass. */
export async function buildFrontend(frontendRoot, runner = defaultRunner) {
  let projectInfo;
  let temporary;
  let published = false;
  try {
    checkEnvironment();
    projectInfo = await project(frontendRoot);
    // Validate the entire selected tree before deleting even its old stamp.
    await checkTree(projectInfo.output, projectInfo.device);
    // Preflight inputs also refuses .env and unsafe source before destructive work.
    await inventory(projectInfo.frontend, true);
    await fs.rm(projectInfo.output, {recursive: true, force: true});
    await fs.mkdir(projectInfo.output);
    const context = {cwd: projectInfo.frontend, env: Object.freeze({...process.env, NODE_ENV: "production"})};
    const before = await inventory(projectInfo.frontend, true);
    const versions = await toolchain(projectInfo.frontend, runner, context);
    for (const command of COMMANDS) {
      const result = await runner([process.execPath, ...command.slice(1)], context);
      if (result.exitCode !== 0) throw commandFailure(result.exitCode);
    }
    if (!equal(before, await inventory(projectInfo.frontend, true))) fail("inputs");
    if (!equal(versions, await toolchain(projectInfo.frontend, runner, context))) fail("toolchain");
    const afterProject = await project(projectInfo.frontend);
    if (afterProject.mode !== projectInfo.mode || afterProject.output !== projectInfo.output) fail("inputs");
    const outputs = await inventory(projectInfo.output);
    payload(outputs);
    const effectiveRecipe = recipe(projectInfo.mode);
    const value = {format: "qcl-negf.frontend-receipt.v1", mode: projectInfo.mode,
      recipe: effectiveRecipe, recipe_sha256: sha(canonical(effectiveRecipe)), toolchain: versions,
      inputs: before, inputs_sha256: sha(canonical(before)),
      outputs, outputs_sha256: sha(canonical(outputs))};
    if (!equal(before, await inventory(projectInfo.frontend, true))) fail("inputs");
    const candidate = `.qcl-negf-frontend-${randomBytes(12).toString("hex")}.tmp`;
    const handle = await fs.open(path.join(projectInfo.output, candidate), "wx", 0o644);
    temporary = candidate;
    try {await handle.writeFile(Buffer.concat([canonical(value), Buffer.from("\n")])); await handle.sync();}
    finally {await handle.close();}
    await fs.rename(path.join(projectInfo.output, temporary), path.join(projectInfo.output, STAMP));
    temporary = undefined;
    published = true;
    const directory = await fs.open(projectInfo.output, "r");
    try {
      try {await directory.sync();} catch (error) {
        if (!["EINVAL", "ENOTSUP", "EISDIR", "EBADF"].includes(error.code)) throw error;
      }
    } finally {await directory.close();}
    await verifyFrontend(projectInfo.frontend);
  } catch (caught) {
    const error = publicError(caught);
    for (const name of [temporary, published ? STAMP : undefined].filter(Boolean)) {
      try {await safeRemoveStamp(projectInfo, name);} catch {
        error.message += "; receipt cleanup failed";
      }
    }
    throw error;
  }
}

export async function verifyFrontend(frontendRoot) {
  try {
    checkEnvironment();
    const info = await project(frontendRoot);
    await inspect(info.output, info.device, "directory");
    await inspect(path.join(info.output, STAMP), info.device, "file");
    const raw = await fs.readFile(path.join(info.output, STAMP));
    let value;
    try {value = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(raw));}
    catch {fail("schema");}
    keys(value, FIELDS);
    if (!raw.equals(Buffer.concat([canonical(value), Buffer.from("\n")]))) fail("schema");
    if (value.format !== "qcl-negf.frontend-receipt.v1") fail("schema");
    if (!["source-static", "nix-dist"].includes(value.mode) || value.mode !== info.mode) fail("mode");
    keys(value.recipe, ["id", "commands", "output_root", "node_env", "output_reset"]);
    if (!equal(value.recipe, recipe(info.mode))) fail("recipe");
    keys(value.toolchain, ["node", "npm", "typescript", "vite"]);
    if (Object.values(value.toolchain).some(version => !validVersion(version))
      || !/^24\.[0-9]+\.[0-9]+$/.test(value.toolchain.node)) fail("toolchain");
    rows(value.inputs);
    rows(value.outputs);
    for (const field of ["recipe", "inputs", "outputs"]) {
      validHash(value[field + "_sha256"]);
      if (sha(canonical(value[field])) !== value[field + "_sha256"]) fail(field);
    }
    if (!equal(value.inputs, await inventory(info.frontend, true))) fail("inputs");
    const lock = await readJson(path.join(info.frontend, "package-lock.json"), "toolchain");
    for (const name of ["typescript", "vite"]) {
      if (lock?.packages?.[`node_modules/${name}`]?.version !== value.toolchain[name]) fail("toolchain");
    }
    const outputs = await inventory(info.output);
    if (!equal(outputs, value.outputs)) fail("outputs");
    payload(outputs);
  } catch (error) {throw publicError(error);}
}

// Real tool location controls CLI roots. Importing this module performs no build.
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const frontend = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
  try {
    if (process.argv.length !== 3 || !["build", "verify"].includes(process.argv[2])) fail("recipe");
    if (process.argv[2] === "build") await buildFrontend(frontend);
    else await verifyFrontend(frontend);
  } catch (error) {
    const failure = publicError(error);
    process.stderr.write(failure.message + "\n");
    process.exitCode = failure.exitCode || 1;
  }
}
