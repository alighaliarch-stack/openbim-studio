"""
tests/test_command_contract.py

Day-27 AI Command Contract tests.

- Validation (services/commands/contract.py) is tested independently of
  any LLM and of OpenSCAD, against a real EntityStore on a temporary model.
- Execution (services/commands/executor.py) is tested through the real
  EntityStore.set_prop() pipeline with the real OpenSCAD CLI (skipped if
  OpenSCAD cannot be found), and traced to prove OpenSCAD is reached only
  via set_prop -> ParametricGeometry -> GeometryFactory -> WallGenerator.

backend/data/model.json is never read or written.

Run from backend/:
    venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.persistence import ModelPersistence  # noqa: E402
from services.commands import (  # noqa: E402
    CommandRejected,
    RejectionReason,
    execute_command,
    validate_command,
)
from services.ifc.entity_store import EntityStore  # noqa: E402
from services.llm.models import Command, CommandType  # noqa: E402
from services.llm.output_parser import OutputParser  # noqa: E402

WALL = "day27-wall-001"
DOOR = "day27-door-001"
WINDOW = "day27-window-001"
STOREY = "day27-storey-001"
QTO = "Qto_WallBaseQuantities"


def _seed_document() -> dict:
    wall_geometry = {"type": "stl", "url": "/geometry/day27-seed.stl", "bounds": {"min": [0, 0, 0], "max": [5000, 200, 3000]}}
    return {
        "project": {"guid": "day27-project", "name": "Day 27 command contract", "description": ""},
        "entities": [
            {"guid": STOREY, "type": "IfcBuildingStorey", "name": "Ground Floor", "children_guids": [WALL, DOOR, WINDOW]},
            {
                "guid": WALL, "type": "IfcWall", "name": "Test Wall", "parent_guid": STOREY,
                "psets": {
                    QTO: {"Length": 5000, "Height": 3000, "Thickness": 200},
                    "Pset_WallCommon": {"IsExternal": True, "LoadBearing": False},
                },
                "geometry": wall_geometry,
            },
            {
                "guid": DOOR, "type": "IfcDoor", "name": "Test Door", "parent_guid": STOREY,
                "psets": {"Qto_DoorBaseQuantities": {"Width": 900, "Height": 2100, "Thickness": 40}},
            },
            {
                "guid": WINDOW, "type": "IfcWindow", "name": "Test Window", "parent_guid": STOREY,
                "psets": {QTO: {"Length": 1200, "Height": 1500, "Thickness": 100}},
            },
        ],
    }


def edit(prop: str = "Length", value: object = 3000, unit: object = "mm", guid: str = WALL, pset: str = QTO) -> dict:
    return {"type": "EditProperty", "params": {"target_guid": guid, "pset": pset, "property": prop, "value": value, "unit": unit}}


class _TempModelTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="openbim-day27-"))
        self.model_path = self._tmp / "model.json"
        self.model_path.write_text(json.dumps(_seed_document(), indent=2), encoding="utf-8")
        self.store = EntityStore(ModelPersistence(self.model_path))
        self.store.load()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


class ValidCommandTest(_TempModelTestCase):
    """Accepted commands and unit normalisation (validation only, no execution)."""

    def test_valid_commands_normalise_to_millimetres(self) -> None:
        cases = [
            ("1 Length 3000 mm", edit("Length", 3000, "mm"), "Length", 3000),
            ("2 Height 2800 mm", edit("Height", 2800, "mm"), "Height", 2800),
            ("3 Thickness 200 mm", edit("Thickness", 200, "mm"), "Thickness", 200),
            ("4 Length 3 m", edit("Length", 3, "m"), "Length", 3000),
            ("5 Height 2.8 m", edit("Height", 2.8, "m"), "Height", 2800),
            ("Length 3.5 m", edit("Length", 3.5, "m"), "Length", 3500),
            ("Thickness 0.25 m", edit("Thickness", 0.25, "m"), "Thickness", 250),
            ("Thickness 212.5 mm", edit("Thickness", 212.5, "mm"), "Thickness", 212.5),
        ]
        for label, command, prop, expected_mm in cases:
            with self.subTest(label):
                accepted = validate_command(command, self.store)
                self.assertEqual((accepted.target_guid, accepted.pset, accepted.property), (WALL, QTO, prop))
                self.assertEqual(accepted.value_mm, expected_mm)
                self.assertIs(type(accepted.value_mm), type(expected_mm))

    def test_accepts_existing_command_model(self) -> None:
        command = Command(type=CommandType.EDIT_PROPERTY, params=edit("Height", 2.7, "m")["params"])
        self.assertEqual(validate_command(command, self.store).value_mm, 2700)

    def test_validation_does_not_mutate_model(self) -> None:
        before = self.model_path.read_bytes()
        validate_command(edit("Length", 3, "m"), self.store)
        self.assertEqual(self.store.get_entity(WALL).psets[QTO]["Length"], 5000)
        self.assertEqual(self.model_path.read_bytes(), before)


class RejectedCommandTest(_TempModelTestCase):
    """Every rejection is explicit, carries a reason, and changes nothing."""

    CASES = [
        ("6 unsupported Width", edit("Width", 3000), RejectionReason.UNSUPPORTED_PROPERTY),
        ("arbitrary IFC property", edit("IsExternal", 1, pset="Pset_WallCommon"), RejectionReason.UNSUPPORTED_PROPERTY),
        ("arbitrary property name", edit("Name", 3000), RejectionReason.UNSUPPORTED_PROPERTY),
        ("7 nonexistent GUID", edit(guid="wall-does-not-exist"), RejectionReason.UNKNOWN_ENTITY),
        ("8 non-wall: IfcDoor", edit("Height", 2100, guid=DOOR, pset="Qto_DoorBaseQuantities"), RejectionReason.UNSUPPORTED_TARGET_TYPE),
        ("8 non-wall: IfcWindow (wall pset names)", edit(guid=WINDOW), RejectionReason.UNSUPPORTED_TARGET_TYPE),
        ("8 non-wall: IfcBuildingStorey", edit(guid=STOREY), RejectionReason.UNSUPPORTED_TARGET_TYPE),
        ("9 zero", edit(value=0), RejectionReason.VALUE_OUT_OF_RANGE),
        ("9 zero metres", edit(value=0.0, unit="m"), RejectionReason.VALUE_OUT_OF_RANGE),
        ("10 negative", edit(value=-3000), RejectionReason.VALUE_OUT_OF_RANGE),
        ("above 100 m", edit(value=101, unit="m"), RejectionReason.VALUE_OUT_OF_RANGE),
        ("11 NaN", edit(value=math.nan), RejectionReason.INVALID_VALUE),
        ("12 Infinity", edit(value=math.inf), RejectionReason.INVALID_VALUE),
        ("12 -Infinity", edit(value=-math.inf), RejectionReason.INVALID_VALUE),
        ("numeric string", edit(value="3000"), RejectionReason.INVALID_VALUE),
        ("value with unit text", edit(value="3m"), RejectionReason.INVALID_VALUE),
        ("boolean value", edit(value=True), RejectionReason.INVALID_VALUE),
        ("null value", edit(value=None), RejectionReason.INVALID_VALUE),
        ("13 unsupported unit ft", edit(value=10, unit="ft"), RejectionReason.UNSUPPORTED_UNIT),
        ("13 unsupported unit cm", edit(value=300, unit="cm"), RejectionReason.UNSUPPORTED_UNIT),
        ("13 unit wrong case M", edit(value=3, unit="M"), RejectionReason.UNSUPPORTED_UNIT),
        ("13 empty unit", edit(unit=""), RejectionReason.UNSUPPORTED_UNIT),
        ("missing unit", {"type": "EditProperty", "params": {"target_guid": WALL, "pset": QTO, "property": "Length", "value": 3000}}, RejectionReason.MALFORMED_COMMAND),
        ("14 create wall (CreateComponent)", {"type": "CreateComponent", "params": {"component_type": "wall", "length": 3000}}, RejectionReason.UNSUPPORTED_COMMAND),
        ("14 create wall (CreateWall)", {"type": "CreateWall", "params": {"length": 3000}}, RejectionReason.UNSUPPORTED_COMMAND),
        ("15 move wall", {"type": "MoveComponent", "params": {"target_guid": WALL, "dx": 1000}}, RejectionReason.UNSUPPORTED_COMMAND),
        ("16 rotate wall", {"type": "RotateComponent", "params": {"target_guid": WALL, "angle": 90}}, RejectionReason.UNSUPPORTED_COMMAND),
        ("delete wall", {"type": "DeleteComponent", "params": {"target_guid": WALL}}, RejectionReason.UNSUPPORTED_COMMAND),
        ("UpdateSelection", {"type": "UpdateSelection", "params": {"guids": [WALL]}}, RejectionReason.UNSUPPORTED_COMMAND),
        ("17 direct SCAD render command", {"type": "RenderScad", "params": {"component_type": "wall", "parameters": {"length": 3000}}}, RejectionReason.UNSUPPORTED_COMMAND),
        ("17 SCAD source smuggled as extra param", {"type": "EditProperty", "params": {**edit()["params"], "scad": "cube([1,1,1]);"}}, RejectionReason.MALFORMED_COMMAND),
        ("17 SCAD source as extra command field", {**edit(), "scad": "cube([1,1,1]);"}, RejectionReason.MALFORMED_COMMAND),
        ("18 arbitrary SCAD text as value", edit(value="1000; sphere(r=900)"), RejectionReason.INVALID_VALUE),
        ("18 arbitrary SCAD text as property", edit("Length; sphere(r=900)"), RejectionReason.UNSUPPORTED_PROPERTY),
        ("18 arbitrary SCAD text as unit", edit(unit="mm; sphere(r=900)"), RejectionReason.UNSUPPORTED_UNIT),
        ("params not an object", {"type": "EditProperty", "params": "Length=3000"}, RejectionReason.MALFORMED_COMMAND),
        ("guid not a string", edit(guid=123), RejectionReason.MALFORMED_COMMAND),  # type: ignore[arg-type]
        ("not an object", "set wall length to 3m", RejectionReason.MALFORMED_COMMAND),
    ]

    def test_rejections(self) -> None:
        before = self.model_path.read_bytes()
        for label, command, reason in self.CASES:
            with self.subTest(label):
                with self.assertRaises(CommandRejected) as caught:
                    validate_command(command, self.store)
                self.assertEqual(caught.exception.reason, reason, caught.exception.detail)
                self.assertTrue(caught.exception.detail)
        self.assertEqual(self.store.get_entity(WALL).psets[QTO], {"Length": 5000, "Height": 3000, "Thickness": 200})
        self.assertEqual(self.model_path.read_bytes(), before)

    def test_property_must_already_exist(self) -> None:
        del self.store.get_entity(WALL).psets[QTO]["Thickness"]
        with self.assertRaises(CommandRejected) as caught:
            validate_command(edit("Thickness", 200), self.store)
        self.assertEqual(caught.exception.reason, RejectionReason.PROPERTY_NOT_PRESENT)

    def test_rejected_commands_never_reach_set_prop(self) -> None:
        with mock.patch.object(EntityStore, "set_prop") as set_prop:
            for label, command, _ in self.CASES:
                with self.subTest(label), self.assertRaises(CommandRejected):
                    execute_command(command, self.store)
            set_prop.assert_not_called()

    def test_nan_from_llm_json_is_rejected(self) -> None:
        # Python's json module accepts the non-standard NaN/Infinity literals,
        # so they can arrive through the existing OutputParser.
        raw = json.dumps({"intent": "edit_property", "commands": [edit()], "text": ""}).replace("3000", "NaN")
        response = OutputParser().parse(raw)
        self.assertTrue(math.isnan(response.commands[0].params["value"]))
        with self.assertRaises(CommandRejected) as caught:
            validate_command(response.commands[0], self.store)
        self.assertEqual(caught.exception.reason, RejectionReason.INVALID_VALUE)


class ExecutionBoundaryTest(unittest.TestCase):
    """The command layer cannot reach geometry or HTTP except via EntityStore."""

    FORBIDDEN_IMPORT_PREFIXES = ("services.openscad", "services.geometry", "api", "httpx", "requests", "urllib", "http", "subprocess")

    def test_commands_package_imports(self) -> None:
        for source in (BACKEND_ROOT / "services" / "commands").glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    with self.subTest(file=source.name, module=name):
                        self.assertFalse(
                            any(name == p or name.startswith(p + ".") for p in self.FORBIDDEN_IMPORT_PREFIXES),
                            f"{source.name} imports {name}",
                        )


def _find_openscad() -> str | None:
    import os

    configured = os.environ.get("OPENSCAD_BINARY")
    if configured:
        return configured if (Path(configured).exists() or shutil.which(configured)) else None
    windows_default = Path("C:/Program Files/OpenSCAD/openscad.exe")
    return shutil.which("openscad") or (str(windows_default) if windows_default.exists() else None)


class ExecutionTest(_TempModelTestCase):
    """Accepted commands run through the existing deterministic pipeline (real OpenSCAD)."""

    @classmethod
    def setUpClass(cls) -> None:
        openscad = _find_openscad()
        if openscad is None:
            raise unittest.SkipTest("OpenSCAD CLI not found; set OPENSCAD_BINARY to run the execution tests")
        from services.openscad import engine as engine_module
        from services.openscad.cli_executor import CLIExecutor
        from services.openscad.component_registry import ComponentRegistry
        from services.openscad.engine import OpenSCADEngine
        from services.openscad.stl_parser import StlParser
        from services.openscad.template_renderer import TemplateRenderer

        cls._engine_module = engine_module
        cls._previous_engine = engine_module._current_engine
        cls._scad_dir = Path(tempfile.mkdtemp(prefix="openbim-day27-scad-"))
        engine_module.set_current_engine(OpenSCADEngine(
            ComponentRegistry(), TemplateRenderer(BACKEND_ROOT / "templates"),
            CLIExecutor(openscad, cls._scad_dir, 30), StlParser(),
        ))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._engine_module._current_engine = cls._previous_engine
        shutil.rmtree(cls._scad_dir, ignore_errors=True)

    def test_accepted_commands_update_property_and_geometry(self) -> None:
        sequence = [
            (edit("Length", 3000, "mm"), "Length", 3000),
            (edit("Height", 2800, "mm"), "Height", 2800),
            (edit("Thickness", 200, "mm"), "Thickness", 200),
            (edit("Length", 3.5, "m"), "Length", 3500),
            (edit("Height", 2.8, "m"), "Height", 2800),
        ]
        expected = {"Length": 5000, "Height": 3000, "Thickness": 200}
        urls = {self.store.get_entity(WALL).geometry.url}
        for command, prop, value_mm in sequence:
            with self.subTest(command=command["params"]):
                result = execute_command(command, self.store)
                expected[prop] = value_mm
                self.assertTrue(result.geometry_regenerated)
                self.assertEqual(result.entity.psets[QTO], expected)
                url = result.entity.geometry.url
                self.assertNotIn(url, urls)
                urls.add(url)
                self.assertTrue((self._scad_dir / url.rsplit("/", 1)[1]).is_file())
                self.assertEqual(list(result.entity.geometry.bounds.max),
                                 [float(expected["Length"]), float(expected["Thickness"]), float(expected["Height"])])
                persisted = next(e for e in json.loads(self.model_path.read_text(encoding="utf-8"))["entities"] if e["guid"] == WALL)
                self.assertEqual(persisted["psets"][QTO], expected)
                self.assertEqual(persisted["geometry"]["url"], url)

    def test_execution_path_is_the_existing_pipeline(self) -> None:
        from services.openscad.engine import OpenSCADEngine

        original_render = OpenSCADEngine.render
        render_stacks: list[list[tuple[str, str]]] = []

        def traced_render(engine, component_id, params):
            render_stacks.append([(Path(f.filename).name, f.function) for f in inspect.stack()])
            return original_render(engine, component_id, params)

        with mock.patch.object(EntityStore, "set_prop", autospec=True, side_effect=EntityStore.set_prop) as set_prop, \
                mock.patch.object(OpenSCADEngine, "render", traced_render):
            result = execute_command(edit("Length", 3, "m"), self.store)

        self.assertTrue(result.geometry_regenerated)
        set_prop.assert_called_once_with(self.store, WALL, QTO, "Length", 3000)
        self.assertEqual(len(render_stacks), 1, "exactly one OpenSCAD render per accepted command")

        # Innermost-first stack at the moment OpenSCAD is invoked.
        chain = [frame for frame in render_stacks[0] if frame[0] != "mock.py"]
        expected_order = [
            ("wall_generator.py", "generate"),
            ("geometry_factory.py", "regenerate"),
            ("parametric_geometry.py", "regenerate"),
            ("entity_store.py", "set_prop"),
            ("executor.py", "execute_command"),
        ]
        positions = [chain.index(frame) for frame in expected_order]
        self.assertEqual(positions, sorted(positions), chain[:10])
        self.assertFalse(any(name in {"scad.py", "properties.py"} for name, _ in chain), "no API route in the path")

    def test_regeneration_failure_is_reported_not_hidden(self) -> None:
        # Existing semantics (Day 20/24): property saved, previous geometry kept.
        from services.openscad.engine import OpenSCADEngine
        from services.openscad.models import RenderResult, RenderStatus

        url_before = self.store.get_entity(WALL).geometry.url
        with mock.patch.object(OpenSCADEngine, "render", return_value=RenderResult(status=RenderStatus.FAILED, error="forced")):
            result = execute_command(edit("Height", 2.9, "m"), self.store)
        self.assertFalse(result.geometry_regenerated)
        self.assertEqual(result.entity.psets[QTO]["Height"], 2900)
        self.assertEqual(result.entity.geometry.url, url_before)


if __name__ == "__main__":
    unittest.main()
