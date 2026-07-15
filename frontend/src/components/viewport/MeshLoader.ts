/**
 * components/viewport/MeshLoader.ts
 *
 * M06 Geometry Manager — MeshLoader (SDS Section 3, M06 "MeshLoader
 * (STL -> BufferGeometry)").
 *
 * MVP: derives a placeholder box from GeometryRef.bounds rather than
 * parsing the actual STL client-side (SceneManager.loadMesh already
 * takes bounds directly). This module exists as the seam where real STL
 * loading will be inserted in Phase 2.
 */

import type { SceneManager } from "@/components/viewport/SceneManager";
import type { IfcEntity } from "@/types/entities";

export class MeshLoader {
  constructor(private readonly sceneManager: SceneManager) {}

  /**
   * Load geometry for every entity that has a `geometry` ref into the
   * scene. Entities without geometry (e.g. spatial containers like
   * IfcBuildingStorey) are skipped.
   *
   * TODO(Phase 2): Fetch and parse the actual STL bytes from
   * `entity.geometry.url` via THREE.STLLoader instead of using bounds
   * as a box placeholder.
   */
  loadEntities(entities: IfcEntity[]): void {
    for (const entity of entities) {
      if (entity.geometry) {
        this.sceneManager.loadMesh(entity.guid, entity.geometry.bounds);
      }
    }
  }
}
