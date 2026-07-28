/**
 * components/viewport/SceneManager.ts
 *
 * M06 Geometry Manager — imperative Three.js scene owner (SDS Section 3,
 * M06 "THREEScene (scene, camera, renderer)").
 *
 * Deliberately framework-free: React only tells this class when to
 * mount/unmount/resize/loadMesh/highlight, per the SDS API surface:
 *   render(), loadMesh(guid, geometry), highlight(guid, mode),
 *   pick(ray), applyTransform(guid, matrix)
 *
 * MVP scope: orbit camera (via OrbitControls: right-drag orbit, middle-drag
 * pan, wheel zoom -- left mouse is deliberately left unbound from camera
 * control so it's free for selection, see mount()) + raycaster picking +
 * placeholder box geometry (bounding-box rendering, per
 * MVP_Summary_and_Overview.md "Parsing real STL files is too complex; use
 * bounding box -> BoxGeometry").
 *
 * TODO(Phase 2): Load real STL geometry via THREE.STLLoader instead of
 * bounding-box placeholders; add SelectionGizmo (three-transform-controls).
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import type { GeometryBounds } from "@/types/entities";

export type HighlightMode = "selected" | "hover" | "none";

interface MeshEntry {
  mesh: THREE.Mesh;
  baseColor: THREE.Color;
}

export class SceneManager {
  private readonly scene: THREE.Scene;
  private readonly camera: THREE.PerspectiveCamera;
  private renderer: THREE.WebGLRenderer | null = null;
  private controls: OrbitControls | null = null;
  private readonly raycaster = new THREE.Raycaster();
  private readonly meshesByGuid = new Map<string, MeshEntry>();
  private animationFrameId: number | null = null;

  constructor() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xf0f0f0);

    this.camera = new THREE.PerspectiveCamera(50, 1, 1, 1_000_000);
    this.camera.position.set(8000, -8000, 6000);
    this.camera.up.set(0, 0, 1); // Z-up, matching IFC convention
    this.camera.lookAt(0, 0, 0);

    const light = new THREE.HemisphereLight(0xffffff, 0x444444, 1.2);
    this.scene.add(light);
    this.scene.add(new THREE.GridHelper(20000, 20));
  }

  /** Attach the renderer to a DOM canvas. Call once, on mount. */
  mount(canvas: HTMLCanvasElement): void {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });

    // Camera mouse-button scheme (Revit/Blender-style, not free-look):
    //   left mouse   -> intentionally left UNBOUND. OrbitControls never
    //                   sees left-button drags, so it can't compete with
    //                   click-to-select in Viewport.tsx, and a left-drag
    //                   currently does nothing (reserved for a future
    //                   selection-rectangle gesture).
    //   right mouse  -> orbit (ROTATE), around controls.target
    //   middle mouse -> pan
    //   wheel        -> zoom (DOLLY; handled internally by OrbitControls,
    //                   independent of the mouseButtons mapping below)
    const controls = new OrbitControls(this.camera, canvas);
    controls.mouseButtons = {
      MIDDLE: THREE.MOUSE.PAN,
      RIGHT: THREE.MOUSE.ROTATE,
    };
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    controls.target.set(0, 0, 0);
    controls.update();
    this.controls = controls;

    this.startRenderLoop();
  }

  /** Detach and dispose all GPU resources. Call once, on unmount. */
  unmount(): void {
    if (this.animationFrameId !== null) {
      cancelAnimationFrame(this.animationFrameId);
    }
    this.meshesByGuid.forEach(({ mesh }) => this.disposeMesh(mesh));
    this.meshesByGuid.clear();
    this.controls?.dispose();
    this.controls = null;
    this.renderer?.dispose();
    this.renderer = null;
  }

  resize(width: number, height: number): void {
    this.camera.aspect = width / Math.max(height, 1);
    this.camera.updateProjectionMatrix();
    this.renderer?.setSize(width, height, false);
  }

  /**
   * FR-044: Update viewport geometry when an entity's geometry changes.
   * Disposes any previous mesh for `guid` and loads a new placeholder box
   * matching `bounds`.
   */
  loadMesh(guid: string, bounds: GeometryBounds): void {
    const existing = this.meshesByGuid.get(guid);
    if (existing) {
      this.disposeMesh(existing.mesh);
      this.meshesByGuid.delete(guid);
    }

    const size = new THREE.Vector3(
      bounds.max[0] - bounds.min[0],
      bounds.max[1] - bounds.min[1],
      bounds.max[2] - bounds.min[2]
    );
    const center = new THREE.Vector3(
      (bounds.max[0] + bounds.min[0]) / 2,
      (bounds.max[1] + bounds.min[1]) / 2,
      (bounds.max[2] + bounds.min[2]) / 2
    );

    const geometry = new THREE.BoxGeometry(size.x || 1, size.y || 1, size.z || 1);
    const baseColor = new THREE.Color(0xb0b8c1);
    const material = new THREE.MeshLambertMaterial({ color: baseColor });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.copy(center);
    mesh.userData.guid = guid;

    this.scene.add(mesh);
    this.meshesByGuid.set(guid, { mesh, baseColor });
  }

  /** FR-043: Raycast from normalized device coords, return hit guid or null. */
  pick(ndcX: number, ndcY: number): string | null {
    this.raycaster.setFromCamera(new THREE.Vector2(ndcX, ndcY), this.camera);
    const meshes = Array.from(this.meshesByGuid.values()).map((entry) => entry.mesh);
    const intersections = this.raycaster.intersectObjects(meshes, false);
    if (intersections.length === 0) return null;
    return (intersections[0].object.userData.guid as string) ?? null;
  }

  /**
   * Focus the camera on `guid`'s mesh: retarget OrbitControls so future
   * orbiting happens around that object (not the world origin), and move
   * the camera along its current viewing direction so the object is
   * nicely framed, rather than jumping to an arbitrary angle.
   *
   * No-ops if `guid` has no loaded mesh or controls aren't mounted yet.
   *
   * Written as a small, self-contained "frame this bounding box" utility
   * so it can be reused later for a multi-object Fit-to-View without
   * changing its shape -- that's a separate, not-yet-approved feature,
   * but this method's internals are intentionally already generic enough
   * to support it.
   */
  focusOn(guid: string): void {
    const entry = this.meshesByGuid.get(guid);
    if (!entry || !this.controls) return;

    const box = new THREE.Box3().setFromObject(entry.mesh);
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const radius = sphere.radius || 1;

    const previousTarget = this.controls.target.clone();
    let direction = new THREE.Vector3().subVectors(this.camera.position, previousTarget);
    if (direction.lengthSq() === 0) {
      // Camera was exactly at the target (degenerate case) -- fall back
      // to a fixed viewing angle rather than dividing by zero.
      direction = new THREE.Vector3(1, -1, 0.75);
    }
    direction.normalize();

    // Distance at which a sphere of this radius fills the vertical FOV,
    // with 50% padding so the object isn't touching the viewport edges.
    const verticalFovRadians = (this.camera.fov * Math.PI) / 180;
    const fitDistance = (radius / Math.sin(verticalFovRadians / 2)) * 1.5;

    this.camera.position.copy(sphere.center).addScaledVector(direction, fitDistance);
    this.controls.target.copy(sphere.center);
    this.controls.update();
  }

  /** Apply a highlight color to `guid`'s mesh (selection/hover feedback). */
  highlight(guid: string, mode: HighlightMode): void {
    const entry = this.meshesByGuid.get(guid);
    if (!entry) return;
    const material = entry.mesh.material as THREE.MeshLambertMaterial;
    if (mode === "selected") {
      material.color.set(0xff8c1a);
    } else if (mode === "hover") {
      material.color.set(0x1a90ff);
    } else {
      material.color.copy(entry.baseColor);
    }
  }

  /**
   * TODO(Phase 2): applyTransform(guid, matrix) — hook up once
   * SelectionGizmo / M07 Command Interpreter exist to dispatch transform
   * deltas.
   */
  applyTransform(_guid: string, _matrix: THREE.Matrix4): void {
    throw new Error("applyTransform is not implemented in the MVP (Phase 2 feature)");
  }

  private startRenderLoop(): void {
    const renderFrame = () => {
      this.controls?.update();
      if (this.renderer) {
        this.renderer.render(this.scene, this.camera);
      }
      this.animationFrameId = requestAnimationFrame(renderFrame);
    };
    this.animationFrameId = requestAnimationFrame(renderFrame);
  }

  private disposeMesh(mesh: THREE.Mesh): void {
    this.scene.remove(mesh);
    mesh.geometry.dispose();
    if (Array.isArray(mesh.material)) {
      mesh.material.forEach((m) => m.dispose());
    } else {
      mesh.material.dispose();
    }
  }
}