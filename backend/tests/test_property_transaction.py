"""
tests/test_property_transaction.py

Day-28 atomic property-edit tests, driven through the real
POST /api/properties/{guid} route.

Acceptance: every rejected edit leaves the in-memory model and the
persisted model.json unchanged (byte-identical); a successful wall
dimension edit commits matching semantic properties and regenerated
geometry, identically in memory, on disk and from GET /api/model.

The app is built from the real api_router on a temporary model and STL
directory. `main` is deliberately not imported: it builds its /geometry
static mount once per process at import time, which would tie
test_workflow.py's mount to this file's temporary directory.

Failure injection uses mocks at the real boundaries (OpenSCADEngine.render,
CLIExecutor.render_to_stl, StlParser.parse_bounds, ModelPersistence.save,
os.replace), so AtomicPropertyEditTest needs no OpenSCAD and is never
skipped. RealOpenSCADEditTest uses the real CLI and is skipped without it.
backend/data/model.json is never read or written.

Run from backend/:
    venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest import mock

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.router import api_router  # noqa: E402
from core.persistence import ModelPersistence  # noqa: E402
from services.commands import contract  # noqa: E402
from services.geometry import wall_generator as wall_generator_module  # noqa: E402
from services.ifc import entity_store as entity_store_module  # noqa: E402
from services.ifc.entity_store import EntityStore  # noqa: E402
from services.ifc.geometry_inputs import (  # noqa: E402
    GEOMETRY_INPUTS, MAX_COORDINATE_MM, MAX_DIMENSION_MM, PLACEMENT_PSET,
)
from services.ifc.models import IfcType, ModelDocument  # noqa: E402
from services.openscad import engine as engine_module  # noqa: E402
from services.openscad.cli_executor import CLIExecutor, CLIResult, OpenSCADTimeoutError  # noqa: E402
from services.openscad.component_registry import ComponentRegistry  # noqa: E402
from services.openscad.engine import OpenSCADEngine  # noqa: E402
from services.openscad.models import GeometryBounds, RenderResult, RenderStatus  # noqa: E402
from services.openscad.stl_parser import StlParseError, StlParser  # noqa: E402
from services.openscad.template_renderer import TemplateRenderer  # noqa: E402
from tests.test_workflow import _find_openscad, _stl_bounds  # noqa: E402

QTO = "Qto_WallBaseQuantities"
WALL = "day28-wall-001"
WALL_NO_GEOMETRY = "day28-wall-no-geometry"
WALL_NO_QTO = "day28-wall-no-qto"
WALL_BAD_HEIGHT = "day28-wall-bad-height"
DOOR = "day28-door-001"
STOREY = "day28-storey-001"
SEED = {"Length": 5000, "Height": 3000, "Thickness": 200}
SEED_URL = "/geometry/day28-seed.stl"


def _geometry(dims: dict) -> dict:
    return {"type": "stl", "url": SEED_URL,
            "bounds": {"min": [0, 0, 0], "max": [dims["Length"], dims["Thickness"], dims["Height"]]}}


def _seed_document() -> dict:
    walls = [WALL, WALL_NO_GEOMETRY, WALL_NO_QTO, WALL_BAD_HEIGHT]
    return {
        "project": {"guid": "day28-project", "name": "Day 28 atomic edits", "description": ""},
        "entities": [
            {"guid": STOREY, "type": "IfcBuildingStorey", "name": "Ground Floor", "children_guids": [*walls, DOOR]},
            {"guid": WALL, "type": "IfcWall", "name": "Test Wall", "parent_guid": STOREY,
             "psets": {QTO: dict(SEED), "Pset_WallCommon": {"IsExternal": True}}, "geometry": _geometry(SEED)},
            {"guid": WALL_NO_GEOMETRY, "type": "IfcWall", "name": "Wall Without Geometry", "parent_guid": STOREY,
             "psets": {QTO: dict(SEED)}},
            {"guid": WALL_NO_QTO, "type": "IfcWall", "name": "Wall Without Quantities", "parent_guid": STOREY,
             "psets": {"Pset_WallCommon": {"IsExternal": False}}, "geometry": _geometry(SEED)},
            {"guid": WALL_BAD_HEIGHT, "type": "IfcWall", "name": "Wall With Bad Height", "parent_guid": STOREY,
             "psets": {QTO: {"Length": 5000, "Height": "abc", "Thickness": 200}}, "geometry": _geometry(SEED)},
            {"guid": DOOR, "type": "IfcDoor", "name": "Test Door", "parent_guid": STOREY,
             "psets": {"Qto_DoorBaseQuantities": {"Width": 900, "Height": 2100}}},
        ],
    }


class _TransactionTestCase(unittest.TestCase):
    """Real route + EntityStore on a temporary model, with consistency helpers."""

    OPENSCAD_BINARY: str | None = None  # None -> a binary that doesn't exist

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="openbim-day28-"))
        self.model_path = self._tmp / "model.json"
        self.scad_dir = self._tmp / "scad"
        self.model_path.write_text(json.dumps(_seed_document(), indent=2), encoding="utf-8")

        self.store = EntityStore(ModelPersistence(self.model_path))
        self.store.load()

        self._previous_engine = engine_module._current_engine
        binary = self.OPENSCAD_BINARY or str(self._tmp / "openscad-not-installed.exe")
        engine_module.set_current_engine(OpenSCADEngine(
            ComponentRegistry(), TemplateRenderer(BACKEND_ROOT / "templates"),
            CLIExecutor(binary, self.scad_dir, 30), StlParser(),
        ))

        app = FastAPI()
        app.include_router(api_router)
        app.state.entity_store = self.store
        self.client = TestClient(app)

        # Keep expected exception tracebacks out of the test output; tests
        # can still assert on them.
        patcher = mock.patch.object(entity_store_module.logger, "exception")
        self.logged_exception = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(wall_generator_module.logger, "warning")
        self.logged_render_warning = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        engine_module._current_engine = self._previous_engine
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── requests ──

    def edit(self, prop: str, value: object, guid: str = WALL, pset: str = QTO):
        return self.client.post(f"/api/properties/{guid}", json={"pset_name": pset, "property_name": prop, "value": value})

    def edit_raw(self, body: str, guid: str = WALL):
        return self.client.post(f"/api/properties/{guid}", content=body, headers={"Content-Type": "application/json"})

    # ── state ──

    def memory(self) -> list[dict]:
        return [e.model_dump(mode="json") for e in self.store.get_all_entities()]

    def disk(self) -> list[dict]:
        document = ModelDocument.model_validate(json.loads(self.model_path.read_text(encoding="utf-8")))
        return [e.model_dump(mode="json") for e in document.entities]

    def snapshot(self) -> tuple[bytes, list[dict]]:
        return self.model_path.read_bytes(), self.memory()

    def assert_unchanged(self, before: tuple[bytes, list[dict]]) -> None:
        self.assertEqual(self.model_path.read_bytes(), before[0], "model.json changed")
        self.assertEqual(self.memory(), before[1], "in-memory model changed")
        self.assert_consistent()

    def assert_consistent(self) -> None:
        """Memory == disk == GET /api/model."""
        memory = self.memory()
        self.assertEqual(self.disk(), memory, "model.json differs from the in-memory model")
        response = self.client.get("/api/model")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entities"], memory, "GET /api/model differs from the in-memory model")

    def assert_rejected(self, response, status: int, code: str) -> dict:
        self.assertEqual(response.status_code, status, response.text)
        detail = response.json()["detail"]
        self.assertIs(detail["success"], False)
        self.assertEqual(detail["code"], code)
        self.assertTrue(detail["error"])
        self.assertTrue(detail["detail"])
        return detail

    def assert_commits(self, prop: str, value, guid: str = WALL) -> dict:
        """Edit `prop`; it must commit the property together with geometry matching all quantities."""
        previous_url = self.wall_url(guid)
        response = self.edit(prop, value, guid=guid)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIs(body["success"], True)
        self.assertIsNone(body["geometry_warning"])
        entity = body["entity"]
        quantities = entity["psets"][QTO]
        self.assertEqual(quantities[prop], value)
        self.assertNotEqual(entity["geometry"]["url"], previous_url)
        self.assertRegex(entity["geometry"]["url"], r"^/geometry/[0-9a-f]{32}\.stl$")
        self.assertEqual(entity["geometry"]["bounds"]["max"],
                         [float(quantities["Length"]), float(quantities["Thickness"]), float(quantities["Height"])])
        self.assertEqual(self.store.get_entity(guid).model_dump(mode="json"), entity)
        self.assert_consistent()
        return entity

    def wall_url(self, guid: str = WALL) -> str:
        return self.store.get_entity(guid).geometry.url


def _fake_render(_engine, _component_id, params) -> RenderResult:
    """A successful render without OpenSCAD: new uuid STL url, bounds from the params (Day-52C: incl. placement)."""
    x, y, z = params.get("x", 0.0), params.get("y", 0.0), params.get("z", 0.0)
    return RenderResult(
        status=RenderStatus.SUCCESS,
        stl_path=str(Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}.stl"),
        bounds=GeometryBounds(min=(x, y, z), max=(x + params["length"], y + params["thickness"], z + params["height"])),
        render_time_ms=1.0,
    )


def _failed_render(*_args, **_kwargs) -> RenderResult:
    return RenderResult(status=RenderStatus.FAILED, error="forced failure")


class AtomicPropertyEditTest(_TransactionTestCase):
    """Validation, failure injection, memory/disk consistency and recovery (no OpenSCAD needed)."""

    def test_successful_edit_commits_property_and_matching_geometry(self) -> None:
        with mock.patch.object(OpenSCADEngine, "render", _fake_render):
            for prop, value in [("Length", 6000), ("Height", 3200), ("Thickness", 250.5)]:
                with self.subTest(prop=prop):
                    self.assert_commits(prop, value)

    def test_invalid_dimension_values_are_rejected_before_any_change(self) -> None:
        values = [0, 0.0, -1, -0.5, MAX_DIMENSION_MM + 1, MAX_DIMENSION_MM + 0.5, 10**30,
                  "3000", "abc", "", True, False, None, [], {}, [3000], {"value": 3000}]
        with mock.patch.object(OpenSCADEngine, "render") as render:
            for prop in sorted(GEOMETRY_INPUTS[IfcType.WALL][QTO]):
                for value in values:
                    with self.subTest(prop=prop, value=value):
                        before = self.snapshot()
                        self.assert_rejected(self.edit(prop, value), 422, "validation_failed")
                        self.assert_unchanged(before)
            render.assert_not_called()

    def test_non_finite_values_are_rejected(self) -> None:
        # Python's json (used by the request parser) accepts these literals.
        with mock.patch.object(OpenSCADEngine, "render") as render:
            for literal in ["NaN", "Infinity", "-Infinity"]:
                with self.subTest(literal=literal):
                    before = self.snapshot()
                    body = f'{{"pset_name": "{QTO}", "property_name": "Length", "value": {literal}}}'
                    self.assert_rejected(self.edit_raw(body), 422, "validation_failed")
                    self.assert_unchanged(before)
            render.assert_not_called()

    def test_upper_bound_is_inclusive(self) -> None:
        with mock.patch.object(OpenSCADEngine, "render", _fake_render):
            self.assert_commits("Length", MAX_DIMENSION_MM)

    def test_wall_without_geometry_is_rejected(self) -> None:
        before = self.snapshot()
        with mock.patch.object(OpenSCADEngine, "render") as render:
            detail = self.assert_rejected(self.edit("Length", 3000, guid=WALL_NO_GEOMETRY), 422, "no_existing_geometry")
        render.assert_not_called()
        self.assertIn(WALL_NO_GEOMETRY, detail["detail"])
        self.assert_unchanged(before)

    def test_rejected_edit_creates_no_property_set(self) -> None:
        # The wall has geometry but no quantities: the new pset would be
        # incomplete, so the edit is rejected and no pset is created.
        before = self.snapshot()
        with mock.patch.object(OpenSCADEngine, "render") as render:
            self.assert_rejected(self.edit("Length", 3000, guid=WALL_NO_QTO), 422, "validation_failed")
        render.assert_not_called()
        self.assertNotIn(QTO, self.store.get_entity(WALL_NO_QTO).psets)
        self.assert_unchanged(before)

    def test_invalid_stored_quantity_is_reported_not_rendered(self) -> None:
        before = self.snapshot()
        with mock.patch.object(OpenSCADEngine, "render") as render:
            detail = self.assert_rejected(self.edit("Length", 3000, guid=WALL_BAD_HEIGHT), 422, "validation_failed")
        render.assert_not_called()
        self.assertIn("Height", detail["detail"])
        self.assert_unchanged(before)

    def test_regeneration_failures_are_rejected_with_502(self) -> None:
        junk_stl = self._tmp / "junk.stl"
        junk_stl.write_bytes(b"not an stl")
        injections = {
            "OpenSCAD binary missing (unpatched CLIExecutor)": [],
            "render returns FAILED": [mock.patch.object(OpenSCADEngine, "render", _failed_render)],
            "CLI timeout": [mock.patch.object(CLIExecutor, "render_to_stl", side_effect=OpenSCADTimeoutError("forced"))],
            "CLI non-zero exit": [mock.patch.object(CLIExecutor, "render_to_stl",
                                                    return_value=CLIResult(False, None, "", "forced error", 1))],
            "STL parse error": [
                mock.patch.object(CLIExecutor, "render_to_stl", return_value=CLIResult(True, junk_stl, "", "", 0)),
                mock.patch.object(StlParser, "parse_bounds", side_effect=StlParseError("forced")),
            ],
            "render raises RuntimeError": [mock.patch.object(OpenSCADEngine, "render", side_effect=RuntimeError("forced"))],
            "subprocess raises PermissionError": [
                mock.patch.object(CLIExecutor, "render_to_stl", side_effect=PermissionError("forced"))],
            "no engine registered": [mock.patch.object(engine_module, "_current_engine", None)],
        }
        for label, patches in injections.items():
            with self.subTest(label):
                before = self.snapshot()
                for patch in patches:
                    patch.start()
                try:
                    response = self.edit("Height", 3300)
                finally:
                    for patch in reversed(patches):
                        patch.stop()
                self.assert_rejected(response, 502, "geometry_regeneration_failed")
                self.assert_unchanged(before)
                self.assertEqual(self.wall_url(), SEED_URL)

    def test_missing_binary_reason_is_logged_and_edit_rejected(self) -> None:
        # Day-52: the unpatched engine points at a binary that doesn't exist.
        # The response stays the generic 502, but the engine's own reason
        # must reach the server log instead of being discarded.
        before = self.snapshot()
        response = self.edit("Thickness", 200)
        detail = self.assert_rejected(response, 502, "geometry_regeneration_failed")
        self.assertEqual(detail["detail"], "OpenSCAD did not produce new geometry")
        self.logged_render_warning.assert_called_once()
        template, *args = self.logged_render_warning.call_args.args
        message = template % tuple(args)
        self.assertIn(WALL, message)
        self.assertIn("openscad-not-installed.exe", message)
        self.assertIn("not found", message)
        self.assert_unchanged(before)
        self.assertEqual(self.wall_url(), SEED_URL)
        self.assertEqual(list(self.scad_dir.glob("*.stl")), [])

    def test_persistence_failure_is_rejected_with_500(self) -> None:
        before = self.snapshot()
        with mock.patch.object(OpenSCADEngine, "render", _fake_render), \
                mock.patch.object(ModelPersistence, "save", side_effect=OSError("disk full")):
            self.assert_rejected(self.edit("Length", 6000), 500, "persistence_failed")
        # Geometry was regenerated on the candidate only; nothing reached memory or disk.
        self.assert_unchanged(before)
        self.assertEqual(self.wall_url(), SEED_URL)

    def test_failed_atomic_replace_leaves_no_partial_file(self) -> None:
        before = self.snapshot()
        with mock.patch.object(OpenSCADEngine, "render", _fake_render), \
                mock.patch("core.persistence.os.replace", side_effect=PermissionError("file locked")):
            self.assert_rejected(self.edit("Length", 6000), 500, "persistence_failed")
        self.assert_unchanged(before)
        self.assertEqual(sorted(p.name for p in self._tmp.glob(".model_*")), [], "temporary model file left behind")

    def test_recovery_after_each_kind_of_failure(self) -> None:
        failures = [
            ("validation", lambda: self.edit("Length", -1), [], 422),
            ("regeneration", lambda: self.edit("Length", 7000), [mock.patch.object(OpenSCADEngine, "render", _failed_render)], 502),
            ("persistence", lambda: self.edit("Length", 7000),
             [mock.patch.object(OpenSCADEngine, "render", _fake_render),
              mock.patch.object(ModelPersistence, "save", side_effect=OSError("forced"))], 500),
        ]
        for index, (label, request, patches, status) in enumerate(failures):
            with self.subTest(label):
                before = self.snapshot()
                for patch in patches:
                    patch.start()
                try:
                    self.assertEqual(request().status_code, status)
                finally:
                    for patch in reversed(patches):
                        patch.stop()
                self.assert_unchanged(before)

                value = 4000 + 100 * index
                with mock.patch.object(OpenSCADEngine, "render", _fake_render):
                    self.assert_commits("Length", value)

    def test_properties_that_do_not_drive_geometry_skip_regeneration(self) -> None:
        cases = [
            (WALL, "Pset_WallCommon", "IsExternal", False),
            (WALL, QTO, "NetVolume", 3.0),  # wall quantity WallGenerator doesn't read
            (DOOR, "Qto_DoorBaseQuantities", "Width", 1000),  # no generator for IfcDoor
            (WALL_NO_GEOMETRY, "Pset_WallCommon", "IsExternal", True),
        ]
        with mock.patch.object(OpenSCADEngine, "render") as render:
            for guid, pset, prop, value in cases:
                with self.subTest(guid=guid, prop=prop):
                    geometry_before = self.store.get_entity(guid).geometry
                    response = self.edit(prop, value, guid=guid, pset=pset)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIsNone(response.json()["geometry_warning"])
                    entity = self.store.get_entity(guid)
                    self.assertEqual(entity.psets[pset][prop], value)
                    self.assertEqual(entity.geometry, geometry_before)
                    self.assert_consistent()
            render.assert_not_called()

    def test_subscribers_are_notified_only_on_commit(self) -> None:
        events: list[tuple] = []
        self.store.subscribe(WALL, lambda guid, change, payload: events.append((guid, change, payload)))

        self.edit("Length", -1)
        with mock.patch.object(OpenSCADEngine, "render", _failed_render):
            self.edit("Length", 6000)
        with mock.patch.object(OpenSCADEngine, "render", _fake_render), \
                mock.patch.object(ModelPersistence, "save", side_effect=OSError("forced")):
            self.edit("Length", 6000)
        self.assertEqual(events, [])

        def failing_subscriber(*_args) -> None:
            raise RuntimeError("subscriber bug")

        self.store.subscribe(WALL, failing_subscriber)
        self.logged_exception.reset_mock()  # the persistence failure above also logged
        with mock.patch.object(OpenSCADEngine, "render", _fake_render):
            self.assert_commits("Length", 6000)
        self.assertEqual(events, [(WALL, "property_edited", {"pset": QTO, "prop": "Length", "value": 6000})])
        self.logged_exception.assert_called()  # the failing subscriber was logged, not raised

    def test_unknown_entity_is_404(self) -> None:
        before = self.snapshot()
        response = self.edit("Length", 3000, guid="no-such-wall")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["error"], "Entity not found")
        self.assert_unchanged(before)

    def test_command_contract_shares_the_geometry_input_rules(self) -> None:
        self.assertEqual(contract.EDITABLE_PROPERTIES, GEOMETRY_INPUTS)
        self.assertEqual(contract.MAX_DIMENSION_MM, Decimal(MAX_DIMENSION_MM))


class PlacementEditTest(_TransactionTestCase):
    """Day-52C: OpenBIM_Placement X/Y/Z drive geometry through the same atomic edit (no OpenSCAD needed)."""

    def edit_placement(self, prop: str, value: object, guid: str = WALL):
        return self.edit(prop, value, guid=guid, pset=PLACEMENT_PSET)

    def assert_world_bounds(self, entity: dict, xyz: tuple, dims: dict) -> None:
        x, y, z = xyz
        self.assertEqual(entity["geometry"]["bounds"], {
            "min": [x, y, z],
            "max": [x + dims["Length"], y + dims["Thickness"], z + dims["Height"]],
        })

    def assert_placed_commit(self, prop: str, value, expected_xyz: tuple, dims: dict = SEED) -> dict:
        previous_url = self.wall_url()
        response = self.edit_placement(prop, value)
        self.assertEqual(response.status_code, 200, response.text)
        entity = response.json()["entity"]
        self.assertEqual(entity["psets"][PLACEMENT_PSET][prop], value)
        self.assertNotEqual(entity["geometry"]["url"], previous_url)
        self.assert_world_bounds(entity, expected_xyz, dims)
        self.assertEqual(self.store.get_entity(WALL).model_dump(mode="json"), entity)
        self.assert_consistent()
        return entity

    def test_placement_edit_regenerates_geometry_at_the_new_position(self) -> None:
        with mock.patch.object(OpenSCADEngine, "render", autospec=True, side_effect=_fake_render) as render:
            entity = self.assert_placed_commit("X", 2000, (2000, 0, 0))
        params = render.call_args.args[2]
        self.assertEqual((params["x"], params["y"], params["z"]), (2000.0, 0.0, 0.0))
        self.assertEqual(entity["psets"][PLACEMENT_PSET], {"X": 2000})
        self.assertEqual(entity["psets"][QTO], SEED)

    def test_zero_negative_and_boundary_coordinates_are_accepted(self) -> None:
        with mock.patch.object(OpenSCADEngine, "render", _fake_render):
            self.assert_placed_commit("X", -1500, (-1500, 0, 0))
            self.assert_placed_commit("Y", 0, (-1500, 0, 0))
            self.assert_placed_commit("Z", -MAX_COORDINATE_MM, (-1500, 0, -MAX_COORDINATE_MM))
            self.assert_placed_commit("Y", MAX_COORDINATE_MM, (-1500, MAX_COORDINATE_MM, -MAX_COORDINATE_MM))
            self.assert_placed_commit("X", 0.5, (0.5, MAX_COORDINATE_MM, -MAX_COORDINATE_MM))

    def test_invalid_coordinates_are_rejected_before_any_change(self) -> None:
        values = [MAX_COORDINATE_MM + 1, -MAX_COORDINATE_MM - 1, MAX_COORDINATE_MM + 0.5, 10**30, -10**30,
                  "100", "", "abc", True, False, None, [], {}, [100], {"value": 100}]
        with mock.patch.object(OpenSCADEngine, "render") as render:
            for prop in ["X", "Y", "Z"]:
                for value in values:
                    with self.subTest(prop=prop, value=value):
                        before = self.snapshot()
                        detail = self.assert_rejected(self.edit_placement(prop, value), 422, "validation_failed")
                        self.assertIn(f"{PLACEMENT_PSET}.{prop}", detail["detail"])
                        self.assert_unchanged(before)
                        self.assertNotIn(PLACEMENT_PSET, self.store.get_entity(WALL).psets)
                for literal in ["NaN", "Infinity", "-Infinity"]:
                    with self.subTest(prop=prop, literal=literal):
                        before = self.snapshot()
                        body = f'{{"pset_name": "{PLACEMENT_PSET}", "property_name": "{prop}", "value": {literal}}}'
                        self.assert_rejected(self.edit_raw(body), 422, "validation_failed")
                        self.assert_unchanged(before)
            render.assert_not_called()

    def test_regeneration_failures_on_a_placement_edit_are_atomic(self) -> None:
        injections = {
            "OpenSCAD binary missing (unpatched CLIExecutor)": [],
            "render returns FAILED": [mock.patch.object(OpenSCADEngine, "render", _failed_render)],
            "render raises": [mock.patch.object(OpenSCADEngine, "render", side_effect=RuntimeError("forced"))],
        }
        for label, patches in injections.items():
            with self.subTest(label):
                before = self.snapshot()
                for patch in patches:
                    patch.start()
                try:
                    response = self.edit_placement("X", 2000)
                finally:
                    for patch in reversed(patches):
                        patch.stop()
                self.assert_rejected(response, 502, "geometry_regeneration_failed")
                self.assert_unchanged(before)
                self.assertEqual(self.wall_url(), SEED_URL)
                self.assertNotIn(PLACEMENT_PSET, self.store.get_entity(WALL).psets)

    def test_dimension_edit_preserves_the_placement(self) -> None:
        with mock.patch.object(OpenSCADEngine, "render", autospec=True, side_effect=_fake_render) as render:
            self.assert_placed_commit("X", 2000, (2000, 0, 0))
            self.assert_placed_commit("Y", -500, (2000, -500, 0))
            response = self.edit("Length", 6000)
        self.assertEqual(response.status_code, 200, response.text)
        entity = response.json()["entity"]
        params = render.call_args.args[2]
        self.assertEqual((params["x"], params["y"], params["z"], params["length"]), (2000.0, -500.0, 0.0, 6000.0))
        self.assertEqual(entity["psets"][PLACEMENT_PSET], {"X": 2000, "Y": -500})
        self.assert_world_bounds(entity, (2000, -500, 0), {**SEED, "Length": 6000})
        self.assert_consistent()

    def test_wall_without_placement_stays_at_the_origin(self) -> None:
        with mock.patch.object(OpenSCADEngine, "render", autospec=True, side_effect=_fake_render) as render:
            entity = self.assert_commits("Height", 3200)
        params = render.call_args.args[2]
        self.assertEqual((params["x"], params["y"], params["z"]), (0.0, 0.0, 0.0))
        self.assertNotIn(PLACEMENT_PSET, entity["psets"])
        self.assertEqual(entity["geometry"]["bounds"]["min"], [0.0, 0.0, 0.0])

    def test_placement_edit_on_a_wall_without_geometry_is_rejected(self) -> None:
        before = self.snapshot()
        with mock.patch.object(OpenSCADEngine, "render") as render:
            self.assert_rejected(self.edit_placement("X", 1000, guid=WALL_NO_GEOMETRY), 422, "no_existing_geometry")
        render.assert_not_called()
        self.assert_unchanged(before)

    def test_invalid_stored_coordinate_blocks_regeneration(self) -> None:
        # A hand-edited model could store a bad coordinate; any geometry edit
        # then reports it as a validation failure instead of rendering.
        self.store.get_entity(WALL).psets[PLACEMENT_PSET] = {"X": "abc"}
        with mock.patch.object(OpenSCADEngine, "render") as render:
            detail = self.assert_rejected(self.edit("Length", 6000), 422, "validation_failed")
        render.assert_not_called()
        self.assertIn(f"stored {PLACEMENT_PSET}.X", detail["detail"])
        self.assertEqual(self.store.get_entity(WALL).psets[QTO], SEED)

    def test_placement_is_not_part_of_the_ai_command_contract(self) -> None:
        self.assertNotIn(PLACEMENT_PSET, contract.EDITABLE_PROPERTIES[IfcType.WALL])


class RealOpenSCADEditTest(_TransactionTestCase):
    """Boundary values and recovery through the real OpenSCAD CLI."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.OPENSCAD_BINARY = _find_openscad()
        if cls.OPENSCAD_BINARY is None:
            raise unittest.SkipTest("OpenSCAD CLI not found; set OPENSCAD_BINARY to run the real-render transaction tests")

    def assert_real_stl(self, entity: dict) -> None:
        stl = self.scad_dir / entity["geometry"]["url"].rsplit("/", 1)[1]
        self.assertTrue(stl.is_file(), stl)
        q = entity["psets"][QTO]
        stl_min, stl_max = _stl_bounds(stl.read_bytes())
        for axis, dim in enumerate((q["Length"], q["Thickness"], q["Height"])):
            self.assertAlmostEqual(stl_min[axis], 0.0, places=3)
            self.assertAlmostEqual(stl_max[axis], dim, places=3)

    def test_boundary_values_are_accepted_and_rendered(self) -> None:
        for prop, value in [("Length", 1), ("Length", MAX_DIMENSION_MM), ("Thickness", 212.5), ("Height", 2800)]:
            with self.subTest(prop=prop, value=value):
                entity = self.assert_commits(prop, value)
                self.assert_real_stl(entity)

    def test_recovery_after_forced_failure(self) -> None:
        before = self.snapshot()
        with mock.patch.object(OpenSCADEngine, "render", _failed_render):
            self.assert_rejected(self.edit("Height", 3300), 502, "geometry_regeneration_failed")
        self.assert_unchanged(before)

        entity = self.assert_commits("Height", 3300)
        self.assert_real_stl(entity)
        self.assertEqual(entity["psets"][QTO], {**SEED, "Height": 3300})

    def test_placement_translates_the_real_stl(self) -> None:
        # Day-52C: the OpenSCAD STL itself is in world coordinates, and a
        # later dimension edit keeps the wall where it was moved.
        self.assertEqual(self.edit("X", 2000, pset=PLACEMENT_PSET).status_code, 200)
        moved = self.edit("Y", -1500, pset=PLACEMENT_PSET)
        self.assertEqual(moved.status_code, 200, moved.text)
        resized = self.edit("Length", 4500)
        self.assertEqual(resized.status_code, 200, resized.text)
        for response, length in [(moved, SEED["Length"]), (resized, 4500)]:
            entity = response.json()["entity"]
            expected_min = (2000.0, -1500.0, 0.0)
            expected_max = (2000.0 + length, -1500.0 + SEED["Thickness"], float(SEED["Height"]))
            stl = self.scad_dir / entity["geometry"]["url"].rsplit("/", 1)[1]
            stl_min, stl_max = _stl_bounds(stl.read_bytes())
            for axis in range(3):
                self.assertAlmostEqual(stl_min[axis], expected_min[axis], places=3)
                self.assertAlmostEqual(stl_max[axis], expected_max[axis], places=3)
            self.assertEqual(entity["geometry"]["bounds"], {"min": list(expected_min), "max": list(expected_max)})
        self.assert_consistent()


if __name__ == "__main__":
    unittest.main()
