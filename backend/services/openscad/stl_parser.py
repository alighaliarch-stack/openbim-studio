"""
services/openscad/stl_parser.py

M08 OpenSCAD Engine — StlParser (FR-052).

Parses an STL file with `trimesh` and extracts a bounding box for the
frontend viewport (placeholder BoxGeometry rendering per MVP scope note:
"Parsing real STL files is too complex; use bounding box -> BoxGeometry").
"""

from __future__ import annotations

from pathlib import Path

import trimesh

from services.openscad.models import GeometryBounds


class StlParseError(RuntimeError):
    """Raised when an STL file cannot be loaded or is malformed."""


class StlParser:
    """Wraps trimesh loading + bounds extraction for rendered geometry."""

    def parse_bounds(self, stl_path: Path) -> GeometryBounds:
        """
        Load `stl_path` and return its axis-aligned bounding box.

        Raises:
            StlParseError: if the file is missing or cannot be parsed.
        """
        if not stl_path.exists():
            raise StlParseError(f"STL file not found: {stl_path}")

        try:
            mesh = trimesh.load(str(stl_path), file_type="stl")
        except Exception as exc:  # noqa: BLE001
            raise StlParseError(f"Failed to parse STL '{stl_path}': {exc}") from exc

        bounds = mesh.bounds  # shape (2, 3): [min_xyz, max_xyz]
        return GeometryBounds(
            min=(float(bounds[0][0]), float(bounds[0][1]), float(bounds[0][2])),
            max=(float(bounds[1][0]), float(bounds[1][1]), float(bounds[1][2])),
        )
