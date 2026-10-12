/**
 * e2e/ai-workflow.mjs
 *
 * Day-32 end-to-end test of the AI -> property -> OpenSCAD -> viewport workflow:
 *   select wall -> natural-language request -> validated proposal card ->
 *   explicit "Apply change" -> POST /api/ai/confirm -> EntityStore.set_prop
 *   -> OpenSCAD -> new STL -> viewport mesh replacement
 * plus rejections and failure recovery (no selection, wrong target, door,
 * unsupported property/unit, missing/ambiguous/relative quantity, zero,
 * negative, NaN/Infinity, out of range, stale proposal, used proposal,
 * double submission, out-of-order STL loads, response lost after commit,
 * backend down, restart, OpenSCAD missing, persistence failure).
 *
 * Real: FastAPI backend (uvicorn), EntityStore, contract/provenance/executor,
 * OpenSCAD CLI, Vite dev server, React app, Three.js viewport in headless
 * Chrome (driven over raw CDP). Fake: the language model only -- a local
 * HTTP server speaking Ollama's POST /api/chat, returning fixed JSON chosen
 * per step. Nothing here measures language-model behaviour.
 *
 * Isolation: temporary model.json + SCAD dir via MODEL_JSON_PATH/SCAD_TMP_DIR;
 * backend/data/model.json sha256 checked before and after.
 *
 * Not covered here: the 600 s proposal expiry (backend test with a controlled
 * clock, tests/test_ai_property_bridge.py) and any real language model.
 *
 * Observation points and the read-only route to Viewport's SceneManager
 * (React fiber -> meshesByGuid) are the same as in e2e/workflow.mjs; this
 * test also adds "dispose" listeners to a mesh it is about to see replaced.
 *
 * Run from frontend/ (backend dev server on :8000 must NOT be running --
 * vite.config.ts proxies to :8000, so this test owns that port):
 *     node e2e/ai-workflow.mjs
 *
 * Optional env: CHROME_PATH, OPENSCAD_BINARY, BACKEND_PYTHON,
 * E2E_VITE_PORT (default 5199), E2E_CDP_PORT (default 9333),
 * E2E_LLM_PORT (default 11535), E2E_KEEP_TMP=1.
 */

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(FRONTEND_ROOT, "..");
const BACKEND_ROOT = path.join(REPO_ROOT, "backend");
const PROTECTED_MODEL_JSON = path.join(BACKEND_ROOT, "data", "model.json");
const IS_WINDOWS = process.platform === "win32";
const PYTHON =
  process.env.BACKEND_PYTHON ??
  path.join(BACKEND_ROOT, "venv", IS_WINDOWS ? "Scripts/python.exe" : "bin/python");
const CHROME =
  process.env.CHROME_PATH ??
  (IS_WINDOWS ? "C:/Program Files/Google/Chrome/Application/chrome.exe" : "google-chrome");
const OPENSCAD =
  process.env.OPENSCAD_BINARY ?? (IS_WINDOWS ? "C:/Program Files/OpenSCAD/openscad.exe" : "openscad");
const VITE_BIN = path.join(FRONTEND_ROOT, "node_modules", "vite", "bin", "vite.js");

const BACKEND_PORT = 8000; // fixed: vite.config.ts proxies /api and /geometry here
const VITE_PORT = Number(process.env.E2E_VITE_PORT ?? 5199);
const CDP_PORT = Number(process.env.E2E_CDP_PORT ?? 9333);
const LLM_PORT = Number(process.env.E2E_LLM_PORT ?? 11535); // fake Ollama; a real one is never contacted
const VIEW = { width: 1600, height: 900 };

const QTO = "Qto_WallBaseQuantities";
const WALL_A = "d32-wall-a", WALL_A_NAME = "Wall A";
const WALL_B = "d32-wall-b", WALL_B_NAME = "Wall B (stale STL url)";
const DOOR = "d32-door", DOOR_NAME = "Door D";
const SEED = { Length: 5000, Height: 3000, Thickness: 200 };
const SEED_STL = "d32-seed-a.stl";
const MISSING_STL = "d32-missing.stl";
const SELECTED_HEX = "ff8c1a";

const results = [];
const evidence = [];
function check(id, description, ok, detail = "") {
  results.push({ id, description, ok: Boolean(ok), detail });
  console.log(`${ok ? "PASS" : "FAIL"} [${id}] ${description}${detail ? ` -- ${detail}` : ""}`);
  return Boolean(ok);
}
function note(text) { evidence.push(text); console.log(`  · ${text}`); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, label, timeoutMs = 30_000, intervalMs = 50) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await fn();
    if (last) return last;
    await sleep(intervalMs);
  }
  throw new Error(`timed out waiting for: ${label}`);
}
const sha256 = (file) => createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const portInUse = (port) => new Promise((resolve) => {
  const s = net.connect({ port, host: "127.0.0.1" });
  s.once("connect", () => { s.destroy(); resolve(true); });
  s.once("error", () => resolve(false));
});
async function httpOk(url) { try { const r = await fetch(url); return r.ok ? r : null; } catch { return null; } }
function killTree(child) {
  if (!child || child.exitCode !== null) return;
  if (IS_WINDOWS) spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  else child.kill("SIGTERM");
}
const near = (a, b, tol = 1e-3) => Math.abs(a - b) <= tol;
const dimsOf = (q) => [q.Length, q.Thickness, q.Height]; // X=Length, Y=Thickness, Z=Height
const sameDims = (arr, dims) => arr.length === 3 && arr.every((v, i) => near(v, dims[i]));

/** Independent STL bounds reader (ASCII or binary); does not use the backend's parser. */
function stlBounds(file) {
  const buf = fs.readFileSync(file);
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  const add = (x, y, z) => [x, y, z].forEach((v, i) => { min[i] = Math.min(min[i], v); max[i] = Math.max(max[i], v); });
  const head = buf.subarray(0, 512).toString("latin1");
  let format;
  if (head.startsWith("solid") && /facet|endsolid/.test(buf.toString("latin1"))) {
    format = "ascii";
    for (const m of buf.toString("latin1").matchAll(/vertex\s+(\S+)\s+(\S+)\s+(\S+)/g)) add(+m[1], +m[2], +m[3]);
  } else {
    format = "binary";
    const n = buf.readUInt32LE(80);
    for (let t = 0; t < n; t++) {
      const o = 84 + t * 50 + 12;
      for (let v = 0; v < 3; v++) add(buf.readFloatLE(o + v * 12), buf.readFloatLE(o + v * 12 + 4), buf.readFloatLE(o + v * 12 + 8));
    }
  }
  return { min, max, format };
}

class Cdp {
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => { ws.addEventListener("open", res, { once: true }); ws.addEventListener("error", rej, { once: true }); });
    return new Cdp(ws);
  }
  constructor(ws) {
    this.ws = ws; this.nextId = 0; this.pending = new Map(); this.listeners = [];
    ws.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id !== undefined) {
        const e = this.pending.get(msg.id);
        if (!e) return;
        this.pending.delete(msg.id); clearTimeout(e.timer);
        if (msg.error) e.reject(new Error(`${e.method}: ${msg.error.message}`)); else e.resolve(msg.result);
      } else for (const l of this.listeners) l(msg.method, msg.params);
    });
  }
  send(method, params = {}) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP ${method} timed out`)); }, 30_000);
      this.pending.set(id, { resolve, reject, method, timer });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(l) { this.listeners.push(l); }
  close() { this.ws.close(); }
}

const FIND_SCENE_MANAGER = `(() => {
  const canvas = document.querySelector(".viewport__canvas");
  if (!canvas) return null;
  const key = Object.keys(canvas).find((k) => k.startsWith("__reactFiber$"));
  let fiber = key ? canvas[key] : null;
  while (fiber && !(typeof fiber.type === "function" && fiber.type.name === "Viewport")) fiber = fiber.return;
  for (let hook = fiber ? fiber.memoizedState : null; hook; hook = hook.next) {
    const v = hook.memoizedState;
    if (v && typeof v === "object" && "current" in v && v.current && v.current.meshesByGuid) return v.current;
  }
  return null;
})()`;

const probeExpr = (guid, oldUuid) => `(() => {
  const sm = ${FIND_SCENE_MANAGER};
  if (!sm) return { found: false };
  const entry = sm.meshesByGuid.get(${JSON.stringify(guid)});
  if (!entry) return { found: true, hasMesh: false, meshCount: sm.meshesByGuid.size };
  const mesh = entry.mesh;
  mesh.geometry.computeBoundingBox();
  const bb = mesh.geometry.boundingBox, p = mesh.position;
  return {
    found: true, hasMesh: true, uuid: mesh.uuid, geometryType: mesh.geometry.type,
    min: [bb.min.x + p.x, bb.min.y + p.y, bb.min.z + p.z],
    max: [bb.max.x + p.x, bb.max.y + p.y, bb.max.z + p.z],
    inScene: mesh.parent === sm.scene, color: mesh.material.color.getHexString(),
    highlight: sm.highlightByGuid.get(${JSON.stringify(guid)}) ?? "none",
    oldMeshStillInScene: ${oldUuid ? `Boolean(sm.scene.getObjectByProperty("uuid", ${JSON.stringify(oldUuid)}))` : "false"},
    meshCount: sm.meshesByGuid.size,
    sceneMeshes: sm.scene.children.filter((c) => c.isMesh).length,
  };
})()`;

/** Mark the current mesh of `guid` so its disposal can be observed (listeners only; app unchanged). */
const markMeshExpr = (guid) => `(() => {
  const sm = ${FIND_SCENE_MANAGER};
  const m = sm.meshesByGuid.get(${JSON.stringify(guid)}).mesh;
  const rec = { uuid: m.uuid, geometryDisposed: false, materialDisposed: false };
  m.geometry.addEventListener("dispose", () => { rec.geometryDisposed = true; });
  m.material.addEventListener("dispose", () => { rec.materialDisposed = true; });
  window.__d32marks = window.__d32marks || {};
  window.__d32marks[m.uuid] = { rec, mesh: m };
  return m.uuid;
})()`;
const markStateExpr = (uuid) => `(() => { const e = window.__d32marks[${JSON.stringify(uuid)}];
  return { ...e.rec, parentNull: e.mesh.parent === null }; })()`;

const UI_STATE = `(() => {
  const text = (el, sel) => el.querySelector(sel)?.textContent?.trim() ?? null;
  const cards = [...document.querySelectorAll(".ai-proposal")].map((c) => {
    const dds = [...c.querySelectorAll("dd")].map((d) => d.textContent.trim());
    const btn = c.querySelector(".ai-proposal__actions button");
    return { status: text(c, ".ai-proposal__status"), wall: dds[0], property: dds[1], value: dds[2],
      outcome: text(c, ".ai-proposal__outcome"), applyDisabled: btn ? btn.disabled : null,
      hint: text(c, ".ai-proposal__hint") };
  });
  const messages = [...document.querySelectorAll(".chat-message__text")].map((e) => e.textContent);
  const selectedRows = [...document.querySelectorAll(".model-tree__row--selected .model-tree__name")].map((e) => e.textContent.trim());
  return { cards, messages, thinking: Boolean(document.querySelector(".ai-chat-panel__thinking")),
    composerDisabled: document.querySelector(".ai-chat-panel__composer input")?.disabled ?? null,
    treeSelected: selectedRows, panelName: document.querySelector(".property-panel__name")?.textContent?.trim() ?? null };
})()`;

const fieldExpr = (label) => `(() => {
  const field = [...document.querySelectorAll(".property-panel .input-field")]
    .find((f) => f.querySelector(".input-field__label")?.textContent.trim() === ${JSON.stringify(label)});
  const input = field?.querySelector("input.input-field__number");
  if (!input) return null;
  input.scrollIntoView({ block: "center" });
  const r = input.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, value: input.value, disabled: input.disabled };
})()`;

function editReply(prop, value, unit, guid = WALL_A, pset = QTO) {
  const v = typeof value === "string" ? value : JSON.stringify(value); // raw "NaN" allowed
  return `{"intent":"edit_property","commands":[{"type":"EditProperty","params":{"target_guid":${JSON.stringify(guid)},"pset":${JSON.stringify(pset)},"property":${JSON.stringify(prop)},"value":${v},"unit":${JSON.stringify(unit)}}}],"text":"ok"}`;
}

async function main() {
  const protectedBefore = sha256(PROTECTED_MODEL_JSON);
  note(`protected model.json sha256 before: ${protectedBefore}`);
  for (const port of [BACKEND_PORT, VITE_PORT, CDP_PORT, LLM_PORT]) {
    if (await portInUse(port)) throw new Error(`port ${port} already in use`);
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "openbim-day32-e2e-"));
  const modelPath = path.join(tmp, "model.json");
  const scadDir = path.join(tmp, "scad");
  fs.mkdirSync(scadDir);
  const children = [];
  let backend = null, cdp = null, llmServer = null;

  // ── Fake Ollama ──
  let llmReply = null;
  let llmCalls = 0;
  llmServer = http.createServer((req, res) => {
    let body = "";
    req.on("data", (d) => { body += d; });
    req.on("end", () => {
      if (req.method === "POST" && req.url === "/api/chat") {
        llmCalls++;
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ model: "fake", message: { role: "assistant", content: llmReply }, done: true }));
      } else { res.writeHead(404); res.end(); }
    });
  });
  await new Promise((r) => llmServer.listen(LLM_PORT, "127.0.0.1", r));

  const backendLog = [];
  async function startBackend(openscadBinary) {
    backend = spawn(PYTHON, ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", String(BACKEND_PORT)], {
      cwd: BACKEND_ROOT,
      env: { ...process.env, MODEL_JSON_PATH: modelPath, SCAD_TMP_DIR: scadDir, OPENSCAD_BINARY: openscadBinary,
        LLM_PROVIDER: "ollama", OLLAMA_BASE_URL: `http://127.0.0.1:${LLM_PORT}`, OLLAMA_MODEL: "fake-model" },
    });
    children.push(backend);
    backend.stdout.on("data", (d) => backendLog.push(String(d)));
    backend.stderr.on("data", (d) => backendLog.push(String(d)));
    await waitFor(() => httpOk(`http://127.0.0.1:${BACKEND_PORT}/api/health`), "backend health");
  }
  async function stopBackend() {
    killTree(backend);
    await waitFor(async () => !(await portInUse(BACKEND_PORT)), "backend port free", 15_000);
  }
  const api = async (method, p, body) => {
    const r = await fetch(`http://127.0.0.1:${BACKEND_PORT}${p}`, {
      method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    let json = null; try { json = await r.json(); } catch { /* empty */ }
    return { status: r.status, body: json };
  };
  const serverWall = async (guid = WALL_A) => (await api("GET", "/api/model")).body.entities.find((e) => e.guid === guid);
  const diskWall = (guid = WALL_A) => JSON.parse(fs.readFileSync(modelPath, "utf8")).entities.find((e) => e.guid === guid);
  const stlFiles = () => fs.readdirSync(scadDir).filter((f) => f.endsWith(".stl")).sort();

  try {
    // ── Seed ──
    const seedScad = path.join(tmp, "seed.scad");
    fs.writeFileSync(seedScad, `cube([${dimsOf(SEED).join(", ")}]);\n`);
    const r = spawnSync(OPENSCAD, ["-o", path.join(scadDir, SEED_STL), seedScad], { encoding: "utf8" });
    if (r.status !== 0) throw new Error(`seed render failed: ${r.stderr}`);
    const wall = (guid, name, url) => ({ guid, type: "IfcWall", name, parent_guid: "d32-storey",
      psets: { [QTO]: { ...SEED } }, geometry: { type: "stl", url, bounds: { min: [0, 0, 0], max: dimsOf(SEED) } } });
    fs.writeFileSync(modelPath, JSON.stringify({
      project: { guid: "d32-project", name: "Day 32 AI workflow", description: "" },
      entities: [
        { guid: "d32-building", type: "IfcBuilding", name: "Test Building", children_guids: ["d32-storey"] },
        { guid: "d32-storey", type: "IfcBuildingStorey", name: "Ground Floor", parent_guid: "d32-building",
          children_guids: [WALL_A, WALL_B, DOOR] },
        wall(WALL_A, WALL_A_NAME, `/geometry/${SEED_STL}`),
        wall(WALL_B, WALL_B_NAME, `/geometry/${MISSING_STL}`),
        { guid: DOOR, type: "IfcDoor", name: DOOR_NAME, parent_guid: "d32-storey",
          psets: { Qto_DoorBaseQuantities: { Width: 900, Height: 2100 } } },
      ],
    }, null, 2));

    await startBackend(OPENSCAD);
    const vite = spawn(process.execPath, [VITE_BIN, "--port", String(VITE_PORT), "--strictPort", "--host", "127.0.0.1"], { cwd: FRONTEND_ROOT });
    children.push(vite);
    const appUrl = `http://127.0.0.1:${VITE_PORT}/`;
    await waitFor(() => httpOk(appUrl), "vite");
    await waitFor(() => httpOk(`${appUrl}api/model`), "vite proxy");

    const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${path.join(tmp, "chrome")}`,
      "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-first-run", "--no-default-browser-check",
      `--window-size=${VIEW.width},${VIEW.height}`, "about:blank"], { stdio: "ignore" });
    children.push(chrome);
    const target = await waitFor(async () => {
      const res = await httpOk(`http://127.0.0.1:${CDP_PORT}/json/list`);
      return (res ? await res.json() : []).find((t) => t.type === "page") ?? null;
    }, "chrome target");
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);

    const appErrors = [];
    const requests = new Map();
    let onFetchPaused = null;
    cdp.on((method, params) => {
      if (method === "Fetch.requestPaused") {
        if (onFetchPaused) onFetchPaused(params);
        else cdp.send("Fetch.continueRequest", { requestId: params.requestId }).catch(() => {});
      } else if (method === "Runtime.exceptionThrown") {
        appErrors.push(`exception: ${params.exceptionDetails.exception?.description ?? params.exceptionDetails.text}`);
      } else if (method === "Runtime.consoleAPICalled" && params.type === "error") {
        appErrors.push(`console.error: ${params.args.map((a) => a.value ?? a.description).join(" ")}`);
      } else if (method === "Network.requestWillBeSent") {
        requests.set(params.requestId, { url: params.request.url, method: params.request.method,
          postData: params.request.postData ?? null, status: null, finished: false, failed: null, t0: params.timestamp });
      } else if (method === "Network.responseReceived") {
        const q = requests.get(params.requestId); if (q) q.status = params.response.status;
      } else if (method === "Network.loadingFinished") {
        const q = requests.get(params.requestId); if (q) { q.finished = true; q.t1 = params.timestamp; }
      } else if (method === "Network.loadingFailed") {
        const q = requests.get(params.requestId); if (q) q.failed = params.errorText;
      }
    });
    for (const d of ["Page", "Runtime", "Network"]) await cdp.send(`${d}.enable`);
    await cdp.send("Emulation.setDeviceMetricsOverride", { ...VIEW, deviceScaleFactor: 1, mobile: false });

    const evaluate = async (expression) => {
      const res = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (res.exceptionDetails) throw new Error(`evaluate: ${res.exceptionDetails.exception?.description ?? res.exceptionDetails.text}`);
      return res.result.value;
    };
    const probe = (guid = WALL_A, oldUuid) => evaluate(probeExpr(guid, oldUuid));
    const ui = () => evaluate(UI_STATE);
    const reqsSince = (n) => [...requests.entries()].slice(n);
    const clickAt = async (x, y) => {
      await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
      await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
      await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 });
    };
    const rectOf = (selectorExpr) => evaluate(`(() => { const el = ${selectorExpr}; if (!el) return null;
      el.scrollIntoView({ block: "center" }); const r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2, hit: document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === el }; })()`);
    const selectInTree = async (name) => {
      const rr = await rectOf(`[...document.querySelectorAll(".model-tree__row")].find((r) => r.querySelector(".model-tree__name")?.textContent.trim() === ${JSON.stringify(name)})`);
      await clickAt(rr.x, rr.y);
      await waitFor(async () => (await ui()).treeSelected.join() === name, `tree selection ${name}`, 5_000);
    };
    const clearSelection = async () => {
      // Click empty viewport space (raycast hits nothing -> clear), verified to land on the canvas.
      const pt = await evaluate(`(() => { const r = document.querySelector(".viewport__canvas").getBoundingClientRect();
        const x = r.left + 15, y = r.top + 15; return { x, y, hit: document.elementFromPoint(x, y)?.classList.contains("viewport__canvas") }; })()`);
      if (!pt.hit) throw new Error("empty-viewport click point is not on the canvas");
      await clickAt(pt.x, pt.y);
      await waitFor(async () => (await ui()).treeSelected.length === 0, "selection cleared", 5_000);
    };
    /** Type and send a prompt through the chat composer; returns new message texts and new cards. */
    const sendPrompt = async (prompt, reply) => {
      llmReply = reply;
      const before = await ui();
      const callsBefore = llmCalls;
      const input = await rectOf(`document.querySelector(".ai-chat-panel__composer input")`);
      await clickAt(input.x, input.y);
      await cdp.send("Input.insertText", { text: prompt });
      const btn = await rectOf(`document.querySelector(".ai-chat-panel__composer button")`);
      await clickAt(btn.x, btn.y);
      const after = await waitFor(async () => {
        const s = await ui();
        return !s.thinking && s.messages.length >= before.messages.length + 2 ? s : null;
      }, `reply to "${prompt}"`, 30_000);
      await sleep(100);
      const settled = await ui();
      return { text: settled.messages.slice(before.messages.length + 1).join("\n"),
        newCards: settled.cards.slice(before.cards.length), firstCard: before.cards.length, llmCalled: llmCalls - callsBefore };
    };
    const applyButton = (i) => `document.querySelectorAll(".ai-proposal")[${i}]?.querySelector(".ai-proposal__actions button")`;
    const clickApply = async (i) => { const b = await rectOf(applyButton(i)); await clickAt(b.x, b.y); };
    const waitCardSettled = (i, label) => waitFor(async () => {
      const c = (await ui()).cards[i];
      return c && ["Applied", "Not applied", "Outcome unknown"].includes(c.status) ? c : null;
    }, label, 30_000);

    const expected = { ...SEED };
    let currentUrl = `/geometry/${SEED_STL}`;

    // ── 0. Load ──
    await cdp.send("Page.navigate", { url: appUrl });
    await waitFor(async () => { const p = await probe(); return p.hasMesh && p.geometryType === "BufferGeometry" ? p : null; }, "seed STL in viewport");
    await sleep(500);
    const pB = await probe(WALL_B);
    const missingReq = [...requests.values()].find((q) => q.url.endsWith(MISSING_STL));
    check("0", "app loads; Wall A shows its real seed STL", (await probe()).geometryType === "BufferGeometry");
    check("C13a", "stale/invalid geometry URL: Wall B's STL 404s and the bounds placeholder box stays (no crash)",
      missingReq?.status === 404 && pB.geometryType === "BoxGeometry" && sameDims(pB.max, dimsOf(SEED)),
      `GET ${MISSING_STL} -> ${missingReq?.status}; Wall B mesh ${pB.geometryType} max ${JSON.stringify(pB.max)}`);

    /**
     * Full successful AI edit through the UI: propose -> verify nothing changed -> Apply ->
     * verify server, disk, STL file bounds, browser STL fetch, viewport mesh, disposal, highlight.
     */
    const aiEdit = async (tag, prompt, reply, prop, valueMm) => {
      const diskBefore = fs.readFileSync(modelPath);
      const stlBefore = stlFiles();
      const beforeMesh = await probe();
      const n0 = requests.size;
      const res = await sendPrompt(prompt, reply);
      const card = res.newCards[0];
      check(`${tag}-1`, "AI returns a proposal card, not an applied change",
        res.newCards.length === 1 && card.status === "Proposed change — not applied" && /Proposed \(not applied\)/.test(res.text),
        `card status "${card?.status}", text "${res.text.split("\n")[0]}"`);
      check(`${tag}-2`, "card shows the intended wall, property and canonical mm value",
        card.wall === `${WALL_A_NAME} (${WALL_A})` && card.property === `${QTO}.${prop}` && card.value === `${valueMm} mm`,
        `${card.wall} | ${card.property} | ${card.value}`);
      const sw = await serverWall();
      const noConfirmYet = !reqsSince(n0).some(([, q]) => q.url.endsWith("/api/ai/confirm"));
      check(`${tag}-3`, "before confirmation: no property change, no regeneration, no confirm request, mesh unchanged",
        sw.psets[QTO][prop] === expected[prop] && sw.geometry.url === currentUrl && fs.readFileSync(modelPath).equals(diskBefore)
          && JSON.stringify(stlFiles()) === JSON.stringify(stlBefore) && noConfirmYet && (await probe()).uuid === beforeMesh.uuid,
        `server ${prop}=${sw.psets[QTO][prop]}, url ${sw.geometry.url}, STL files ${stlFiles().length}`);

      const oldUuid = await evaluate(markMeshExpr(WALL_A));
      const n1 = requests.size;
      const tClick = Date.now();
      await clickApply(res.firstCard);
      const settled = await waitCardSettled(res.firstCard, `${tag} apply`);
      const confirmEntry = reqsSince(n1).find(([, q]) => q.url.endsWith("/api/ai/confirm"));
      const sent = JSON.parse(confirmEntry[1].postData);
      check(`${tag}-4`, "confirm request carries only {proposal_id, selected_guid} -- no command or value",
        JSON.stringify(Object.keys(sent).sort()) === JSON.stringify(["proposal_id", "selected_guid"]) && sent.selected_guid === WALL_A,
        confirmEntry[1].postData);
      const body = JSON.parse((await cdp.send("Network.getResponseBody", { requestId: confirmEntry[0] })).body);
      expected[prop] = valueMm;
      const dims = dimsOf(expected);
      const newUrl = body.entity?.geometry?.url;
      check(`${tag}-5`, "server applied the stored proposal: 200, card 'Applied', response entity has the new value and a new STL url",
        confirmEntry[1].status === 200 && settled.status === "Applied" && body.entity.psets[QTO][prop] === valueMm
          && newUrl !== currentUrl && /^\/geometry\/[0-9a-f]{32}\.stl$/.test(newUrl),
        `HTTP ${confirmEntry[1].status}, "${settled.outcome}"`);
      const sw2 = await serverWall(), dw = diskWall();
      check(`${tag}-6`, "persisted via EntityStore: server memory and model.json on disk agree (all three quantities, new url)",
        JSON.stringify(sw2.psets[QTO]) === JSON.stringify(expected) && JSON.stringify(dw.psets[QTO]) === JSON.stringify(expected)
          && sw2.geometry.url === newUrl && dw.geometry.url === newUrl, JSON.stringify(dw.psets[QTO]));
      const stlPath = path.join(scadDir, newUrl.split("/").pop());
      const fileB = stlBounds(stlPath);
      check(`${tag}-7`, `independently parsed STL file bounds = [L,T,H] = ${JSON.stringify(dims)} mm, min at origin`,
        sameDims(fileB.max, dims) && sameDims(fileB.min, [0, 0, 0])
          && sameDims(body.entity.geometry.bounds.max, dims),
        `${fileB.format} STL min ${JSON.stringify(fileB.min)} max ${JSON.stringify(fileB.max)}; GeometryRef max ${JSON.stringify(body.entity.geometry.bounds.max)}`);
      const stlReq = await waitFor(() => reqsSince(n1).find(([, q]) => q.url.endsWith(newUrl) && (q.finished || q.failed)), `${tag} STL GET`, 15_000);
      const after = await waitFor(async () => {
        const p = await probe(WALL_A, oldUuid);
        return p.hasMesh && p.uuid !== oldUuid && p.geometryType === "BufferGeometry" && sameDims(p.max, dims) ? p : null;
      }, `${tag} STL mesh in viewport`, 15_000).catch(() => null);
      const tLoaded = Date.now();
      check(`${tag}-8`, "browser fetched the replacement STL and the viewport shows it (BufferGeometry, matching bounds)",
        stlReq[1].status === 200 && after && sameDims(after.min, [0, 0, 0]), `GET ${newUrl} -> ${stlReq[1].status}; mesh max ${JSON.stringify(after?.max)}`);
      const mark = await evaluate(markStateExpr(oldUuid));
      check(`${tag}-9`, "previous mesh removed from the scene and its geometry + material disposed",
        after && !after.oldMeshStillInScene && mark.parentNull && mark.geometryDisposed && mark.materialDisposed,
        JSON.stringify(mark));
      const s = await ui();
      const pBnow = await probe(WALL_B);
      check(`${tag}-10`, "selection and highlight preserved (Wall A selected/orange; Wall B not highlighted)",
        after && after.highlight === "selected" && after.color === SELECTED_HEX && s.treeSelected.join() === WALL_A_NAME
          && s.panelName === WALL_A_NAME && pBnow.highlight === "none",
        `A ${after?.highlight} #${after?.color}; B ${pBnow.highlight}; tree ${s.treeSelected}`);
      const field = await evaluate(fieldExpr(prop));
      check(`${tag}-11`, "Properties panel shows the confirmed value", field?.value === String(valueMm), field?.value);
      note(`${tag}: ${prop} ${sw.psets[QTO][prop]} -> ${valueMm} mm; bounds max ${JSON.stringify(beforeMesh.max)} -> ${JSON.stringify(after?.max)}; `
        + `confirm HTTP round trip ${((confirmEntry[1].t1 - confirmEntry[1].t0) * 1000).toFixed(0)} ms (CDP Network timestamps); `
        + `Apply click -> STL mesh visible ${tLoaded - tClick} ms (wall clock, includes 50 ms polling)`);
      currentUrl = newUrl;
    };

    /** A request the boundary must reject: no card, reason in text, nothing changed anywhere. */
    const rejected = async (tag, desc, prompt, reply, reason) => {
      const diskBefore = fs.readFileSync(modelPath);
      const stlBefore = stlFiles();
      const meshBefore = await probe();
      const n0 = requests.size;
      const res = await sendPrompt(prompt, reply);
      await sleep(150);
      const sw = await serverWall();
      check(tag, `${desc} -> rejected (${reason}); no card, nothing changed`,
        res.newCards.length === 0 && res.text.includes(`: ${reason} (`) && /No change was proposed/.test(res.text)
          && fs.readFileSync(modelPath).equals(diskBefore) && JSON.stringify(stlFiles()) === JSON.stringify(stlBefore)
          && JSON.stringify(sw.psets[QTO]) === JSON.stringify(expected) && sw.geometry.url === currentUrl
          && !reqsSince(n0).some(([, q]) => q.url.endsWith("/api/ai/confirm") || q.url.includes("/scad/"))
          && (await probe()).uuid === meshBefore.uuid,
        res.text.replace(/\n/g, " | "));
    };

    // ── A/B. Successful edits through the real UI ──
    await selectInTree(WALL_A_NAME);
    check("A0", "Wall A selected via the Model Tree; highlighted in the viewport",
      (await probe()).highlight === "selected" && (await probe()).color === SELECTED_HEX);
    await aiEdit("A", "Make the selected wall 3.2 m high", editReply("Height", 3.2, "m"), "Height", 3200);
    check("B1", "reference dimensions reached: [Length, Thickness, Height] = [5000, 200, 3200]",
      JSON.stringify(dimsOf(expected)) === "[5000,200,3200]");
    await aiEdit("B2", "Make the selected wall 4 m long", editReply("Length", 4, "m"), "Length", 4000);
    await aiEdit("B3", "Make the selected wall 250 mm thick", editReply("Thickness", 250, "mm"), "Thickness", 250);

    // ── C. Rejections through the real UI ──
    await clearSelection();
    await rejected("C1", "no wall selected", "Make the wall 3 m high", editReply("Height", 3, "m"), "no_selection");
    await selectInTree(WALL_A_NAME);
    await rejected("C2", "LLM targets Wall B while Wall A is selected", "Make the wall 3 m high", editReply("Height", 3, "m", WALL_B), "target_mismatch");
    await selectInTree(DOOR_NAME);
    await rejected("C3", "unsupported entity type (IfcDoor selected and targeted)", "Make the selected door 2.2 m high",
      editReply("Height", 2.2, "m", DOOR, "Qto_DoorBaseQuantities"), "unsupported_target_type");
    await selectInTree(WALL_A_NAME);
    await rejected("C4", "unsupported property (Width)", "Make the selected wall 3 m wide", editReply("Width", 3, "m"), "unsupported_property");
    await rejected("C5a", "no explicit quantity", "Make the selected wall taller", editReply("Height", 3500, "mm"), "no_explicit_quantity");
    await rejected("C5b", "ambiguous quantity", "Make the selected wall 3 m or 4 m high", editReply("Height", 3, "m"), "ambiguous_quantity");
    await rejected("C5c", "unsupported unit (ft)", "Make the selected wall 10 ft high", editReply("Height", 10, "ft"), "unsupported_unit");
    await rejected("C5d", "relative change", "Make the selected wall 1 m higher", editReply("Height", 1, "m"), "relative_change_unsupported");
    await rejected("C6a", "zero dimension", "Make the selected wall 0 m high", editReply("Height", 0, "m"), "value_out_of_range");
    await rejected("C6b", "negative dimension", "Make the selected wall -3 m high", editReply("Height", -3, "m"), "value_out_of_range");
    await rejected("C6c", "non-finite dimension (NaN from the model)", "Make the selected wall 3 m high", editReply("Height", "NaN", "m"), "invalid_value");
    await rejected("C6d", "non-finite dimension (Infinity from the model)", "Make the selected wall 3 m high", editReply("Height", "Infinity", "m"), "invalid_value");
    await rejected("C6e", "out of range (150 m > 100 m bound)", "Make the selected wall 150 m high", editReply("Height", 150, "m"), "value_out_of_range");
    await rejected("C6f", "wrong conversion (3.2 m proposed as 3200 m)", "Make the selected wall 3.2 m high", editReply("Height", 3200, "m"), "value_out_of_range");

    // ── C7. Selection change after proposal (UI guard), then stale proposal after a manual edit ──
    let res = await sendPrompt("Make the selected wall 3.4 m high", editReply("Height", 3.4, "m"));
    const staleCard = res.firstCard;
    await selectInTree(WALL_B_NAME);
    let c = (await ui()).cards[staleCard];
    const disabledOnB = c.applyDisabled && c.hint === "Select this wall to apply the change.";
    await selectInTree(WALL_A_NAME);
    c = (await ui()).cards[staleCard];
    check("C7a", "Apply is disabled with a hint while another wall is selected; re-enabled on reselecting",
      disabledOnB && c.applyDisabled === false, `on B disabled=${disabledOnB}, on A disabled=${c.applyDisabled}`);
    // Manual edit of the same property through the Properties panel (real UI path).
    const field = await waitFor(async () => { const f = await evaluate(fieldExpr("Height")); return f && !f.disabled ? f : null; }, "Height field");
    await clickAt(field.x, field.y);
    await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "a", code: "KeyA", windowsVirtualKeyCode: 65, modifiers: 2, commands: ["selectAll"] });
    await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "a", code: "KeyA", windowsVirtualKeyCode: 65 });
    await cdp.send("Input.insertText", { text: "3300" });
    await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
    await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
    await waitFor(async () => (await serverWall()).psets[QTO].Height === 3300, "manual edit committed");
    expected.Height = 3300;
    currentUrl = (await serverWall()).geometry.url;
    await waitFor(async () => { const p = await probe(); return p.geometryType === "BufferGeometry" && sameDims(p.max, dimsOf(expected)) ? p : null; }, "manual-edit mesh");
    const diskBeforeStale = fs.readFileSync(modelPath);
    const stlBeforeStale = stlFiles();
    const meshBeforeStale = await probe();
    await clickApply(staleCard);
    c = await waitCardSettled(staleCard, "stale apply");
    await sleep(300);
    check("C7b", "stale proposal (Height changed manually to 3300 after proposing 3400): 'Not applied', server and viewport keep 3300",
      c.status === "Not applied" && /changed since this proposal/.test(c.outcome ?? "") && (await serverWall()).psets[QTO].Height === 3300
        && fs.readFileSync(modelPath).equals(diskBeforeStale) && JSON.stringify(stlFiles()) === JSON.stringify(stlBeforeStale)
        && (await probe()).uuid === meshBeforeStale.uuid, c.outcome);

    // ── C8. Server-side target check at confirm, then UI on an already-used proposal ──
    res = await sendPrompt("Make the selected wall 3.5 m high", editReply("Height", 3.5, "m"));
    const usedCard = res.firstCard;
    const ids = await api("POST", "/api/ai/prompt", { prompt: "Make the selected wall 3.5 m high", selected_guid: WALL_A });
    const direct = await api("POST", "/api/ai/confirm", { proposal_id: ids.body.proposal_ids[0], selected_guid: WALL_B });
    check("C8a", "confirm with a different selected wall (direct HTTP) -> 422 target_mismatch, nothing changed",
      direct.status === 422 && direct.body.detail.code === "target_mismatch" && (await serverWall()).psets[QTO].Height === 3300,
      `${direct.status} ${direct.body.detail.code}`);
    const again = await api("POST", "/api/ai/confirm", { proposal_id: ids.body.proposal_ids[0], selected_guid: WALL_A });
    check("C8b", "the rejected proposal is used up: re-confirm -> 404 proposal_not_found",
      again.status === 404 && again.body.detail.code === "proposal_not_found");
    // UI card for a different, still-valid proposal: dismiss it (visual only) and verify nothing happens.
    const dismissBtn = await rectOf(`document.querySelectorAll(".ai-proposal")[${usedCard}]?.querySelectorAll(".ai-proposal__actions button")[1]`);
    await clickAt(dismissBtn.x, dismissBtn.y);
    await sleep(200);
    c = (await ui()).cards[usedCard];
    check("C8c", "Dismiss: card shows 'Dismissed — not applied', no request, nothing changed",
      c.status === "Dismissed — not applied" && (await serverWall()).psets[QTO].Height === 3300);

    // ── C9. Double submission (synchronous double click) ──
    res = await sendPrompt("Make the selected wall 3.6 m high", editReply("Height", 3.6, "m"));
    let n = requests.size;
    await evaluate(`(() => { const b = ${applyButton(res.firstCard)}; b.click(); b.click(); })()`);
    c = await waitCardSettled(res.firstCard, "double click apply");
    await sleep(500);
    const confirms = reqsSince(n).filter(([, q]) => q.url.endsWith("/api/ai/confirm"));
    expected.Height = 3600;
    currentUrl = (await serverWall()).geometry.url;
    check("C9", "synchronous double click sends exactly one /api/ai/confirm; applied once",
      confirms.length === 1 && c.status === "Applied" && (await serverWall()).psets[QTO].Height === 3600, `${confirms.length} confirm request(s)`);
    await waitFor(async () => { const p = await probe(); return p.geometryType === "BufferGeometry" && sameDims(p.max, dimsOf(expected)) ? p : null; }, "C9 mesh");

    // ── C10. Rapid successive edits with an out-of-order (delayed) STL load ──
    const r1 = await sendPrompt("Make the selected wall 4.5 m long", editReply("Length", 4.5, "m"));
    const r2 = await sendPrompt("Make the selected wall 300 mm thick", editReply("Thickness", 300, "mm"));
    // Hold the first request for a NEW Wall A STL (i.e. edit 1's), let everything else through.
    let heldStl = null;
    let heldUrl = null;
    const knownUrl = currentUrl;
    onFetchPaused = (params) => {
      const u = params.request.url;
      if (!heldStl && !u.endsWith(knownUrl) && !u.endsWith(MISSING_STL)) { heldStl = params.requestId; heldUrl = u; return; }
      cdp.send("Fetch.continueRequest", { requestId: params.requestId }).catch(() => {});
    };
    await cdp.send("Fetch.enable", { patterns: [{ urlPattern: "*/geometry/*", requestStage: "Request" }] });
    await clickApply(r1.firstCard);
    await waitCardSettled(r1.firstCard, "C10 apply 1");
    await waitFor(() => heldStl, "edit-1 STL request held", 10_000).catch(() => null);
    await waitFor(async () => (await ui()).cards[r2.firstCard].applyDisabled === false, "apply 2 enabled", 10_000);
    await clickApply(r2.firstCard);
    await waitCardSettled(r2.firstCard, "C10 apply 2");
    expected.Length = 4500; expected.Thickness = 300;
    const finalUrl = (await serverWall()).geometry.url;
    currentUrl = finalUrl;
    const finalMesh = await waitFor(async () => { const p = await probe(); return p.geometryType === "BufferGeometry" && sameDims(p.max, dimsOf(expected)) ? p : null; }, "C10 final mesh", 15_000).catch(() => null);
    const wasHeld = Boolean(heldStl);
    if (heldStl) await cdp.send("Fetch.continueRequest", { requestId: heldStl }).catch(() => {});
    onFetchPaused = null;
    await sleep(1500);
    await cdp.send("Fetch.disable");
    const afterRelease = await probe();
    check("C10", "rapid successive AI edits: the late (stale) STL of edit 1 arriving after edit 2 does not replace edit 2's mesh",
      wasHeld && finalMesh && afterRelease.uuid === finalMesh.uuid && sameDims(afterRelease.max, dimsOf(expected))
        && afterRelease.highlight === "selected" && afterRelease.meshCount === finalMesh.meshCount,
      `held=${wasHeld} (${heldUrl?.split("/").pop()}); final ${finalUrl.split("/").pop()} max ${JSON.stringify(afterRelease.max)} expected ${JSON.stringify(dimsOf(expected))}`);

    // ── C11. Interrupted request: response lost AFTER the backend committed ──
    res = await sendPrompt("Make the selected wall 3.7 m high", editReply("Height", 3.7, "m"));
    onFetchPaused = (params) => cdp.send("Fetch.failRequest", { requestId: params.requestId, errorReason: "ConnectionReset" }).catch(() => {});
    await cdp.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/ai/confirm", requestStage: "Response" }] });
    await clickApply(res.firstCard);
    c = await waitCardSettled(res.firstCard, "C11 apply");
    await cdp.send("Fetch.disable");
    onFetchPaused = null;
    const swC11 = await serverWall();
    const dimsC11 = dimsOf({ ...expected, Height: swC11.psets[QTO].Height });
    const meshC11 = await waitFor(async () => { const q = await probe(); return q.geometryType === "BufferGeometry" && sameDims(q.max, dimsC11) ? q : null; },
      "C11 viewport shows the server's state", 15_000).catch(() => null);
    const fieldC11 = await waitFor(async () => { const f = await evaluate(fieldExpr("Height")); return f?.value === String(swC11.psets[QTO].Height) ? f : null; },
      "C11 Properties shows the server's state", 10_000).catch(() => null);
    note(`C11 observed: card "${c.status}" / "${c.outcome}"; server Height ${swC11.psets[QTO].Height}; viewport max ${JSON.stringify((await probe()).max)}; Properties Height field ${(await evaluate(fieldExpr("Height")))?.value}`);
    check("C11", "interrupted confirm (server committed, response lost): card says 'Outcome unknown' (not 'Not applied') and the model is reloaded, so viewport + Properties show the server's actual value",
      swC11.psets[QTO].Height === 3700 && c.status === "Outcome unknown" && /may or may not have been applied/.test(c.outcome ?? "")
        && meshC11 && fieldC11 && meshC11.highlight === "selected",
      `card "${c.status}", server Height ${swC11.psets[QTO].Height}, viewport Z ${meshC11?.max[2]}, field ${fieldC11?.value}`);
    expected.Height = swC11.psets[QTO].Height; currentUrl = swC11.geometry.url;

    // Resync the page from the server before the restart phases (reload, reselect).
    await cdp.send("Page.reload");
    await waitFor(async () => { const p = await probe(); return p.hasMesh && p.geometryType === "BufferGeometry" && sameDims(p.max, dimsOf(expected)) ? p : null; }, "reload mesh");
    await selectInTree(WALL_A_NAME);

    // ── C12. Backend unavailable; proposals lost on restart ──
    const ra = await sendPrompt("Make the selected wall 3.8 m high", editReply("Height", 3.8, "m"));
    const rb = await sendPrompt("Make the selected wall 3.9 m high", editReply("Height", 3.9, "m"));
    const diskBeforeDown = fs.readFileSync(modelPath);
    const meshBeforeDown = await probe();
    await stopBackend();
    await clickApply(ra.firstCard);
    c = await waitCardSettled(ra.firstCard, "C12 apply while down");
    await sleep(1500);
    const treeText = await evaluate(`document.querySelector(".model-tree")?.textContent?.trim().slice(0, 120) ?? null`);
    const meshDown = await probe();
    note(`C12a observed: card "${c.status}" / "${c.outcome}"; Model Tree now shows: "${treeText}"`);
    check("C12a", "backend down (dev proxy answers 500): card 'Outcome unknown' + unreachable message, never 'Applied'; viewport geometry and model file unchanged",
      c.status === "Outcome unknown" && /Could not reach the backend/.test(c.outcome ?? "")
        && sameDims(meshDown.max, meshBeforeDown.max) && meshDown.highlight === "selected"
        && fs.readFileSync(modelPath).equals(diskBeforeDown), c.outcome);
    const meshAfterDown = await probe(); // definite rejections below must not touch the viewport

    // ── C13. OpenSCAD unavailable ──
    await startBackend(path.join(tmp, "openscad-not-installed.exe"));
    await clickApply(rb.firstCard);
    c = await waitCardSettled(rb.firstCard, "C12b apply after restart");
    check("C12b", "after a backend restart the old proposal is gone: 'Not applied' (404 no longer available), nothing changed",
      c.status === "Not applied" && /no longer available/.test(c.outcome ?? "") && fs.readFileSync(modelPath).equals(diskBeforeDown), c.outcome);
    res = await sendPrompt("Make the selected wall 3.9 m high", editReply("Height", 3.9, "m"));
    const stlBeforeNoScad = stlFiles();
    n = requests.size;
    await clickApply(res.firstCard);
    c = await waitCardSettled(res.firstCard, "C13 apply");
    await sleep(500);
    check("C13b", "OpenSCAD binary missing: 502 'Geometry could not be regenerated', nothing persisted, no STL, viewport unchanged",
      c.status === "Not applied" && /Geometry could not be regenerated/.test(c.outcome ?? "")
        && reqsSince(n).some(([, q]) => q.url.endsWith("/api/ai/confirm") && q.status === 502)
        && fs.readFileSync(modelPath).equals(diskBeforeDown) && JSON.stringify(stlFiles()) === JSON.stringify(stlBeforeNoScad)
        && (await serverWall()).psets[QTO].Height === expected.Height && (await probe()).uuid === meshAfterDown.uuid, c.outcome);

    // ── C14. Persistence failure (model.json read-only; real os.replace failure) ──
    await stopBackend();
    await startBackend(OPENSCAD);
    res = await sendPrompt("Make the selected wall 3.9 m high", editReply("Height", 3.9, "m"));
    fs.chmodSync(modelPath, 0o444);
    const stlBeforePersist = stlFiles();
    n = requests.size;
    await clickApply(res.firstCard);
    c = await waitCardSettled(res.firstCard, "C14 apply");
    await sleep(500);
    fs.chmodSync(modelPath, 0o666);
    const orphan = stlFiles().filter((f) => !stlBeforePersist.includes(f));
    check("C14", "persistence failure: 500 'The model could not be saved', server memory and disk unchanged, viewport unchanged",
      c.status === "Not applied" && /could not be saved/.test(c.outcome ?? "")
        && reqsSince(n).some(([, q]) => q.url.endsWith("/api/ai/confirm") && q.status === 500)
        && fs.readFileSync(modelPath).equals(diskBeforeDown) && (await serverWall()).psets[QTO].Height === expected.Height
        && (await probe()).uuid === meshAfterDown.uuid, `${c.outcome}; unreferenced STL files left: ${orphan.length}`);
    note(`C14: the failed edit left ${orphan.length} unreferenced STL file(s) in SCAD_TMP_DIR (rendered before the save failed)`);

    // ── D. API-level checks on the real backend ──
    llmReply = editReply("Height", 3.1, "m");
    let p = await api("POST", "/api/ai/prompt", { prompt: "Make the selected wall 3.1 m high", selected_guid: WALL_A });
    const forged = await api("POST", "/api/ai/confirm", { proposal_id: p.body.proposal_ids[0], selected_guid: WALL_A,
      value: 9999, unit: "mm", property: "Length",
      command: { type: "EditProperty", params: { target_guid: WALL_A, pset: QTO, property: "Length", value: 1, unit: "mm" } } });
    expected.Height = 3100;
    check("D1", "client-supplied value/property/command in the confirm body are ignored: the stored proposal (Height 3100) is executed",
      forged.status === 200 && JSON.stringify(forged.body.entity.psets[QTO]) === JSON.stringify(expected),
      JSON.stringify(forged.body.entity?.psets?.[QTO]));
    llmReply = editReply("Height", 2.9, "m");
    p = await api("POST", "/api/ai/prompt", { prompt: "Make the selected wall 2.9 m high", selected_guid: WALL_A });
    const statuses = (await Promise.all(Array.from({ length: 5 }, () =>
      api("POST", "/api/ai/confirm", { proposal_id: p.body.proposal_ids[0], selected_guid: WALL_A })))).map((x) => x.status).sort();
    expected.Height = 2900;
    check("D2", "5 concurrent confirms of one proposal against the running server: exactly one 200, four 404",
      JSON.stringify(statuses) === "[200,404,404,404,404]" && (await serverWall()).psets[QTO].Height === 2900, JSON.stringify(statuses));
    const auditLines = backendLog.join("").split(/\r?\n/).filter((l) => l.includes("AI property edit applied"));
    note(`backend audit log lines "AI property edit applied": ${auditLines.length} (INFO level; visible only if uvicorn logs INFO for app loggers)`);

    // ── Final: page reload reflects the authoritative state ──
    await cdp.send("Page.reload");
    const finalProbe = await waitFor(async () => { const q = await probe(); return q.hasMesh && q.geometryType === "BufferGeometry" && sameDims(q.max, dimsOf(expected)) ? q : null; }, "final mesh", 15_000).catch(() => null);
    const finalFile = stlBounds(path.join(scadDir, (await serverWall()).geometry.url.split("/").pop()));
    check("E", "after reload the viewport, server, disk and STL file all agree on the final dimensions",
      finalProbe && sameDims(finalFile.max, dimsOf(expected)) && JSON.stringify(diskWall().psets[QTO]) === JSON.stringify(expected),
      `final [L,T,H] ${JSON.stringify(dimsOf(expected))}`);

    const unexpected = appErrors.filter((e) => !/favicon|status of (404|422|500|502)|Failed to load resource|ERR_CONNECTION|Failed to fetch|NetworkError|Failed to load model: ApiError: GET \/(model|tree) failed with status 500/.test(e));
    note(`browser console errors: ${appErrors.length} total (expected ones from deliberate failures), unexpected: ${unexpected.length}`);
    for (const e of unexpected) note(`unexpected: ${e}`);
    const tracebacks = backendLog.join("").split(/\r?\n/).filter((l) => /Traceback/.test(l));
    note(`backend tracebacks in log: ${tracebacks.length} (C14's deliberate persistence failure logs one via logger.exception)`);
    check("K", "no unexpected browser exceptions/errors", unexpected.length === 0);
  } finally {
    if (cdp) cdp.close();
    for (const child of children.reverse()) killTree(child);
    llmServer?.close();
    await sleep(500);
    try { fs.chmodSync(modelPath, 0o666); } catch { /* gone */ }
    if (process.env.E2E_KEEP_TMP) console.log(`kept ${tmp}`);
    else fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    const after = sha256(PROTECTED_MODEL_JSON);
    check("M", "backend/data/model.json untouched (sha256 unchanged)", after === protectedBefore, after);
  }
}

main()
  .catch((err) => { console.error(`ERROR: ${err.stack}`); results.push({ id: "run", ok: false }); })
  .finally(() => {
    const failed = results.filter((r) => !r.ok);
    console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
    if (failed.length) console.log(`failed: ${failed.map((f) => f.id).join(", ")}`);
    process.exitCode = failed.length === 0 ? 0 : 1;
  });
