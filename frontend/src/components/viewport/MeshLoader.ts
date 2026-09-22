/**
 * components/viewport/MeshLoader.ts
 *
 * M06 Geometry Manager — MeshLoader (SDS Section 3, M06 "MeshLoader
 * (STL -> BufferGeometry)").
 *
 * Loads real STL geometry from `entity.geometry.url` (via
 * SceneManager.loadMeshFromStl, which uses THREE.STLLoader). Falls back
 * to a bounds-derived placeholder box when an entity has no url, or when
 * the STL fails to fetch/parse (SceneManager handles that fallback
 * internally, since it owns the async load).
 */

import type { SceneManager } from "@/components/viewport/SceneManager";
import type { IfcEntity } from "@/types/entities";

export class MeshLoader {
  constructor(private readonly sceneManager: SceneManager) {}

  /**
   * Load geometry for every entity that has a `geometry` ref into the
   * scene. Entities without geometry (e.g. spatial containers like
   * IfcBuildingStorey) are skipped.
   */
  loadEntities(entities: IfcEntity[]): void {
    for (const entity of entities) {
      const geometry = entity.geometry;
      if (!geometry) continue;

      if (geometry.url) {
        this.sceneManager.loadMeshFromStl(entity.guid, geometry.url, geometry.bounds);
      } else {
        this.sceneManager.loadMesh(entity.guid, geometry.bounds);
      }
    }
  }
}
