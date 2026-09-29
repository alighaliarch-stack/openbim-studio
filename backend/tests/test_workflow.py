"""
tests/test_workflow.py

Day-25 minimal repeatable workflow test (backend half).

Drives the real property-edit path end to end through the FastAPI app:

    POST /api/properties/{guid} -> EntityStore.set_prop() -> ParametricGeometry
    -> GeometryFactory -> WallGenerator -> OpenSCADEngine -> openscad CLI
    -> STL on disk -> GeometryRef -> persisted model.json -> GET /geometry/<stl>

Isolation: the app runs against a temporary model.json (MODEL_JSON_PATH)
and a temporary STL directory (SCAD_TMP_DIR), both set before `main` is
imported, so backend/data/model.json is never read or written.

Requires the OpenSCAD CLI (OPENSCAD_BINARY, `openscad` on PATH, or the
default Windows install location); the workflow tests are skipped if it
cannot be found.

Run from backend/:
    venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROTECTED_MODEL_JSON = BACKEND_ROOT / "data" / "model.json"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

WALL_GUID = "day25-wall-001"
QTO = "Qto_WallBaseQuantities"

# Realistic wall (mm): 5 m long, 3 m high, 200 mm thick.
SEED_QUANTITIES = {"Length": 5000, "Height": 3000, "Thickness": 200}

# The same Length -> Height -> Thickness sequence, run twice with
# different realistic values so every edit is a real change.
EDIT_ROUNDS = [
    [("Length", 6000), ("Height", 3200), ("Thickness", 250)],
    [("Length", 4500), ("Height", 2800), ("Thickness", 300)],
]

_WINDOWS_OPENSCAD = Path("C:/Program Files/OpenSCAD/openscad.exe")


def _find_openscad() -> str | None:
    configured = os.environ.get("OPENSCAD_BINARY")
    if configured:
        return configured if (Path(configured).exists() or shutil.which(configured)) else None
    on_path = shutil.which("openscad")
    if on_path:
        return on_path
    return str(_WINDOWS_OPENSCAD) if _WINDOWS_OPENSCAD.exists() else None


def _seed_document() -> dict:
    # A minimal Project -> Building -> Storey -> Wall model. The wall's
    # seed geometry url points at an STL that doesn't exist yet; the first
    # edit is what produces the first real STL.
    return {
        "project": {"guid": "day25-project", "name": "Day 25 workflow test", "description": ""},
        "entities": [
            {
                "guid": "day25-building-001",
                "type": "IfcBuilding",
                "name": "Test Building",
                "children_guids": ["day25-storey-001"],
            },
            {
                "guid": "day25-storey-001",
                "type": "IfcBuildingStorey",
                "name": "Ground Floor",
                "parent_guid": "day25-building-001",
                "children_guids": [WALL_GUID],
            },
            {
                "guid": WALL_GUID,
                "type": "IfcWall",
                "name": "Test Wall",
                "parent_guid": "day25-storey-001",
                "psets": {QTO: dict(SEED_QUANTITIES)},
                "geometry": {
                    "type": "stl",
                    "url": "/geometry/day25-seed-placeholder.stl",
                    "bounds": {"min": [0, 0, 0], "max": [5000, 200, 3000]},
                },
            },
        ],
    }


def _stl_bounds(data: bytes) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Independent STL bounds reader (ASCII or binary); deliberately does
    not reuse the backend's StlParser, so the check doesn't trust the code
    under test."""
    vertices: list[tuple[float, float, float]] = []
    if data.lstrip()[:5].lower() == b"solid" and b"facet" in data:
        for line in data.decode("ascii", errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0] == "vertex":
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
    else:
        (count,) = struct.unpack_from("<I", data, 80)
        for i in range(count):
            offset = 84 + i * 50 + 12  # skip the facet normal
            for v in range(3):
                vertices.append(struct.unpack_from("<3f", data, offset + v * 12))
    if not vertices:
        raise AssertionError("STL contains no vertices")
    xs, ys, zs = zip(*vertices)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


class ModelPathConfigTest(unittest.TestCase):
    """MODEL_JSON_PATH must only take effect when explicitly set."""

    def test_default_and_override(self) -> None:
        from core.config import Settings

        saved = os.environ.pop("MODEL_JSON_PATH", None)
        try:
            self.assertEqual(Settings().model_json_path, PROTECTED_MODEL_JSON)
            os.environ["MODEL_JSON_PATH"] = ""
            self.assertEqual(Settings().model_json_path, PROTECTED_MODEL_JSON)
            override = Path(tempfile.gettempdir()) / "day25-override-model.json"
            os.environ["MODEL_JSON_PATH"] = str(override)
            self.assertEqual(Settings().model_json_path, override)
        finally:
            if saved is None:
                os.environ.pop("MODEL_JSON_PATH", None)
            else:
                os.environ["MODEL_JSON_PATH"] = saved


class WallEditWorkflowTest(unittest.TestCase):
    """Wall property edit -> OpenSCAD -> new STL -> GeometryRef -> persistence."""

    @classmethod
    def setUpClass(cls) -> None:
        openscad = _find_openscad()
        if openscad is None:
            raise unittest.SkipTest("OpenSCAD CLI not found; set OPENSCAD_BINARY to run the workflow test")

        cls._tmp = Path(tempfile.mkdtemp(prefix="openbim-day25-"))
        cls.model_path = cls._tmp / "model.json"
        cls.scad_dir = cls._tmp / "scad"
        cls.model_path.write_text(json.dumps(_seed_document(), indent=2), encoding="utf-8")

        cls._saved_env = {k: os.environ.get(k) for k in ("MODEL_JSON_PATH", "SCAD_TMP_DIR", "OPENSCAD_BINARY")}
        os.environ["MODEL_JSON_PATH"] = str(cls.model_path)
        os.environ["SCAD_TMP_DIR"] = str(cls.scad_dir)
        os.environ["OPENSCAD_BINARY"] = openscad

        # main builds its app (and the /geometry static mount) at import
        # time from the environment above.
        import main
        from core.config import get_settings
        from fastapi.testclient import TestClient

        settings = get_settings()
        assert settings.model_json_path == cls.model_path, settings.model_json_path
        assert settings.model_json_path.resolve() != PROTECTED_MODEL_JSON.resolve()

        # Entering the client runs main's real lifespan (EntityStore.load,
        # OpenSCADEngine wiring), exactly as the server does on startup.
        cls._client_cm = TestClient(main.app)
        cls.client = cls._client_cm.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_cm.__exit__(None, None, None)
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _get_wall(self) -> dict:
        response = self.client.get("/api/model")
        self.assertEqual(response.status_code, 200)
        return next(e for e in response.json()["entities"] if e["guid"] == WALL_GUID)

    def test_edit_sequence_regenerates_geometry_repeatably(self) -> None:
        # A. The wall is served by the model API and is selectable by guid.
        wall = self._get_wall()
        self.assertEqual(wall["type"], "IfcWall")
        self.assertEqual(wall["psets"][QTO], SEED_QUANTITIES)

        expected = dict(SEED_QUANTITIES)
        seen_urls = {wall["geometry"]["url"]}

        for round_index, edits in enumerate(EDIT_ROUNDS, start=1):
            for prop, value in edits:
                with self.subTest(round=round_index, prop=prop, value=value):
                    previous_url = self._get_wall()["geometry"]["url"]

                    # B/C. Submit the edit; backend confirms it.
                    response = self.client.post(
                        f"/api/properties/{WALL_GUID}",
                        json={"pset_name": QTO, "property_name": prop, "value": value},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    body = response.json()
                    self.assertTrue(body["success"])
                    self.assertIsNone(body["geometry_warning"])

                    expected[prop] = value
                    entity = body["entity"]
                    self.assertEqual(entity["psets"][QTO], expected)

                    # D/E/F. Regeneration produced a new, unique STL.
                    url = entity["geometry"]["url"]
                    self.assertNotEqual(url, previous_url)
                    self.assertNotIn(url, seen_urls)
                    seen_urls.add(url)
                    self.assertRegex(url, r"^/geometry/[0-9a-f]{32}\.stl$")

                    stl_file = self.scad_dir / url.rsplit("/", 1)[1]
                    self.assertTrue(stl_file.is_file(), stl_file)
                    fetched = self.client.get(url)
                    self.assertEqual(fetched.status_code, 200)
                    self.assertGreater(len(fetched.content), 0)
                    self.assertEqual(fetched.content, stl_file.read_bytes())

                    # Axis convention (wall.scad.j2): X=Length, Y=Thickness, Z=Height.
                    dims = (expected["Length"], expected["Thickness"], expected["Height"])
                    stl_min, stl_max = _stl_bounds(fetched.content)
                    for axis in range(3):
                        self.assertAlmostEqual(stl_min[axis], 0.0, places=3)
                        self.assertAlmostEqual(stl_max[axis], dims[axis], places=3)
                    self.assertEqual(entity["geometry"]["bounds"]["min"], [0.0, 0.0, 0.0])
                    self.assertEqual(entity["geometry"]["bounds"]["max"], [float(d) for d in dims])

                    # Persisted (temporary) model matches the response.
                    persisted = json.loads(self.model_path.read_text(encoding="utf-8"))
                    persisted_wall = next(e for e in persisted["entities"] if e["guid"] == WALL_GUID)
                    self.assertEqual(persisted_wall["psets"][QTO], expected)
                    self.assertEqual(persisted_wall["geometry"], entity["geometry"])

                    # The model API now serves the regenerated entity.
                    self.assertEqual(self._get_wall()["geometry"], entity["geometry"])

        # L. Both rounds ran: seed url + one new STL per edit, all distinct.
        self.assertEqual(len(seen_urls), 1 + sum(len(r) for r in EDIT_ROUNDS))


if __name__ == "__main__":
    unittest.main()
