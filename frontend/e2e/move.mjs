/**
 * e2e/move.mjs
 *
 * Day-52C Move tool test (browser half; backend rules are covered by
 * backend/tests/test_property_transaction.py PlacementEditTest).
 *
 * Drives the real application in headless Chrome:
 *   Move availability (no selection / storey / one wall / two walls)
 *   -> real mouse drag on the gizmo's X arrow -> exactly one
 *   POST /api/properties edit of OpenBIM_Placement.X -> OpenSCAD -> new
 *   STL at the new world position -> viewport mesh replaced, selection kept
 *   -> stationary click on the gizmo over empty space keeps the selection
 *   -> XY-plane drag rejected (no request) -> injected 502 reverts
 *   -> page reload keeps the position (viewport + Properties panel)
 *   -> Select mode still selects.
 *
 * The 502 case is an INJECTED response (CDP Fetch answers the POST; the
 * backend is never called), as in workflow.mjs N2: it tests frontend
 * revert handling only. Real backend atomicity is a backend unit test.
 *
 * Isolation: own backend on a temporary model (MODEL_JSON_PATH /
 * SCAD_TMP_DIR) seeded with two walls: A at the origin WITHOUT an
 * OpenBIM_Placement set (an existing wall), B with OpenBIM_Placement
 * X = -7000. backend/data/model.json is never read or written; its sha256
 * is checked before and after.
 *
 * React/Three internals read (read-only), as in workflow.mjs: Viewport's
 * SceneManager via the canvas's React fiber -> meshesByGuid (mesh
 * geometry type and world bounding box), controls.enabled, and the
 * TransformControls instance (attached object, dragging, and the world
 * position of its invisible X / XY picker handles, projected to the screen
 * so the test can press exactly where a user would grab them).
 *
 * Run from frontend/ (nothing may listen on :8000 -- vite.config.ts
 * proxies there, so this test owns that port):
 *     node e2e/move.mjs
 *
 * Optional env: CHROME_PATH, OPENSCAD_BINARY, BACKEND_PYTHON,
 * E2E_VITE_PORT (default 5199), E2E_CDP_PORT (default 9333), E2E_KEEP_TMP=1.
 */

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(FRONTEND_ROOT, "..");
const BACKEND_ROOT = path.join(REPO_ROOT, "backend");
const PROTECTED_MODEL_JSON = path.join(BACKEND_ROOT, "data", "model.json");
const IS_WINDOWS = process.platform === "win32";

const BACKEND_PORT = 8000;
const VITE_PORT = Number(process.env.E2E_VITE_PORT ?? 5199);
const CDP_PORT = Number(process.env.E2E_CDP_PORT ?? 9333);
const VIEW = { width: 1600, height: 900 };

const PYTHON =
  process.env.BACKEND_PYTHON ??
  path.join(BACKEND_ROOT, "venv", IS_WINDOWS ? "Scripts/python.exe" : "bin/python");
const CHROME =
  process.env.CHROME_PATH ??
  (IS_WINDOWS ? "C:/Program Files/Google/Chrome/Application/chrome.exe" : "google-chrome");
const OPENSCAD =
  process.env.OPENSCAD_BINARY ?? (IS_WINDOWS ? "C:/Program Files/OpenSCAD/openscad.exe" : "openscad");
const VITE_BIN = path.join(FRONTEND_ROOT, "node_modules", "vite", "bin", "vite.js");

const QTO = "Qto_WallBaseQuantities";
const PLACEMENT = "OpenBIM_Placement";
const STOREY = { guid: "day52-storey-001", name: "Ground Floor" };
const WALL_A = { guid: "day52-wall-a", name: "Wall A", dims: { Length: 5000, Height: 3000, Thickness: 200 }, at: [0, 0, 0] };
const WALL_B = { guid: "day52-wall-b", name: "Wall B", dims: { Length: 4000, Height: 3000, Thickness: 200 }, at: [-7000, 0, 0] };
const SELECTED_HEX = "ff8c1a";

// ── Result recording ─────────────────────────────────────────────────────────

const results = [];

function check(id, description, ok, detail = "") {
  results.push({ id, description, ok: Boolean(ok) });
  console.log(`${ok ? "PASS" : "FAIL"} [${id}] ${description}${detail ? ` -- ${detail}` : ""}`);
  return Boolean(ok);
}

function mustPass(id, description, ok, detail = "") {
  if (!check(id, description, ok, detail)) throw new Error(`prerequisite failed: [${id}] ${description}`);
}

// ── Small helpers ────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(fn, label, timeoutMs = 30_000, intervalMs = 50) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await fn();
    if (value) return value;
    await sleep(intervalMs);
  }
  throw new Error(`timed out waiting for: ${label}`);
}

const sha256 = (file) => createHash("sha256").update(fs.readFileSync(file)).digest("hex");

function portInUse(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ port, host: "127.0.0.1" });
    socket.once("connect", () => { socket.destroy(); resolve(true); });
    socket.once("error", () => resolve(false));
  });
}

async function httpOk(url) {
  try {
    const response = await fetch(url);
    return response.ok ? response : null;
  } catch {
    return null;
  }
}

function killTree(child) {
  if (!child || child.exitCode !== null) return;
  if (IS_WINDOWS) spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  else child.kill("SIGTERM");
}

const near = (a, b, tol = 1e-3) => Math.abs(a - b) <= tol;
/** wall.scad.j2: X=Length, Y=Thickness, Z=Height, translated to the placement. */
const boundsOf = (wall, at = wall.at) => ({
  min: at,
  max: [at[0] + wall.dims.Length, at[1] + wall.dims.Thickness, at[2] + wall.dims.Height],
});
const sameBounds = (probe, bounds) =>
  probe && probe.min.every((v, i) => near(v, bounds.min[i])) && probe.max.every((v, i) => near(v, bounds.max[i]));

// ── Minimal CDP client (as in workflow.mjs) ──────────────────────────────────

class Cdp {
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", reject, { once: true });
    });
    return new Cdp(ws);
  }

  constructor(ws) {
    this.ws = ws;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = [];
    ws.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id !== undefined) {
        const entry = this.pending.get(msg.id);
        if (!entry) return;
        this.pending.delete(msg.id);
        clearTimeout(entry.timer);
        if (msg.error) entry.reject(new Error(`${entry.method}: ${msg.error.message}`));
        else entry.resolve(msg.result);
      } else {
        for (const listener of this.listeners) listener(msg.method, msg.params);
      }
    });
  }

  send(method, params = {}) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP ${method} timed out`));
      }, 30_000);
      this.pending.set(id, { resolve, reject, method, timer });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  on(listener) {
    this.listeners.push(listener);
  }

  close() {
    this.ws.close();
  }
}

// ── In-page expressions ──────────────────────────────────────────────────────

const FIND_SCENE_MANAGER = `(() => {
  const canvas = document.querySelector(".viewport__canvas");
  if (!canvas) return null;
  const key = Object.keys(canvas).find((k) => k.startsWith("__reactFiber$"));
  let fiber = key ? canvas[key] : null;
  while (fiber && !(typeof fiber.type === "function" && fiber.type.name === "Viewport")) fiber = fiber.return;
  for (let hook = fiber ? fiber.memoizedState : null; hook; hook = hook.next) {
    const value = hook.memoizedState;
    if (value && typeof value === "object" && "current" in value && value.current && value.current.meshesByGuid) {
      return value.current;
    }
  }
  return null;
})()`;

/** World bounds of a guid's mesh (geometry bbox + mesh position), and the gizmo state. */
const probeExpression = (guid) => `(() => {
  const sm = ${FIND_SCENE_MANAGER};
  if (!sm) return { found: false };
  const entry = sm.meshesByGuid.get(${JSON.stringify(guid)});
  const tc = sm.transformControls;
  const gizmo = { attachedTo: tc && tc.object ? tc.object.userData.guid ?? null : null,
                  attachedToCurrentMesh: Boolean(tc && entry && tc.object === entry.mesh),
                  visible: Boolean(tc && tc.visible), dragging: Boolean(tc && tc.dragging) };
  if (!entry) return { found: true, hasMesh: false, gizmo, orbitEnabled: sm.controls?.enabled ?? null };
  const mesh = entry.mesh;
  mesh.geometry.computeBoundingBox();
  const bb = mesh.geometry.boundingBox, p = mesh.position;
  return {
    found: true, hasMesh: true, uuid: mesh.uuid, geometryType: mesh.geometry.type,
    min: [bb.min.x + p.x, bb.min.y + p.y, bb.min.z + p.z],
    max: [bb.max.x + p.x, bb.max.y + p.y, bb.max.z + p.z],
    color: mesh.material.color.getHexString(),
    meshCount: sm.meshesByGuid.size,
    gizmo, orbitEnabled: sm.controls?.enabled ?? null,
  };
})()`;

/**
 * Page coordinates of the gizmo's centre, its +X and -X arrows and its
 * XY-plane handle (picker meshes are invisible; their world centres are
 * projected with the scene camera). Vector3 is borrowed from
 * camera.position. For the -X arrow, `picked` is what a selection click
 * there would pick (SceneManager.pick).
 */
const GIZMO_HANDLES = `(() => {
  const sm = ${FIND_SCENE_MANAGER};
  const tc = sm && sm.transformControls;
  if (!tc || !tc.object) return null;
  tc.updateMatrixWorld(true);
  const V = sm.camera.position.constructor;
  const rect = document.querySelector(".viewport__canvas").getBoundingClientRect();
  const toPage = (v) => { const n = v.clone().project(sm.camera);
    return { x: rect.left + (n.x + 1) / 2 * rect.width, y: rect.top + (1 - n.y) / 2 * rect.height, ndc: [n.x, n.y] }; };
  const centreOf = (mesh) => { mesh.geometry.computeBoundingBox(); const c = mesh.geometry.boundingBox.getCenter(new V()); return mesh.localToWorld(c); };
  const pickers = tc._gizmo.picker.translate.children;
  const origin = tc.object.getWorldPosition(new V());
  const xs = pickers.filter((m) => m.name === "X" && m.visible).map(centreOf).sort((a, b) => b.x - a.x);
  const xy = pickers.filter((m) => m.name === "XY" && m.visible).map(centreOf)[0];
  if (xs.length < 2 || !xy) return null;
  const hit = (p) => document.elementFromPoint(p.x, p.y)?.classList.contains("viewport__canvas") ?? false;
  const o = toPage(origin), x = toPage(xs[0]), xNeg = toPage(xs[xs.length - 1]), plane = toPage(xy);
  return { origin: o, xArrow: { ...x, hit: hit(x) }, plane: { ...plane, hit: hit(plane) },
           xNeg: { ...xNeg, hit: hit(xNeg), picked: sm.pick(xNeg.ndc[0], xNeg.ndc[1]) } };
})()`;

const UI_STATE = `(() => {
  const text = (sel) => document.querySelector(sel)?.textContent?.trim() ?? null;
  const tool = (mode) => { const b = document.querySelector(\`.toolbar button[data-tool="\${mode}"]\`);
    return b ? { disabled: b.disabled, pressed: b.getAttribute("aria-pressed") === "true" } : null; };
  const placement = [...document.querySelectorAll(".pset-accordion")]
    .find((s) => s.querySelector(".pset-accordion__name")?.textContent.trim() === ${JSON.stringify(PLACEMENT)});
  const placementX = placement ? [...placement.querySelectorAll(".input-field")]
    .find((f) => f.querySelector(".input-field__label")?.textContent.trim() === "X")?.querySelector("input")?.value ?? null : null;
  return {
    select: tool("select"), move: tool("move"), rotate: tool("rotate"), draw: tool("draw"),
    status: text(".toolbar__status"), statusKind: document.querySelector(".toolbar__status")?.className ?? null,
    panelName: text(".property-panel__name"),
    panelError: text(".property-panel__error"),
    treeSelected: [...document.querySelectorAll(".model-tree__row--selected .model-tree__name")].map((e) => e.textContent.trim()),
    placementX,
  };
})()`;

const treeRow = (name) => `(() => {
  const row = [...document.querySelectorAll(".model-tree__row")]
    .find((r) => r.querySelector(".model-tree__name")?.textContent.trim() === ${JSON.stringify(name)});
  if (!row) return null;
  // Long names are clipped by the tree panel: click the first label point that really hits this row.
  for (const span of [row.querySelector(".model-tree__name"), row.querySelector(".model-tree__type")]) {
    const r = span.getBoundingClientRect();
    const point = { x: r.left + Math.min(r.width / 2, 8), y: r.top + r.height / 2 };
    if (row.contains(document.elementFromPoint(point.x, point.y))) return point;
  }
  return null;
})()`;

const toolButton = (mode) => `(() => {
  const b = document.querySelector('.toolbar button[data-tool="${mode}"]');
  if (!b) return null;
  const r = b.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, disabled: b.disabled };
})()`;

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const protectedHashBefore = sha256(PROTECTED_MODEL_JSON);
  console.log(`protected model.json sha256 before: ${protectedHashBefore}`);

  for (const [port, what] of [[BACKEND_PORT, "backend"], [VITE_PORT, "vite"], [CDP_PORT, "chrome devtools"]]) {
    if (await portInUse(port)) throw new Error(`port ${port} (${what}) is already in use -- stop the server using it first`);
  }
  for (const [file, what] of [[PYTHON, "backend venv python"], [VITE_BIN, "vite (npm install)"], [CHROME, "Chrome"]]) {
    if (path.isAbsolute(file) && !fs.existsSync(file)) throw new Error(`${what} not found at ${file}`);
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "openbim-day52-move-"));
  const modelPath = path.join(tmp, "model.json");
  const scadDir = path.join(tmp, "scad");
  fs.mkdirSync(scadDir);
  const children = [];
  let cdp = null;

  try {
    // Seed STLs exactly as wall.scad.j2 would produce them.
    for (const wall of [WALL_A, WALL_B]) {
      const scad = path.join(tmp, `${wall.guid}.scad`);
      const { Length, Height, Thickness } = wall.dims;
      fs.writeFileSync(scad, `translate([${wall.at.join(", ")}]) cube([${Length}, ${Thickness}, ${Height}]);\n`);
      const render = spawnSync(OPENSCAD, ["-o", path.join(scadDir, `${wall.guid}.stl`), scad], { encoding: "utf8" });
      if (render.status !== 0) throw new Error(`OpenSCAD seed render failed: ${render.error ?? render.stderr}`);
    }
    const wallEntity = (wall, extraPsets) => ({
      guid: wall.guid, type: "IfcWall", name: wall.name, parent_guid: STOREY.guid,
      psets: { [QTO]: { ...wall.dims }, ...extraPsets },
      geometry: { type: "stl", url: `/geometry/${wall.guid}.stl`, bounds: boundsOf(wall) },
    });
    fs.writeFileSync(modelPath, JSON.stringify({
      project: { guid: "day52-project", name: "Day 52 move test", description: "" },
      entities: [
        { guid: "day52-building-001", type: "IfcBuilding", name: "Test Building", children_guids: [STOREY.guid] },
        { guid: STOREY.guid, type: "IfcBuildingStorey", name: STOREY.name, parent_guid: "day52-building-001",
          children_guids: [WALL_A.guid, WALL_B.guid] },
        wallEntity(WALL_A, {}), // no OpenBIM_Placement: an existing wall at the origin
        wallEntity(WALL_B, { [PLACEMENT]: { X: WALL_B.at[0], Y: 0, Z: 0 } }),
      ],
    }, null, 2));

    const backendLog = [];
    const backend = spawn(PYTHON, ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", String(BACKEND_PORT)], {
      cwd: BACKEND_ROOT,
      env: { ...process.env, MODEL_JSON_PATH: modelPath, SCAD_TMP_DIR: scadDir, OPENSCAD_BINARY: OPENSCAD },
    });
    children.push(backend);
    backend.stdout.on("data", (d) => backendLog.push(String(d)));
    backend.stderr.on("data", (d) => backendLog.push(String(d)));
    const modelResponse = await waitFor(() => httpOk(`http://127.0.0.1:${BACKEND_PORT}/api/model`), "backend /api/model");
    const served = (await modelResponse.json()).entities.map((e) => e.guid);
    mustPass("S1", "backend serves the temporary model", served.includes(WALL_A.guid) && served.includes(WALL_B.guid), served.join(", "));

    const vite = spawn(process.execPath, [VITE_BIN, "--port", String(VITE_PORT), "--strictPort", "--host", "127.0.0.1"], {
      cwd: FRONTEND_ROOT, env: process.env,
    });
    children.push(vite);
    const appUrl = `http://127.0.0.1:${VITE_PORT}/`;
    await waitFor(() => httpOk(appUrl), "vite dev server");
    await waitFor(() => httpOk(`${appUrl}api/model`), "vite /api proxy");

    const chrome = spawn(CHROME, [
      "--headless=new", `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${path.join(tmp, "chrome")}`,
      "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-first-run", "--no-default-browser-check",
      `--window-size=${VIEW.width},${VIEW.height}`, "about:blank",
    ], { stdio: "ignore" });
    children.push(chrome);
    const target = await waitFor(async () => {
      const response = await httpOk(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const list = response ? await response.json() : [];
      return list.find((t) => t.type === "page") ?? null;
    }, "chrome devtools target");
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);

    const appErrors = [];
    const requests = new Map(); // requestId -> { url, method, postData, status, finished, failed }
    const expectedFailedRequestIds = new Set();
    let onFetchPaused = null;
    cdp.on((method, params) => {
      if (method === "Fetch.requestPaused") {
        if (onFetchPaused) onFetchPaused(params);
        else cdp.send("Fetch.continueRequest", { requestId: params.requestId }).catch(() => {});
      } else if (method === "Runtime.exceptionThrown") {
        appErrors.push(`exception: ${params.exceptionDetails.exception?.description ?? params.exceptionDetails.text}`);
      } else if (method === "Runtime.consoleAPICalled" && (params.type === "error" || params.type === "assert")) {
        appErrors.push(`console.${params.type}: ${params.args.map((a) => a.value ?? a.description).join(" ")}`);
      } else if (method === "Log.entryAdded" && params.entry.level === "error") {
        appErrors.push(`log: ${params.entry.text} ${params.entry.url ?? ""}`);
      } else if (method === "Network.requestWillBeSent") {
        requests.set(params.requestId, { url: params.request.url, method: params.request.method,
          postData: params.request.postData ?? null, status: null, finished: false, failed: null });
      } else if (method === "Network.responseReceived") {
        const r = requests.get(params.requestId);
        if (r) r.status = params.response.status;
      } else if (method === "Network.loadingFinished") {
        const r = requests.get(params.requestId);
        if (r) r.finished = true;
      } else if (method === "Network.loadingFailed") {
        const r = requests.get(params.requestId);
        if (r) r.failed = params.errorText;
      }
    });
    for (const domain of ["Page", "Runtime", "Network", "Log"]) await cdp.send(`${domain}.enable`);
    await cdp.send("Emulation.setDeviceMetricsOverride", { ...VIEW, deviceScaleFactor: 1, mobile: false });

    const evaluate = async (expression) => {
      const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error(`evaluate failed: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`);
      return r.result.value;
    };
    const probe = (guid = WALL_A.guid) => evaluate(probeExpression(guid));
    const ui = () => evaluate(UI_STATE);
    const propertyPosts = (fromIndex) => [...requests.entries()].slice(fromIndex)
      .filter(([, r]) => r.method === "POST" && r.url.includes("/api/properties/"));

    const mouseEvent = (type, x, y, extra = {}) => cdp.send("Input.dispatchMouseEvent", { type, x, y, ...extra });
    const click = async ({ x, y }, modifiers = 0) => {
      await mouseEvent("mouseMoved", x, y, { modifiers });
      await mouseEvent("mousePressed", x, y, { button: "left", buttons: 1, clickCount: 1, modifiers });
      await mouseEvent("mouseReleased", x, y, { button: "left", buttons: 0, clickCount: 1, modifiers });
    };
    /** Press at `from`, move in steps to `to` (calling `during` midway), release. */
    const drag = async (from, to, during = null) => {
      await mouseEvent("mouseMoved", from.x, from.y);
      await mouseEvent("mousePressed", from.x, from.y, { button: "left", buttons: 1, clickCount: 1 });
      let midway = null;
      const steps = 12;
      for (let i = 1; i <= steps; i++) {
        const x = from.x + ((to.x - from.x) * i) / steps, y = from.y + ((to.y - from.y) * i) / steps;
        await mouseEvent("mouseMoved", x, y, { button: "left", buttons: 1 });
        await sleep(16);
        if (i === steps / 2 && during) midway = await during();
      }
      await mouseEvent("mouseReleased", to.x, to.y, { button: "left", buttons: 0, clickCount: 1 });
      return midway;
    };
    const clickTree = async (name, modifiers = 0) => {
      const row = await waitFor(() => evaluate(treeRow(name)), `tree row ${name}`, 5_000);
      await click(row, modifiers);
    };
    const clickTool = async (mode) => {
      const button = await waitFor(() => evaluate(toolButton(mode)), `${mode} button`, 5_000);
      await click(button);
    };
    const waitUi = (pred, label, timeoutMs = 5_000) =>
      waitFor(async () => { const s = await ui(); return pred(s) ? s : null; }, label, timeoutMs).catch(() => null);
    const loaded = async () => {
      const [a, b] = [await probe(WALL_A.guid), await probe(WALL_B.guid)];
      return a.hasMesh && b.hasMesh && a.geometryType === "BufferGeometry" && b.geometryType === "BufferGeometry" ? [a, b] : null;
    };

    /** Drag the X arrow by `pixels` along its on-screen direction, after checking (as `tag`) that the press point is on the canvas. */
    const dragXArrow = async (tag, pixels, during = null) => {
      const h = await waitFor(() => evaluate(GIZMO_HANDLES), "gizmo handles", 5_000);
      mustPass(tag, "gizmo X arrow is on the visible canvas (checked before the drag)", h.xArrow.hit,
        `(${h.xArrow.x.toFixed(0)}, ${h.xArrow.y.toFixed(0)})`);
      const dx = h.xArrow.x - h.origin.x, dy = h.xArrow.y - h.origin.y, len = Math.hypot(dx, dy) || 1;
      const to = { x: h.xArrow.x + (dx / len) * pixels, y: h.xArrow.y + (dy / len) * pixels };
      const midway = await drag(h.xArrow, to, during);
      return { h, midway };
    };

    // ── 0. Load ──
    await cdp.send("Page.navigate", { url: appUrl });
    const [a0, b0] = await waitFor(loaded, "both seed STL meshes loaded");
    check("0", "app loads; both walls shown as real STL meshes at their world positions",
      sameBounds(a0, boundsOf(WALL_A)) && sameBounds(b0, boundsOf(WALL_B)),
      `A ${JSON.stringify(a0.min)}..${JSON.stringify(a0.max)}, B ${JSON.stringify(b0.min)}..${JSON.stringify(b0.max)}`);

    // ── T. Move availability ──
    let s = await ui();
    check("T1", "no selection: Move disabled, Select active, Rotate/Draw still disabled",
      s.move?.disabled && s.select?.pressed && !s.move.pressed && s.rotate?.disabled && s.draw?.disabled, JSON.stringify(s.move));

    await clickTree(STOREY.name);
    s = await waitUi((x) => x.panelName === STOREY.name, "storey selected");
    const t2 = s ?? (await ui());
    check("T2", "a storey (not a wall) selected: Move disabled", s && s.move.disabled,
      `panel "${t2.panelName}", tree ${JSON.stringify(t2.treeSelected)}, move ${JSON.stringify(t2.move)}`);

    await clickTree(WALL_A.name);
    s = await waitUi((x) => x.panelName === WALL_A.name && !x.move.disabled, "wall A selected, Move enabled");
    check("T3", "one wall selected: Move enabled (not yet active)", s && !s.move.pressed, JSON.stringify(s?.move));

    await clickTool("move");
    s = await waitUi((x) => x.move.pressed && x.status, "Move active");
    let p = await waitFor(async () => { const q = await probe(); return q.gizmo.attachedToCurrentMesh ? q : null; }, "gizmo attached", 5_000).catch(() => probe());
    check("T4", "Move activates: button pressed, hint shown, gizmo attached to the selected wall's mesh",
      s && !s.select.pressed && /red \(X\) or green \(Y\)/.test(s.status) && p.gizmo.attachedToCurrentMesh && p.gizmo.visible,
      `status "${s?.status}", gizmo on ${p.gizmo.attachedTo}`);

    await clickTree(WALL_B.name, 2 /* Ctrl */);
    s = await waitUi((x) => x.treeSelected.length === 2 && x.move.disabled && x.select.pressed, "two walls selected");
    p = await probe();
    check("T5", "two walls selected: Move disabled, tool falls back to Select, gizmo hidden",
      s && !s.move.pressed && p.gizmo.attachedTo === null && !p.gizmo.visible, `tree ${JSON.stringify(s?.treeSelected)}`);

    await clickTree(WALL_A.name);
    await waitUi((x) => x.panelName === WALL_A.name && x.treeSelected.length === 1 && !x.move.disabled, "wall A alone");
    await clickTool("move");
    await waitFor(async () => (await probe()).gizmo.attachedToCurrentMesh, "gizmo re-attached", 5_000);
    await sleep(300); // a few frames so the gizmo's handle matrices are current

    // ── D. Real X-axis drag ──
    const placementX = () => JSON.parse(fs.readFileSync(modelPath, "utf8")).entities.find((e) => e.guid === WALL_A.guid).psets[PLACEMENT]?.X;
    let requestIndex = requests.size;
    const beforeDrag = await probe();
    const { midway } = await dragXArrow("D0", 120, () => probe());
    check("D1", "during the drag: gizmo dragging, camera orbit disabled, wall preview moved along X only",
      midway && midway.gizmo.dragging && midway.orbitEnabled === false
        && !near(midway.min[0], beforeDrag.min[0], 0.5) && near(midway.min[1], beforeDrag.min[1]) && near(midway.min[2], beforeDrag.min[2]),
      midway ? `orbit ${midway.orbitEnabled}, preview min ${JSON.stringify(midway.min)}` : "no midway probe");

    const post = await waitFor(() => propertyPosts(requestIndex).find(([, r]) => r.finished || r.failed) ?? null, "move POST", 30_000);
    await sleep(500); // any second (unexpected) request would have been sent by now
    const posts = propertyPosts(requestIndex);
    const sent = JSON.parse(post[1].postData ?? "{}");
    const movedX = sent.value;
    check("D2", "one property edit submitted: OpenBIM_Placement.X, whole millimetres, non-zero",
      posts.length === 1 && post[1].url.endsWith(`/api/properties/${WALL_A.guid}`) && sent.pset_name === PLACEMENT
        && sent.property_name === "X" && Number.isInteger(movedX) && movedX !== 0,
      `${posts.length} POST(s), body ${post[1].postData}`);

    const body = JSON.parse((await cdp.send("Network.getResponseBody", { requestId: post[0] })).body);
    const movedBounds = boundsOf(WALL_A, [movedX, 0, 0]);
    check("D3", "backend regenerates the wall at the new position (200, new STL url, world bounds)",
      post[1].status === 200 && body.success === true && body.entity.psets[PLACEMENT]?.X === movedX
        && JSON.stringify(body.entity.psets[QTO]) === JSON.stringify(WALL_A.dims)
        && body.entity.geometry.url !== `/geometry/${WALL_A.guid}.stl`
        && JSON.stringify(body.entity.geometry.bounds) === JSON.stringify(movedBounds),
      `X ${movedX}, bounds ${JSON.stringify(body.entity.geometry.bounds)}`);

    const stlUrl = body.entity.geometry.url;
    const stlReq = await waitFor(() => [...requests.values()].slice(requestIndex).find((r) => r.url.endsWith(stlUrl) && (r.finished || r.failed)) ?? null,
      "new STL fetched", 15_000).catch(() => null);
    const afterMove = await waitFor(async () => {
      const q = await probe();
      return q.hasMesh && q.uuid !== beforeDrag.uuid && q.geometryType === "BufferGeometry" && sameBounds(q, movedBounds) ? q : null;
    }, "moved STL mesh shown", 15_000).catch(() => probe());
    check("D4", "viewport shows the regenerated STL at the new world position, still selected, gizmo back on it",
      stlReq?.status === 200 && afterMove.geometryType === "BufferGeometry" && sameBounds(afterMove, movedBounds)
        && afterMove.color === SELECTED_HEX && afterMove.meshCount === 2 && afterMove.gizmo.attachedToCurrentMesh,
      `${afterMove.geometryType} ${JSON.stringify(afterMove.min)}..${JSON.stringify(afterMove.max)}, colour #${afterMove.color}`);

    s = await waitUi((x) => /^Moved: X = /.test(x.status ?? "") && x.placementX === String(movedX), "UI settled after move", 10_000);
    check("D5", "selection kept (gizmo drag was not a click); toolbar confirms; Properties panel shows the new X; temp model persisted",
      s && s.panelName === WALL_A.name && JSON.stringify(s.treeSelected) === JSON.stringify([WALL_A.name]) && !s.panelError
        && placementX() === movedX,
      `status "${s?.status}", panel X ${s?.placementX}, persisted X ${placementX()}`);
    const otherWall = await probe(WALL_B.guid);
    check("D6", "the other wall did not move", sameBounds(otherWall, boundsOf(WALL_B)), JSON.stringify(otherWall.min));

    // ── G. Stationary click on the gizmo where no wall is picked ──
    // Without Viewport's isMoveDragging() guard this click would resolve to
    // "clear" (empty space) and drop the selection.
    await sleep(300);
    requestIndex = requests.size;
    const persistedBeforeClick = fs.readFileSync(modelPath);
    const hg = await waitFor(() => evaluate(GIZMO_HANDLES), "gizmo handles", 5_000);
    mustPass("G0", "gizmo -X arrow is on the visible canvas over empty space (a selection click there would pick nothing)",
      hg.xNeg.hit && hg.xNeg.picked === null, `(${hg.xNeg.x.toFixed(0)}, ${hg.xNeg.y.toFixed(0)}) picks ${hg.xNeg.picked}`);
    await mouseEvent("mouseMoved", hg.xNeg.x, hg.xNeg.y);
    await mouseEvent("mousePressed", hg.xNeg.x, hg.xNeg.y, { button: "left", buttons: 1, clickCount: 1 });
    const pressed = await probe();
    await mouseEvent("mouseReleased", hg.xNeg.x, hg.xNeg.y, { button: "left", buttons: 0, clickCount: 1 });
    await sleep(500);
    s = await ui();
    p = await probe();
    check("G1", "stationary click on the gizmo: gizmo engaged, selection kept, no POST, wall unchanged",
      pressed.gizmo.dragging && s.panelName === WALL_A.name && JSON.stringify(s.treeSelected) === JSON.stringify([WALL_A.name])
        && propertyPosts(requestIndex).length === 0 && sameBounds(p, movedBounds) && p.color === SELECTED_HEX
        && p.gizmo.attachedToCurrentMesh && p.orbitEnabled === true && fs.readFileSync(modelPath).equals(persistedBeforeClick),
      `engaged ${pressed.gizmo.dragging}, tree ${JSON.stringify(s.treeSelected)}, ${propertyPosts(requestIndex).length} POST(s)`);

    // ── P. XY-plane drag is rejected ──
    await sleep(300);
    requestIndex = requests.size;
    const persistedBeforePlane = fs.readFileSync(modelPath);
    const hp = await waitFor(() => evaluate(GIZMO_HANDLES), "gizmo handles", 5_000);
    mustPass("P0", "gizmo XY-plane handle is on the visible canvas", hp.plane.hit, `(${hp.plane.x.toFixed(0)}, ${hp.plane.y.toFixed(0)})`);
    await drag(hp.plane, { x: hp.plane.x + 90, y: hp.plane.y + 40 });
    s = await waitUi((x) => /one axis at a time/.test(x.status ?? ""), "plane rejection message");
    await sleep(500);
    p = await probe();
    check("P1", "XY-plane drag: brief message, no request, wall stays put, still selected",
      s && /error/.test(s.statusKind) && propertyPosts(requestIndex).length === 0 && sameBounds(p, movedBounds)
        && fs.readFileSync(modelPath).equals(persistedBeforePlane) && s.panelName === WALL_A.name && p.orbitEnabled === true,
      `status "${s?.status}", ${propertyPosts(requestIndex).length} POST(s), min ${JSON.stringify(p.min)}`);

    // ── F. Failed save reverts (INJECTED 502: backend not called) ──
    requestIndex = requests.size;
    const errorCount = appErrors.length;
    const persistedBeforeFail = fs.readFileSync(modelPath);
    onFetchPaused = (params) => cdp.send("Fetch.fulfillRequest", {
      requestId: params.requestId, responseCode: 502,
      responseHeaders: [{ name: "Content-Type", value: "application/json" }],
      body: Buffer.from(JSON.stringify({ detail: {
        success: false, error: "Geometry could not be regenerated; the change was not saved.",
        detail: "OpenSCAD did not produce new geometry", code: "geometry_regeneration_failed",
      } })).toString("base64"),
    }).catch(() => {});
    await cdp.send("Fetch.enable", { patterns: [{ urlPattern: `*/api/properties/${WALL_A.guid}`, requestStage: "Request" }] });
    await dragXArrow("F0", 80);
    const failedPost = await waitFor(() => propertyPosts(requestIndex).find(([, r]) => r.finished || r.failed) ?? null, "failing POST", 30_000);
    await cdp.send("Fetch.disable");
    onFetchPaused = null;
    expectedFailedRequestIds.add(failedPost[0]);
    s = await waitUi((x) => /^Move not saved: Geometry could not be regenerated/.test(x.status ?? ""), "failure message", 15_000);
    const reverted = await waitFor(async () => {
      const q = await probe();
      return q.geometryType === "BufferGeometry" && sameBounds(q, movedBounds) && q.gizmo.attachedToCurrentMesh ? q : null;
    }, "committed mesh back", 15_000).catch(() => probe());
    const ui2 = await ui();
    check("F1", "INJECTED HTTP 502 on a move (frontend handling only): error shown, wall back at its saved position, X unchanged",
      failedPost[1].status === 502 && s && sameBounds(reverted, movedBounds) && reverted.color === SELECTED_HEX
        && ui2.placementX === String(movedX) && ui2.panelName === WALL_A.name
        && fs.readFileSync(modelPath).equals(persistedBeforeFail),
      `status "${s?.status}", min ${JSON.stringify(reverted.min)}, panel X ${ui2.placementX}`);
    const newErrors = appErrors.splice(errorCount);
    const unexpected = newErrors.filter((e) => !/Property edit failed|status of 502/.test(e));
    check("F2", "only the expected rejection errors were logged", unexpected.length === 0, unexpected.join(" | "));

    // ── R. Reload keeps the position ──
    await cdp.send("Page.reload", { ignoreCache: true });
    const [aR, bR] = await waitFor(async () => {
      const both = await loaded();
      return both && sameBounds(both[0], movedBounds) ? both : null;
    }, "meshes after reload", 30_000).catch(async () => [await probe(WALL_A.guid), await probe(WALL_B.guid)]);
    const served2 = await (await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/model`)).json();
    const servedA = served2.entities.find((e) => e.guid === WALL_A.guid);
    check("R1", "after a page reload the wall is drawn at the moved position (STL), as served and persisted",
      aR.geometryType === "BufferGeometry" && sameBounds(aR, movedBounds) && sameBounds(bR, boundsOf(WALL_B))
        && servedA.psets[PLACEMENT]?.X === movedX && servedA.geometry.url === stlUrl && placementX() === movedX,
      `A ${JSON.stringify(aR.min)}..${JSON.stringify(aR.max)}, served X ${servedA.psets[PLACEMENT]?.X}`);
    s = await ui();
    check("R2", "after reload: nothing selected, Select active, Move disabled", s.select.pressed && s.move.disabled && !s.panelName);

    // ── Q. Select mode still works (viewport clicks) ──
    await clickTree(WALL_A.name);
    s = await waitUi((x) => x.panelName === WALL_A.name && !x.move.disabled && x.placementX !== null, "wall A selected after reload");
    check("R3", "after reload, selecting the wall shows the persisted OpenBIM_Placement X in the Properties panel",
      s && s.placementX === String(movedX), `panel X ${s?.placementX}, persisted X ${movedX}`);
    await clickTool("move");
    await waitFor(async () => (await probe()).gizmo.attachedToCurrentMesh, "gizmo attached after reload", 5_000);
    await clickTool("select");
    s = await waitUi((x) => x.select.pressed && !x.move.pressed, "back to Select");
    p = await probe();
    check("Q1", "switching back to Select hides the gizmo", s && p.gizmo.attachedTo === null && !p.gizmo.visible);

    // Click wall B where it is drawn: project its centre (it is unselected and not occluded by A at this angle).
    const centreB = boundsOf(WALL_B).min.map((v, i) => (v + boundsOf(WALL_B).max[i]) / 2);
    const pointB = await evaluate(`(() => { const sm = ${FIND_SCENE_MANAGER}; const V = sm.camera.position.constructor;
      const n = new V(${centreB.join(", ")}).project(sm.camera); const r = document.querySelector(".viewport__canvas").getBoundingClientRect();
      const x = r.left + (n.x + 1) / 2 * r.width, y = r.top + (1 - n.y) / 2 * r.height;
      return { x, y, hit: document.elementFromPoint(x, y)?.classList.contains("viewport__canvas") ?? false,
               picked: sm.pick(n.x, n.y) }; })()`);
    mustPass("Q2a", "wall B's centre is on the visible canvas and picks wall B", pointB.hit && pointB.picked === WALL_B.guid,
      `(${pointB.x.toFixed(0)}, ${pointB.y.toFixed(0)}) picks ${pointB.picked}`);
    await click(pointB);
    s = await waitUi((x) => x.panelName === WALL_B.name, "wall B selected by viewport click");
    const pB = await probe(WALL_B.guid);
    check("Q2", "Select mode: clicking a wall in the viewport selects it", s && pB.color === SELECTED_HEX
      && JSON.stringify(s.treeSelected) === JSON.stringify([WALL_B.name]), JSON.stringify(s?.treeSelected));

    // ── K. No unexpected errors ──
    const httpErrors = [...requests.entries()]
      .filter(([id]) => !expectedFailedRequestIds.has(id)).map(([, r]) => r)
      .filter((r) => ((r.status !== null && r.status >= 400) || r.failed) && !r.url.includes("/favicon.ico"));
    const unexpectedAppErrors = appErrors.filter((e) => !e.includes("/favicon.ico"));
    const backendErrors = backendLog.join("").split(/\r?\n/).filter((l) => /Traceback|^ERROR|\bERROR:/.test(l));
    check("K", "no unexpected application errors (console, exceptions, HTTP, backend log)",
      unexpectedAppErrors.length === 0 && httpErrors.length === 0 && backendErrors.length === 0,
      [...unexpectedAppErrors, ...httpErrors.map((r) => `${r.method} ${r.url} ${r.status ?? r.failed}`), ...backendErrors].join(" | "));
  } finally {
    if (cdp) cdp.close();
    for (const child of children.reverse()) killTree(child);
    await sleep(500);
    if (process.env.E2E_KEEP_TMP) console.log(`temporary directory kept: ${tmp}`);
    else fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    const protectedHashAfter = sha256(PROTECTED_MODEL_JSON);
    check("M", "backend/data/model.json untouched (sha256 unchanged)", protectedHashAfter === protectedHashBefore, protectedHashAfter);
  }
}

main()
  .catch((err) => {
    console.error(`ERROR: ${err.message}`);
    results.push({ id: "run", ok: false });
  })
  .finally(() => {
    const failed = results.filter((r) => !r.ok).length;
    console.log(`\n${results.length - failed}/${results.length} checks passed`);
    process.exitCode = failed === 0 ? 0 : 1;
  });
