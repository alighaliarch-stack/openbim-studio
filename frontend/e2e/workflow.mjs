/**
 * e2e/workflow.mjs
 *
 * Day-25 minimal repeatable workflow test (browser half).
 *
 * Drives the real application in headless Chrome:
 *   select wall -> edit Length / Height / Thickness -> save -> OpenSCAD
 *   regeneration -> new STL -> viewport mesh replacement -> selection
 *   highlight preserved -> double-click focus
 * and repeats the Length -> Height -> Thickness sequence twice.
 *
 * Isolation: starts its own backend with MODEL_JSON_PATH / SCAD_TMP_DIR
 * pointing at a temporary directory seeded with a realistic wall
 * (5000 x 3000 x 200 mm), so backend/data/model.json is never read or
 * written. Its sha256 is checked before and after the run.
 *
 * Zero dependencies: Node >= 22 (global WebSocket/fetch), the repo's
 * backend venv, the frontend's installed Vite, Chrome, and OpenSCAD.
 * Chrome is driven over the raw DevTools Protocol.
 *
 * Run from frontend/ (backend dev server on :8000 must NOT be running --
 * vite.config.ts proxies to :8000, so this test owns that port):
 *     node e2e/workflow.mjs
 *
 * Optional env: CHROME_PATH, OPENSCAD_BINARY, BACKEND_PYTHON,
 * E2E_VITE_PORT (default 5199), E2E_CDP_PORT (default 9333),
 * E2E_KEEP_TMP=1 (keep the temporary directory for inspection),
 * E2E_SCREENSHOT_DIR=<dir> (save each analysed canvas screenshot there).
 *
 * Observation points, preferring user-facing behaviour:
 *   - DOM: Properties panel header/inputs/status, Model Tree selection.
 *   - Network (CDP Network domain): the POST /api/properties response
 *     body and the browser's GET of the new /geometry/<uuid>.stl.
 *   - Pixels (screenshots of the canvas): the wall is drawn in the
 *     selection colour, and after double-click it is centred in view.
 *   - Console/exception/log events for unexpected errors.
 *
 * React/Three internals accessed (read-only), and why: whether the
 * viewport shows the real STL mesh or the bounding-box placeholder is not
 * observable from outside -- for a wall both are identical boxes -- and
 * the app exposes no other observation point. So, as in the Day 22-24
 * validation, the test reaches Viewport's SceneManager through React's
 * fiber on the <canvas> (`canvas.__reactFiber$*` -> nearest `Viewport`
 * fiber -> its hook list -> the useRef whose `.current` has
 * `meshesByGuid`) and reads only:
 *   meshesByGuid.get(guid).mesh -> geometry.type ("BufferGeometry" for
 *     STLLoader output vs "BoxGeometry" for the placeholder), its vertex
 *     count and bounding box (computeBoundingBox() on the geometry), the
 *     mesh uuid (to prove replacement) and material colour;
 *   highlightByGuid.get(guid), scene.getObjectByProperty("uuid", old)
 *     (old mesh removed), meshesByGuid.size, controls.target.
 * Nothing in the application is modified or depends on this test.
 */

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(FRONTEND_ROOT, "..");
const BACKEND_ROOT = path.join(REPO_ROOT, "backend");
const PROTECTED_MODEL_JSON = path.join(BACKEND_ROOT, "data", "model.json");
const IS_WINDOWS = process.platform === "win32";

const BACKEND_PORT = 8000; // fixed: vite.config.ts proxies /api and /geometry here
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

const WALL_GUID = "day25-wall-001";
const WALL_NAME = "Test Wall";
const QTO = "Qto_WallBaseQuantities";
const SEED = { Length: 5000, Height: 3000, Thickness: 200 };
const SEED_STL = "day25-seed.stl";
const EDIT_ROUNDS = [
  [["Length", 6000], ["Height", 3200], ["Thickness", 250]],
  [["Length", 4500], ["Height", 2800], ["Thickness", 300]],
];
const SELECTED_HEX = "ff8c1a"; // SceneManager.applyHighlight "selected"

// ── Result recording ─────────────────────────────────────────────────────────

const results = [];

function check(id, description, ok, detail = "") {
  results.push({ id, description, ok: Boolean(ok) });
  console.log(`${ok ? "PASS" : "FAIL"} [${id}] ${description}${detail ? ` -- ${detail}` : ""}`);
  return Boolean(ok);
}

/** A check the rest of the run depends on: record it, then abort if it failed. */
function mustPass(id, description, ok, detail = "") {
  if (!check(id, description, ok, detail)) throw new Error(`prerequisite failed: [${id}] ${description}`);
}

// ── Small helpers ────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

function sha256(file) {
  return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

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

const near = (a, b, tol) => Math.abs(a - b) <= tol;
/** Wall axis convention (wall.scad.j2): X=Length, Y=Thickness, Z=Height. */
const dimsOf = (q) => [q.Length, q.Thickness, q.Height];

// ── Minimal CDP client ───────────────────────────────────────────────────────

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

// ── PNG decoding (for canvas screenshots) ────────────────────────────────────

function decodePng(buffer) {
  let pos = 8;
  let width = 0, height = 0, bitDepth = 0, colorType = 0;
  const idat = [];
  while (pos < buffer.length) {
    const length = buffer.readUInt32BE(pos);
    const type = buffer.toString("ascii", pos + 4, pos + 8);
    const data = buffer.subarray(pos + 8, pos + 8 + length);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
      if (data[12] !== 0) throw new Error("interlaced PNG not supported");
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      break;
    }
    pos += 12 + length;
  }
  if (bitDepth !== 8 || (colorType !== 2 && colorType !== 6)) {
    throw new Error(`unsupported PNG format (bitDepth ${bitDepth}, colorType ${colorType})`);
  }
  const bpp = colorType === 6 ? 4 : 3;
  const stride = width * bpp;
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const out = Buffer.alloc(height * stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const cur = out.subarray(y * stride, (y + 1) * stride);
    const prev = y > 0 ? out.subarray((y - 1) * stride, y * stride) : null;
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? cur[i - bpp] : 0;
      const b = prev ? prev[i] : 0;
      const c = prev && i >= bpp ? prev[i - bpp] : 0;
      let value = line[i];
      if (filter === 1) value += a;
      else if (filter === 2) value += b;
      else if (filter === 3) value += (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        value += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      } else if (filter !== 0) {
        throw new Error(`bad PNG filter ${filter}`);
      }
      cur[i] = value & 255;
    }
  }
  return { width, height, bpp, data: out };
}

/**
 * Pixel classes, by hue so every face counts: the HemisphereLight lights
 * from +Y, so in this Z-up scene the wall face turned to the default
 * camera gets the dark ground colour, not the bright sky colour.
 * Background (0xf0f0f0) and grid lines are neutral grey and match neither.
 *   selected:   0xff8c1a orange/brown -- red >= green >= blue, clearly warm
 *   unselected: 0xb0b8c1 grey-blue    -- blue >= green, blue above red
 */
const isSelectedPixel = (r, g, b) => r - b >= 25 && r >= g && g >= b;
const isUnselectedWallPixel = (r, g, b) => b - r >= 4 && b >= g && r < 235;

/** Chrome requests /favicon.ico by itself; the app ships none. Not an application request. */
const isBrowserFaviconNoise = (text) => text.includes("/favicon.ico");

/** Count, centroid and the matching pixel nearest the centroid (canvas-local px). */
function pixelStats(image, predicate) {
  const matches = [];
  let sumX = 0, sumY = 0;
  for (let y = 0; y < image.height; y++) {
    for (let x = 0; x < image.width; x++) {
      const i = (y * image.width + x) * image.bpp;
      if (predicate(image.data[i], image.data[i + 1], image.data[i + 2])) {
        matches.push([x, y]);
        sumX += x;
        sumY += y;
      }
    }
  }
  if (matches.length === 0) return { count: 0 };
  const cx = sumX / matches.length, cy = sumY / matches.length;
  let best = matches[0], bestD = Infinity;
  for (const [x, y] of matches) {
    const d = (x - cx) ** 2 + (y - cy) ** 2;
    if (d < bestD) { bestD = d; best = [x, y]; }
  }
  return { count: matches.length, cx, cy, pick: best };
}

// ── In-page expressions ──────────────────────────────────────────────────────

/** Read-only route to Viewport's SceneManager (see header comment). */
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

const probeExpression = (guid, oldUuid) => `(() => {
  const sm = ${FIND_SCENE_MANAGER};
  if (!sm) return { found: false };
  const entry = sm.meshesByGuid.get(${JSON.stringify(guid)});
  if (!entry) return { found: true, hasMesh: false };
  const mesh = entry.mesh;
  mesh.geometry.computeBoundingBox();
  const bb = mesh.geometry.boundingBox;
  const p = mesh.position;
  return {
    found: true,
    hasMesh: true,
    uuid: mesh.uuid,
    geometryType: mesh.geometry.type,
    vertexCount: mesh.geometry.attributes.position.count,
    min: [bb.min.x + p.x, bb.min.y + p.y, bb.min.z + p.z],
    max: [bb.max.x + p.x, bb.max.y + p.y, bb.max.z + p.z],
    inScene: mesh.parent === sm.scene,
    color: mesh.material.color.getHexString(),
    highlight: sm.highlightByGuid.get(${JSON.stringify(guid)}) ?? "none",
    oldMeshStillInScene: ${oldUuid ? `Boolean(sm.scene.getObjectByProperty("uuid", ${JSON.stringify(oldUuid)}))` : "false"},
    meshCount: sm.meshesByGuid.size,
    target: sm.controls ? sm.controls.target.toArray() : null,
  };
})()`;

const PANEL_STATE = `(() => {
  const text = (sel) => document.querySelector(sel)?.textContent?.trim() ?? null;
  const selectedRows = [...document.querySelectorAll(".model-tree__row--selected .model-tree__name")].map((e) => e.textContent.trim());
  return {
    name: text(".property-panel__name"),
    type: text(".property-panel__type"),
    saving: Boolean(document.querySelector(".property-panel__saving")),
    error: text(".property-panel__error"),
    geometryWarning: text(".input-field__geometry-warning"),
    validationError: text(".input-field__validation-error"),
    treeSelected: selectedRows,
  };
})()`;

const fieldExpression = (label) => `(() => {
  const field = [...document.querySelectorAll(".property-panel .input-field")]
    .find((f) => f.querySelector(".input-field__label")?.textContent.trim() === ${JSON.stringify(label)});
  const input = field?.querySelector("input.input-field__number");
  if (!input) return null;
  input.scrollIntoView({ block: "center" });
  const r = input.getBoundingClientRect();
  return {
    x: r.left + r.width / 2,
    y: r.top + r.height / 2,
    value: input.value,
    disabled: input.disabled,
    success: Boolean(field.querySelector(".input-field__status--success")),
    hit: document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === input,
  };
})()`;

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const protectedHashBefore = sha256(PROTECTED_MODEL_JSON);
  console.log(`protected model.json sha256 before: ${protectedHashBefore}`);

  for (const [port, what] of [[BACKEND_PORT, "backend"], [VITE_PORT, "vite"], [CDP_PORT, "chrome devtools"]]) {
    if (await portInUse(port)) {
      throw new Error(`port ${port} (${what}) is already in use -- stop the dev server using it first`);
    }
  }
  for (const [file, what] of [[PYTHON, "backend venv python"], [VITE_BIN, "vite (npm install)"], [CHROME, "Chrome"]]) {
    if (path.isAbsolute(file) && !fs.existsSync(file)) throw new Error(`${what} not found at ${file}`);
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "openbim-day25-e2e-"));
  const modelPath = path.join(tmp, "model.json");
  const scadDir = path.join(tmp, "scad");
  fs.mkdirSync(scadDir);
  const children = [];
  let cdp = null;

  try {
    // Seed a realistic wall whose starting geometry is already a real STL.
    const seedScad = path.join(tmp, "seed.scad");
    fs.writeFileSync(seedScad, `cube([${dimsOf(SEED).join(", ")}]);\n`);
    const render = spawnSync(OPENSCAD, ["-o", path.join(scadDir, SEED_STL), seedScad], { encoding: "utf8" });
    if (render.status !== 0) throw new Error(`OpenSCAD seed render failed: ${render.error ?? render.stderr}`);

    fs.writeFileSync(modelPath, JSON.stringify({
      project: { guid: "day25-project", name: "Day 25 workflow test", description: "" },
      entities: [
        { guid: "day25-building-001", type: "IfcBuilding", name: "Test Building", children_guids: ["day25-storey-001"] },
        {
          guid: "day25-storey-001", type: "IfcBuildingStorey", name: "Ground Floor",
          parent_guid: "day25-building-001", children_guids: [WALL_GUID],
        },
        {
          guid: WALL_GUID, type: "IfcWall", name: WALL_NAME, parent_guid: "day25-storey-001",
          psets: { [QTO]: { ...SEED } },
          geometry: { type: "stl", url: `/geometry/${SEED_STL}`, bounds: { min: [0, 0, 0], max: dimsOf(SEED) } },
        },
      ],
    }, null, 2));

    // Backend on the temporary model.
    const backendLog = [];
    const backend = spawn(PYTHON, ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", String(BACKEND_PORT)], {
      cwd: BACKEND_ROOT,
      env: { ...process.env, MODEL_JSON_PATH: modelPath, SCAD_TMP_DIR: scadDir, OPENSCAD_BINARY: OPENSCAD },
    });
    children.push(backend);
    backend.stdout.on("data", (d) => backendLog.push(String(d)));
    backend.stderr.on("data", (d) => backendLog.push(String(d)));

    const modelResponse = await waitFor(() => httpOk(`http://127.0.0.1:${BACKEND_PORT}/api/model`), "backend /api/model");
    const served = (await modelResponse.json()).entities.map((e) => e.guid).sort();
    mustPass("S1", "backend serves the temporary model (not backend/data/model.json)",
      JSON.stringify(served) === JSON.stringify(["day25-building-001", "day25-storey-001", WALL_GUID]),
      served.join(", "));

    // Vite dev server (proxies /api and /geometry to the backend above).
    const vite = spawn(process.execPath, [VITE_BIN, "--port", String(VITE_PORT), "--strictPort", "--host", "127.0.0.1"], {
      cwd: FRONTEND_ROOT,
      env: process.env,
    });
    children.push(vite);
    const appUrl = `http://127.0.0.1:${VITE_PORT}/`;
    await waitFor(() => httpOk(appUrl), "vite dev server");
    const proxied = await waitFor(() => httpOk(`${appUrl}api/model`), "vite /api proxy");
    mustPass("S2", "vite proxy reaches the temporary-model backend",
      (await proxied.json()).entities.some((e) => e.guid === WALL_GUID));

    // Headless Chrome.
    const chrome = spawn(CHROME, [
      "--headless=new", `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${path.join(tmp, "chrome")}`,
      "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-first-run", "--no-default-browser-check",
      `--window-size=${VIEW.width},${VIEW.height}`, "about:blank",
    ], { stdio: "ignore" });
    children.push(chrome);
    const targets = await waitFor(async () => {
      const response = await httpOk(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const list = response ? await response.json() : [];
      return list.find((t) => t.type === "page") ?? null;
    }, "chrome devtools target");
    cdp = await Cdp.connect(targets.webSocketDebuggerUrl);

    // Error and network recording.
    const appErrors = [];
    const requests = new Map(); // requestId -> { url, method, status, finished, failed }
    cdp.on((method, params) => {
      if (method === "Runtime.exceptionThrown") {
        appErrors.push(`exception: ${params.exceptionDetails.exception?.description ?? params.exceptionDetails.text}`);
      } else if (method === "Runtime.consoleAPICalled" && (params.type === "error" || params.type === "assert")) {
        appErrors.push(`console.${params.type}: ${params.args.map((a) => a.value ?? a.description).join(" ")}`);
      } else if (method === "Log.entryAdded" && params.entry.level === "error") {
        appErrors.push(`log: ${params.entry.text} ${params.entry.url ?? ""}`);
      } else if (method === "Network.requestWillBeSent") {
        requests.set(params.requestId, { url: params.request.url, method: params.request.method, status: null, finished: false, failed: null });
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
    const probe = (oldUuid) => evaluate(probeExpression(WALL_GUID, oldUuid));
    const panel = () => evaluate(PANEL_STATE);
    const findRequest = (pred) => [...requests.entries()].find(([, r]) => pred(r)) ?? null;

    const mouse = async (type, x, y, clickCount = 1) =>
      cdp.send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mousePressed" ? 1 : 0, clickCount });
    const click = async (x, y, clickCount = 1) => {
      await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
      for (let c = 1; c <= clickCount; c++) {
        await mouse("mousePressed", x, y, c);
        await mouse("mouseReleased", x, y, c);
      }
    };
    const key = (key, code, windowsVirtualKeyCode, extra = {}) =>
      cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode, ...extra })
        .then(() => cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode }));

    const canvasRect = () => evaluate(`(() => { const r = document.querySelector(".viewport__canvas").getBoundingClientRect();
      return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
    let screenshotIndex = 0;
    const screenshotCanvas = async (label) => {
      const rect = await canvasRect();
      const shot = await cdp.send("Page.captureScreenshot", {
        format: "png", clip: { x: rect.x, y: rect.y, width: rect.w, height: rect.h, scale: 1 },
      });
      const png = Buffer.from(shot.data, "base64");
      if (process.env.E2E_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.E2E_SCREENSHOT_DIR, { recursive: true });
        const name = `${String(++screenshotIndex).padStart(2, "0")}-${label.replace(/[^\w=-]+/g, "_")}.png`;
        fs.writeFileSync(path.join(process.env.E2E_SCREENSHOT_DIR, name), png);
      }
      return { rect, image: decodePng(png) };
    };
    /** Page coords of a canvas-local pixel, verified to hit the canvas itself (not a panel). */
    const canvasPoint = async (rect, [px, py]) => {
      const x = rect.x + px + 0.5, y = rect.y + py + 0.5;
      const hit = await evaluate(`document.elementFromPoint(${x}, ${y})?.classList.contains("viewport__canvas") ?? false`);
      return { x, y, hit };
    };

    // ── Load ──
    await cdp.send("Page.navigate", { url: appUrl });
    await waitFor(async () => {
      const p = await probe();
      return p.hasMesh && p.geometryType === "BufferGeometry";
    }, "seed STL mesh loaded in the viewport");
    await sleep(300);
    const seedReq = findRequest((r) => r.url.endsWith(`/geometry/${SEED_STL}`));
    const layout = await evaluate(`(() => { const r = document.querySelector(".viewport__canvas").getBoundingClientRect();
      return \`canvas \${r.width}x\${r.height} at (\${r.left}, \${r.top}), window \${innerWidth}x\${innerHeight}\`; })()`);
    check("0", "app loads; seed STL fetched and shown as a real STL mesh",
      seedReq && seedReq[1].status === 200 && seedReq[1].finished, layout);

    // ── A. Select the wall by clicking where it is drawn ──
    let { rect, image } = await screenshotCanvas("before-select");
    const unselected = pixelStats(image, isUnselectedWallPixel);
    const orangeBeforeSelect = pixelStats(image, isSelectedPixel);
    mustPass("A0", "wall visible on canvas before selection (unselected colour, no selection colour)",
      unselected.count > 500 && orangeBeforeSelect.count < 50,
      `unselected ${unselected.count} px, selected ${orangeBeforeSelect.count} px`);
    const selectPoint = await canvasPoint(rect, unselected.pick);
    mustPass("A1", "click point is on the visible canvas", selectPoint.hit, `(${selectPoint.x.toFixed(0)}, ${selectPoint.y.toFixed(0)})`);
    await click(selectPoint.x, selectPoint.y);
    const selectedPanel = await waitFor(async () => {
      const p = await panel();
      return p.name === WALL_NAME ? p : null;
    }, "properties panel shows the wall", 5_000).catch(() => null);
    mustPass("A", "clicking the wall selects it (Properties panel + Model Tree)",
      selectedPanel && selectedPanel.type === "IfcWall" && selectedPanel.treeSelected.includes(WALL_NAME),
      JSON.stringify(selectedPanel?.treeSelected));
    await sleep(200);
    ({ image } = await screenshotCanvas("after-select"));
    const selectedPx = pixelStats(image, isSelectedPixel);
    const leftoverPx = pixelStats(image, isUnselectedWallPixel);
    check("A2", "wall rendered in the selection colour", selectedPx.count > 500 && leftoverPx.count < 50,
      `selected ${selectedPx.count} px, unselected ${leftoverPx.count} px`);

    // ── Edit rounds ──
    const expected = { ...SEED };
    const seenUrls = new Set([`/geometry/${SEED_STL}`]);
    let currentUrl = `/geometry/${SEED_STL}`;
    let editsPassed = 0;

    for (const [roundIndex, edits] of EDIT_ROUNDS.entries()) {
      const round = roundIndex + 1;
      for (const [prop, value] of edits) {
        const tag = `R${round} ${prop}=${value}`;
        const before = await probe();
        const requestCount = requests.size;
        const hasNewRequest = (pred) => [...requests.values()].slice(requestCount).find(pred) ?? null;

        // B. Edit the field like a user: click, select all, type, Enter.
        const field = await waitFor(async () => {
          const f = await evaluate(fieldExpression(prop));
          return f && !f.disabled ? f : null;
        }, `${prop} field enabled`, 10_000);
        mustPass(`B ${tag}`, `${prop} input is visible and enabled`, field.hit);
        await click(field.x, field.y);
        await key("a", "KeyA", 65, { modifiers: process.platform === "darwin" ? 4 : 2, commands: ["selectAll"] });
        await cdp.send("Input.insertText", { text: String(value) });
        const typed = await evaluate(fieldExpression(prop));
        check(`B ${tag}`, "typed value is in the input", typed.value === String(value), typed.value);
        await key("Enter", "Enter", 13);

        // C. Backend confirms the edit.
        const post = await waitFor(() => {
          const entry = [...requests.entries()].slice(requestCount)
            .find(([, r]) => r.method === "POST" && r.url.endsWith(`/api/properties/${WALL_GUID}`) && r.finished);
          return entry ?? null;
        }, `${tag} POST finished`, 30_000);
        const body = JSON.parse((await cdp.send("Network.getResponseBody", { requestId: post[0] })).body);
        expected[prop] = value;
        const entity = body.entity;
        check(`C ${tag}`, "backend confirms the property update",
          post[1].status === 200 && body.success === true && JSON.stringify(entity.psets[QTO]) === JSON.stringify(expected),
          JSON.stringify(entity.psets[QTO]));
        check(`D ${tag}`, "regeneration succeeded (no geometry_warning)", body.geometry_warning === null);

        // E/F. New STL + GeometryRef.
        const url = entity.geometry.url;
        const dims = dimsOf(expected);
        check(`E ${tag}`, "new unique STL generated",
          url !== currentUrl && !seenUrls.has(url) && /^\/geometry\/[0-9a-f]{32}\.stl$/.test(url)
            && fs.existsSync(path.join(scadDir, url.split("/").pop())), url);
        check(`F ${tag}`, "GeometryRef bounds match the new dimensions",
          JSON.stringify(entity.geometry.bounds) === JSON.stringify({ min: [0, 0, 0], max: dims }),
          JSON.stringify(entity.geometry.bounds.max));
        seenUrls.add(url);
        currentUrl = url;

        // G. Browser fetches the updated STL.
        const stlReq = await waitFor(() => hasNewRequest((r) => r.url.endsWith(url) && (r.finished || r.failed)),
          `${tag} STL fetch`, 15_000);
        check(`G ${tag}`, "browser fetched the regenerated STL", stlReq.status === 200 && stlReq.finished,
          `status ${stlReq.status}`);

        // H. Old mesh replaced by the new STL mesh (not the box placeholder).
        const after = await waitFor(async () => {
          const p = await probe(before.uuid);
          return p.hasMesh && p.uuid !== before.uuid && p.geometryType === "BufferGeometry"
            && p.max.every((v, i) => near(v, dims[i], 1e-3)) ? p : null;
        }, `${tag} STL mesh swapped in`, 15_000).catch(() => null);
        const afterProbe = after ?? (await probe(before.uuid));
        check(`H ${tag}`, "viewport replaced the old mesh with the new STL mesh",
          after && after.inScene && !after.oldMeshStillInScene && after.meshCount === 1
            && after.min.every((v) => near(v, 0, 1e-3)),
          `${afterProbe.geometryType}, ${afterProbe.vertexCount} vertices, max ${JSON.stringify(afterProbe.max)}`);

        // UI settled: success tick, no error/warning, input shows the confirmed value.
        const settled = await waitFor(async () => {
          const f = await evaluate(fieldExpression(prop));
          const p = await panel();
          return !p.saving && f && !f.disabled ? { f, p } : null;
        }, `${tag} saving finished`, 10_000);
        const persisted = JSON.parse(fs.readFileSync(modelPath, "utf8")).entities.find((e) => e.guid === WALL_GUID);
        check(`C2 ${tag}`, "UI settled with confirmed value, no error/warning; temp model persisted",
          settled.f.value === String(value) && !settled.p.error && !settled.p.geometryWarning && !settled.p.validationError
            && persisted.geometry.url === url && persisted.psets[QTO][prop] === value);

        // I. Selection/highlight survives the mesh replacement.
        await sleep(200);
        ({ image } = await screenshotCanvas(`after-${tag}`));
        const orange = pixelStats(image, isSelectedPixel);
        const grey = pixelStats(image, isUnselectedWallPixel);
        const stillSelected = await probe();
        const p = await panel();
        const iOk = check(`I ${tag}`, "selection + highlight preserved after replacement",
          p.name === WALL_NAME && p.treeSelected.includes(WALL_NAME) && stillSelected.highlight === "selected"
            && stillSelected.color === SELECTED_HEX && orange.count > 500 && grey.count < 50,
          `highlight ${stillSelected.highlight}, colour #${stillSelected.color}, selected ${orange.count} px, unselected ${grey.count} px`);

        if (after && iOk) editsPassed++;
      }

      // J. Double-click focus on the regenerated mesh.
      let shot = await screenshotCanvas(`R${round}-before-focus`);
      const beforeFocus = pixelStats(shot.image, isSelectedPixel);
      const focusPoint = await canvasPoint(shot.rect, beforeFocus.pick);
      mustPass(`J${round}a`, "double-click point is on the visible canvas", focusPoint.hit);
      const meshBefore = await probe();
      await click(focusPoint.x, focusPoint.y, 2);
      const center = meshBefore.min.map((v, i) => (v + meshBefore.max[i]) / 2);
      const focused = await waitFor(async () => {
        const q = await probe();
        return q.target && q.target.every((v, i) => near(v, center[i], 1)) ? q : null;
      }, `R${round} focus target`, 5_000).catch(() => null);
      await sleep(300);
      shot = await screenshotCanvas(`R${round}-after-focus`);
      const afterFocus = pixelStats(shot.image, isSelectedPixel);
      const { width, height } = shot.image;
      const centred = afterFocus.count > 0
        && near(afterFocus.cx, width / 2, width * 0.05) && near(afterFocus.cy, height / 2, height * 0.05);
      const p = await panel();
      check(`J R${round}`, "double-click focuses the regenerated mesh (target = its centre, wall centred on screen, still selected)",
        focused && focused.uuid === meshBefore.uuid && centred && p.name === WALL_NAME && focused.highlight === "selected",
        `target ${JSON.stringify(focused?.target)}, expected ${JSON.stringify(center)}, `
          + `on-screen centroid (${afterFocus.cx?.toFixed(0)}, ${afterFocus.cy?.toFixed(0)}) of ${width}x${height}`);
    }

    // ── K / L ──
    const httpErrors = [...requests.values()]
      .filter((r) => ((r.status !== null && r.status >= 400) || r.failed) && !isBrowserFaviconNoise(r.url));
    const unexpectedAppErrors = appErrors.filter((e) => !isBrowserFaviconNoise(e));
    const backendErrors = backendLog.join("").split(/\r?\n/).filter((l) => /Traceback|^ERROR|\bERROR:/.test(l));
    check("K", "no unexpected application errors (console, exceptions, HTTP, backend log)",
      unexpectedAppErrors.length === 0 && httpErrors.length === 0 && backendErrors.length === 0,
      [...unexpectedAppErrors, ...httpErrors.map((r) => `${r.method} ${r.url} ${r.status ?? r.failed}`), ...backendErrors].join(" | "));
    const totalEdits = EDIT_ROUNDS.flat().length;
    check("L", "workflow repeated: every edit in both rounds replaced the mesh and kept the selection",
      editsPassed === totalEdits && seenUrls.size === totalEdits + 1, `${editsPassed}/${totalEdits} edits, ${seenUrls.size} distinct STL urls`);
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
