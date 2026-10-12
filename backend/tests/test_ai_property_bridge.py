"""
tests/test_ai_property_bridge.py

Day-31 AI -> property execution bridge tests.

Flow under test (real FastAPI routes, AIChatService, contract, provenance,
executor, EntityStore and OpenSCAD on a temporary model; only Ollama is
replaced, by httpx.MockTransport):

    POST /api/ai/prompt   -> validated proposal + server-issued proposal id
    POST /api/ai/confirm  -> every check again -> execute_command() once
                          -> EntityStore.set_prop() (Day-28 atomic edit)
                          -> deterministic geometry regeneration

Engineering regression tests only; they are not a research evaluation of
the language model. The OpenSCAD CLI is required (skipped if not found,
like the Day-27/28 execution tests). backend/data/model.json is never
read or written.

Run from backend/:
    venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.router import api_router  # noqa: E402
from core.persistence import ModelPersistence  # noqa: E402
from services.commands import executor  # noqa: E402
from services.ifc.entity_store import EntityStore  # noqa: E402
from services.llm import chat_service as chat_service_module  # noqa: E402
from services.llm.chat_service import AIChatService  # noqa: E402
from services.llm.context_builder import ContextBuilder  # noqa: E402
from services.llm.output_parser import OutputParser  # noqa: E402
from services.openscad.engine import OpenSCADEngine  # noqa: E402
from services.openscad.models import RenderResult, RenderStatus  # noqa: E402
from tests.test_command_contract import _find_openscad  # noqa: E402
from tests.test_llm_intent_parsing import (  # noqa: E402
    DOOR,
    QTO,
    STOREY,
    WALL,
    _OllamaStub,
    edit_params,
    llm_reply,
    ollama_body,
    ollama_client,
)

OTHER_WALL = "day31-wall-002"
SEED_QTO = {"Length": 5000, "Height": 3000, "Thickness": 200}


def _seed_document() -> dict:
    def wall(guid: str, name: str) -> dict:
        return {
            "guid": guid, "type": "IfcWall", "name": name, "parent_guid": STOREY,
            "psets": {QTO: dict(SEED_QTO)},
            "geometry": {"type": "stl", "url": f"/geometry/{guid}-seed.stl",
                         "bounds": {"min": [0, 0, 0], "max": [5000, 200, 3000]}},
        }

    return {
        "project": {"guid": "day31-project", "name": "Day 31 AI property bridge", "description": ""},
        "entities": [
            {"guid": STOREY, "type": "IfcBuildingStorey", "name": "Ground Floor",
             "children_guids": [WALL, OTHER_WALL, DOOR]},
            wall(WALL, "Selected Wall"),
            wall(OTHER_WALL, "Other Wall"),
            {"guid": DOOR, "type": "IfcDoor", "name": "Test Door", "parent_guid": STOREY,
             "psets": {"Qto_DoorBaseQuantities": {"Width": 900, "Height": 2100}}},
        ],
    }


def edit_reply(prop: str, value: object, unit: str, guid: str = WALL) -> str:
    return llm_reply({"type": "EditProperty", "params": edit_params(prop, value, unit, guid)})


class AIPropertyBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        openscad = _find_openscad()
        if openscad is None:
            raise unittest.SkipTest("OpenSCAD CLI not found; set OPENSCAD_BINARY to run the Day-31 bridge tests")
        from services.openscad import engine as engine_module
        from services.openscad.cli_executor import CLIExecutor
        from services.openscad.component_registry import ComponentRegistry
        from services.openscad.stl_parser import StlParser
        from services.openscad.template_renderer import TemplateRenderer

        cls._engine_module = engine_module
        cls._previous_engine = engine_module._current_engine
        cls._scad_dir = Path(tempfile.mkdtemp(prefix="openbim-day31-scad-"))
        engine_module.set_current_engine(OpenSCADEngine(
            ComponentRegistry(), TemplateRenderer(BACKEND_ROOT / "templates"),
            CLIExecutor(openscad, cls._scad_dir, 30), StlParser(),
        ))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._engine_module._current_engine = cls._previous_engine
        shutil.rmtree(cls._scad_dir, ignore_errors=True)

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="openbim-day31-"))
        self.model_path = self._tmp / "model.json"
        self.model_path.write_text(json.dumps(_seed_document(), indent=2), encoding="utf-8")
        self.store = EntityStore(ModelPersistence(self.model_path))
        self.store.load()
        self.disk_before = self.model_path.read_bytes()
        self.walls_before = {g: self.store.get_entity(g).model_dump(mode="json") for g in (WALL, OTHER_WALL, DOOR)}

        # The fake Ollama replies with whatever self.reply holds at call time.
        self.reply = llm_reply()
        stub = _OllamaStub(lambda _req: httpx.Response(200, json=ollama_body(self.reply)))
        self.service = AIChatService(ContextBuilder(self.store), ollama_client(stub), OutputParser(), self.store)
        app = FastAPI()
        app.include_router(api_router)
        app.state.entity_store = self.store
        app.state.ai_chat_service = self.service
        self.client = TestClient(app)

        patcher = mock.patch.object(chat_service_module.logger, "warning")
        patcher.start()
        self.addCleanup(patcher.stop)

        # Spies that still run the real code.
        self.execute = mock.patch.object(executor, "execute_command", wraps=executor.execute_command).start()
        self.set_prop = mock.patch.object(EntityStore, "set_prop", autospec=True, side_effect=EntityStore.set_prop).start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- helpers --------------------------------------------------------

    def propose(self, prompt: str, reply: str, selected: str | None = WALL) -> dict:
        self.reply = reply
        response = self.client.post("/api/ai/prompt", json={"prompt": prompt, "selected_guid": selected})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["proposal_ids"]), len(body["commands"]))
        return body

    def proposal_id(self, prompt: str = "Make the selected wall 3.2 m high",
                    reply: str | None = None, selected: str | None = WALL) -> str:
        body = self.propose(prompt, reply or edit_reply("Height", 3.2, "m"), selected)
        (proposal_id,) = body["proposal_ids"]
        return proposal_id

    def confirm(self, proposal_id: str, selected: str | None = WALL) -> httpx.Response:
        return self.client.post("/api/ai/confirm", json={"proposal_id": proposal_id, "selected_guid": selected})

    def assert_rejected_proposal(self, body: dict, reason: str) -> None:
        self.assertEqual((body["intent"], body["commands"], body["proposal_ids"]), ("query", [], []))
        self.assertIn(f": {reason} (", body["text"])

    def assert_error(self, response: httpx.Response, status: int, code: str) -> None:
        self.assertEqual(response.status_code, status, response.text)
        detail = response.json()["detail"]
        self.assertEqual((detail["success"], detail["code"]), (False, code))

    def assert_nothing_changed(self) -> None:
        self.set_prop.assert_not_called()
        self.assertEqual(self.model_path.read_bytes(), self.disk_before)
        for guid, before in self.walls_before.items():
            self.assertEqual(self.store.get_entity(guid).model_dump(mode="json"), before)

    # -- 1, 10: confirmed proposal executes through the existing pipeline --

    def test_confirmed_proposal_executes_once_through_the_existing_pipeline(self) -> None:
        proposal_id = self.proposal_id()
        self.set_prop.assert_not_called()  # proposing alone changes nothing

        with mock.patch.object(OpenSCADEngine, "render", autospec=True, side_effect=OpenSCADEngine.render) as render:
            response = self.confirm(proposal_id)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertIn("Applied: set Qto_WallBaseQuantities.Height of", body["text"])
        self.assertIn("3200 mm", body["text"])
        # Exactly one command execution, one set_prop and one OpenSCAD render.
        self.assertEqual(self.execute.call_count, 1)
        self.set_prop.assert_called_once_with(self.store, WALL, QTO, "Height", 3200)
        self.assertEqual(render.call_count, 1)

        # Semantic property and regenerated geometry, in memory, on disk and in the response.
        entity = self.store.get_entity(WALL)
        self.assertEqual(entity.psets[QTO], {**SEED_QTO, "Height": 3200})
        self.assertNotEqual(entity.geometry.url, self.walls_before[WALL]["geometry"]["url"])
        self.assertTrue((self._scad_dir / entity.geometry.url.rsplit("/", 1)[1]).is_file())
        self.assertEqual(list(entity.geometry.bounds.max), [5000.0, 200.0, 3200.0])
        self.assertEqual(body["entity"]["psets"][QTO]["Height"], 3200)
        self.assertEqual(body["entity"]["geometry"]["url"], entity.geometry.url)
        persisted = {e["guid"]: e for e in json.loads(self.model_path.read_text(encoding="utf-8"))["entities"]}
        self.assertEqual(persisted[WALL]["psets"][QTO]["Height"], 3200)
        self.assertEqual(persisted[WALL]["geometry"]["url"], entity.geometry.url)
        # Other entities untouched.
        for guid in (OTHER_WALL, DOOR):
            self.assertEqual(self.store.get_entity(guid).model_dump(mode="json"), self.walls_before[guid])

    def test_metre_and_millimetre_proposals_execute_as_canonical_millimetres(self) -> None:
        for prompt, reply, prop, value_mm in [
            ("Make the wall 4 metres long", edit_reply("Length", 4, "m"), "Length", 4000),
            ("Make the wall 250 mm thick", edit_reply("Thickness", 250, "mm"), "Thickness", 250),
        ]:
            with self.subTest(prompt):
                response = self.confirm(self.proposal_id(prompt, reply))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.store.get_entity(WALL).psets[QTO][prop], value_mm)

    # -- 2: proposal without confirmation ---------------------------------

    def test_unconfirmed_proposal_changes_nothing(self) -> None:
        body = self.propose("Make the selected wall 3.2 m high", edit_reply("Height", 3.2, "m"))
        self.assertEqual(body["intent"], "edit_property")
        self.assertEqual(body["commands"][0]["params"], edit_params("Height", 3200, "mm"))
        self.assertIn("Proposed (not applied)", body["text"])
        self.execute.assert_not_called()
        self.assert_nothing_changed()

    # -- 3: no selected wall ---------------------------------------------

    def test_no_selection_is_rejected_at_proposal_and_at_confirmation(self) -> None:
        body = self.propose("Make the wall 3.2 m high", edit_reply("Height", 3.2, "m"), selected=None)
        self.assert_rejected_proposal(body, "no_selection")
        self.assertIn("select the wall", body["text"])

        # Deselected between proposing and confirming.
        response = self.confirm(self.proposal_id(), selected=None)
        self.assert_error(response, 422, "no_selection")
        self.execute.assert_not_called()
        self.assert_nothing_changed()

    # -- 4: selected GUID differs from proposed GUID ------------------------

    def test_target_other_than_the_selected_wall_is_rejected(self) -> None:
        # "the wall" (no selection wording): the Day-30 gap, now closed by the Day-31 policy.
        body = self.propose("Make the wall 3.2 m high", edit_reply("Height", 3.2, "m", OTHER_WALL))
        self.assert_rejected_proposal(body, "target_mismatch")
        body = self.propose("Make the selected wall 3.2 m high", edit_reply("Height", 3.2, "m", OTHER_WALL))
        self.assert_rejected_proposal(body, "target_mismatch")

        # Selection changed to another existing wall between proposing and confirming.
        response = self.confirm(self.proposal_id(), selected=OTHER_WALL)
        self.assert_error(response, 422, "target_mismatch")
        self.execute.assert_not_called()
        self.assert_nothing_changed()

    # -- 5: target exists but is not an IfcWall -----------------------------

    def test_non_wall_target_is_rejected(self) -> None:
        door_edit = llm_reply({"type": "EditProperty", "params": {
            "target_guid": DOOR, "pset": "Qto_DoorBaseQuantities", "property": "Height", "value": 2.2, "unit": "m"}})
        for selected in (DOOR, WALL):
            with self.subTest(selected=selected):
                body = self.propose("Make the selected door 2.2 m high", door_edit, selected=selected)
                self.assert_rejected_proposal(body, "unsupported_target_type")
        body = self.propose("Make the selected wall 3.2 m high", edit_reply("Height", 3.2, "m", DOOR), selected=DOOR)
        self.assert_rejected_proposal(body, "unsupported_target_type")
        self.execute.assert_not_called()
        self.assert_nothing_changed()

    # -- 6: unsupported command type ----------------------------------------

    def test_unsupported_commands_are_never_proposed_or_executed(self) -> None:
        with mock.patch.object(OpenSCADEngine, "render") as render:
            for command in (
                {"type": "CreateComponent", "params": {"component_type": "wall", "code": "cube(1);"}},
                {"type": "UpdateSelection", "params": {"guid": OTHER_WALL}},
            ):
                with self.subTest(command["type"]):
                    body = self.propose("Create a wall 3 m long", llm_reply(command))
                    self.assert_rejected_proposal(body, "unsupported_command")
            # An id the server never issued (e.g. invented by a model) cannot be confirmed.
            self.reply = json.dumps({"intent": "edit_property", "commands": [], "text": "ok",
                                     "proposal_ids": ["llm-invented"]})
            body = self.client.post("/api/ai/prompt", json={"prompt": "Make the wall 3 m high",
                                                            "selected_guid": WALL}).json()
            self.assertEqual(body["proposal_ids"], [])
            self.assert_error(self.confirm("llm-invented"), 404, "proposal_not_found")
        render.assert_not_called()
        self.execute.assert_not_called()
        self.assert_nothing_changed()

    # -- 7: wrong numeric value or unit provenance ---------------------------

    def test_wrong_value_or_unit_is_rejected(self) -> None:
        cases = [
            ("Make the selected wall 3.2 m high", edit_reply("Height", 3200, "m"), "value_out_of_range"),
            ("Make the wall 0.08 m thick", edit_reply("Thickness", 80, "m"), "quantity_mismatch"),
            ("Make the selected wall 3.2 m high", edit_reply("Height", 3.3, "m"), "quantity_mismatch"),
            ("Make the wall taller", edit_reply("Height", 3500, "mm"), "no_explicit_quantity"),
            ("Make the wall 2 m longer", edit_reply("Length", 2, "m"), "relative_change_unsupported"),
        ]
        for prompt, reply, reason in cases:
            with self.subTest(prompt=prompt, reply=reply):
                self.assert_rejected_proposal(self.propose(prompt, reply), reason)
        self.execute.assert_not_called()
        self.assert_nothing_changed()

    # -- 8: property mismatch -----------------------------------------------

    def test_property_mismatch_is_rejected(self) -> None:
        for prompt, reply in [
            ("Make the selected wall 3.2 m high", edit_reply("Length", 3.2, "m")),
            ("Make the wall 4 m long", edit_reply("Height", 4, "m")),
        ]:
            with self.subTest(prompt=prompt):
                self.assert_rejected_proposal(self.propose(prompt, reply), "property_mismatch")
        self.execute.assert_not_called()
        self.assert_nothing_changed()

    # -- 9: execution / regeneration failure ---------------------------------

    def test_regeneration_failure_reports_no_success_and_changes_nothing(self) -> None:
        proposal_id = self.proposal_id()
        failed = RenderResult(status=RenderStatus.FAILED, error="forced by test")
        with mock.patch.object(OpenSCADEngine, "render", return_value=failed):
            response = self.confirm(proposal_id)

        self.assert_error(response, 502, "geometry_regeneration_failed")
        self.assertNotIn("Applied", response.text)
        self.assertEqual(self.execute.call_count, 1)  # attempted once, rejected atomically
        self.assertEqual(self.model_path.read_bytes(), self.disk_before)
        self.assertEqual(self.store.get_entity(WALL).model_dump(mode="json"), self.walls_before[WALL])
        # The proposal was used up by the attempt; it is not retried implicitly.
        self.assert_error(self.confirm(proposal_id), 404, "proposal_not_found")
        self.assertEqual(self.execute.call_count, 1)

    def test_target_deleted_after_proposal_is_rejected_at_confirmation(self) -> None:
        proposal_id = self.proposal_id()
        # Simulate the wall disappearing from the model (not via any API).
        del self.store._entities[WALL]
        response = self.confirm(proposal_id)
        self.assert_error(response, 422, "unknown_entity")
        self.execute.assert_not_called()
        self.set_prop.assert_not_called()

    # -- 11: double submission -----------------------------------------------

    def test_repeated_confirmation_executes_only_once(self) -> None:
        proposal_id = self.proposal_id()
        first = self.confirm(proposal_id)
        url_after_first = self.store.get_entity(WALL).geometry.url
        second = self.confirm(proposal_id)

        self.assertEqual(first.status_code, 200, first.text)
        self.assert_error(second, 404, "proposal_not_found")
        self.assertEqual(self.execute.call_count, 1)
        self.assertEqual(self.set_prop.call_count, 1)
        self.assertEqual(self.store.get_entity(WALL).geometry.url, url_after_first)

    def test_concurrent_confirmations_execute_once(self) -> None:
        import asyncio

        proposal_id = self.proposal_id()

        async def confirm_many() -> list[int]:
            transport = httpx.ASGITransport(app=self.client.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                responses = await asyncio.gather(*[
                    client.post("/api/ai/confirm", json={"proposal_id": proposal_id, "selected_guid": WALL})
                    for _ in range(10)])
            return sorted(r.status_code for r in responses)

        self.assertEqual(asyncio.run(confirm_many()), [200] + [404] * 9)
        self.assertEqual(self.execute.call_count, 1)
        self.assertEqual(self.set_prop.call_count, 1)

    # -- M1: stale proposals ---------------------------------------------------

    def test_property_changed_by_manual_edit_makes_the_proposal_stale(self) -> None:
        proposal_id = self.proposal_id()  # Height 3000 -> 3200 proposed
        manual = self.client.post(f"/api/properties/{WALL}",
                                  json={"pset_name": QTO, "property_name": "Height", "value": 3500})
        self.assertEqual(manual.status_code, 200, manual.text)
        self.set_prop.reset_mock()
        disk_after_manual = self.model_path.read_bytes()
        wall_after_manual = self.store.get_entity(WALL).model_dump(mode="json")

        with self.assertNoLogs(executor.logger, "INFO"):
            response = self.confirm(proposal_id)

        self.assert_error(response, 422, "stale_proposal")
        self.assertIn("the wall changed since this proposal was made", response.json()["detail"]["detail"])
        self.assertIn("ask for a new proposal", response.json()["detail"]["detail"])
        self.execute.assert_not_called()
        self.set_prop.assert_not_called()
        self.assertEqual(self.store.get_entity(WALL).psets[QTO]["Height"], 3500)  # manual edit kept
        self.assertEqual(self.store.get_entity(WALL).model_dump(mode="json"), wall_after_manual)
        self.assertEqual(self.model_path.read_bytes(), disk_after_manual)

    def test_unchanged_target_property_still_executes(self) -> None:
        proposal_id = self.proposal_id()  # Height proposal
        # A different property of the same wall changes; Height does not.
        manual = self.client.post(f"/api/properties/{WALL}",
                                  json={"pset_name": QTO, "property_name": "Length", "value": 4500})
        self.assertEqual(manual.status_code, 200, manual.text)
        response = self.confirm(proposal_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.get_entity(WALL).psets[QTO], {"Length": 4500, "Height": 3200, "Thickness": 200})

    # -- M2: expiry ----------------------------------------------------------

    def test_expired_proposal_is_not_executed(self) -> None:
        from services.commands import ai_edit

        clock = mock.Mock(return_value=5000.0)
        with mock.patch.object(ai_edit, "monotonic", clock):
            valid_id = self.proposal_id()
            expired_id = self.proposal_id()
            clock.return_value = 5000.0 + ai_edit.PROPOSAL_TTL_SECONDS
            response = self.confirm(expired_id)
            self.assert_error(response, 404, "proposal_not_found")
            self.assertNotIn("Traceback", response.text)
            self.execute.assert_not_called()
            self.assert_nothing_changed()

            clock.return_value = 5000.0 + ai_edit.PROPOSAL_TTL_SECONDS - 1
            self.assertEqual(self.confirm(valid_id).status_code, 200)
        self.assertEqual(self.execute.call_count, 1)

    # -- L2: audit log -------------------------------------------------------

    def test_successful_ai_edit_is_logged_once_and_failures_are_not(self) -> None:
        prompt = "Make the selected wall 3.2 m high"
        proposal_id = self.proposal_id(prompt)
        with self.assertLogs(executor.logger, "INFO") as logs:
            self.assertEqual(self.confirm(proposal_id).status_code, 200)
        (record,) = logs.output
        geometry_url = self.store.get_entity(WALL).geometry.url
        for field in (f"proposal={proposal_id}", f"target={WALL}", f"property={QTO}.Height",
                      "value=3200 mm", f"geometry={geometry_url}"):
            self.assertIn(field, record)
        self.assertNotIn(prompt, record)

        failed = RenderResult(status=RenderStatus.FAILED, error="forced by test")
        with mock.patch.object(OpenSCADEngine, "render", return_value=failed), \
                self.assertNoLogs(executor.logger, "INFO"):
            self.assert_error(self.confirm(self.proposal_id("Make the wall 4 m long", edit_reply("Length", 4, "m"))),
                              502, "geometry_regeneration_failed")

    # -- 12: non-AI property editing still works -------------------------------

    def test_manual_property_edit_still_works(self) -> None:
        response = self.client.post(f"/api/properties/{WALL}",
                                    json={"pset_name": QTO, "property_name": "Length", "value": 4000})
        self.assertEqual(response.status_code, 200, response.text)
        entity = self.store.get_entity(WALL)
        self.assertEqual(entity.psets[QTO]["Length"], 4000)
        self.assertEqual(list(entity.geometry.bounds.max), [4000.0, 200.0, 3000.0])
        self.execute.assert_not_called()  # the manual path does not go through the AI executor

        # ... and alongside an AI edit on the same wall.
        self.assertEqual(self.confirm(self.proposal_id()).status_code, 200)
        self.assertEqual(self.store.get_entity(WALL).psets[QTO], {"Length": 4000, "Height": 3200, "Thickness": 200})


class ProposalRegistryTest(unittest.TestCase):
    """The pending-proposal store is single-use, bounded and time-limited (no OpenSCAD needed)."""

    def setUp(self) -> None:
        from services.llm.models import Command, CommandType

        self.command = Command(type=CommandType.EDIT_PROPERTY, params=edit_params("Height", 3200, "mm"))

    def test_single_use_and_bounded(self) -> None:
        from services.commands.ai_edit import ProposalRegistry

        registry = ProposalRegistry(capacity=2)
        ids = [registry.add(self.command, "Make the wall 3.2 m high", WALL, 3000) for _ in range(3)]
        self.assertEqual(len(set(ids)), 3)
        self.assertIsNone(registry.take(ids[0]))  # oldest dropped at capacity
        pending = registry.take(ids[1])
        self.assertEqual((pending.command, pending.request_text, pending.selected_guid, pending.original_value),
                         (self.command, "Make the wall 3.2 m high", WALL, 3000))
        self.assertIsNone(registry.take(ids[1]))  # already taken
        self.assertIsNotNone(registry.take(ids[2]))

    def test_expires_after_600_seconds(self) -> None:
        from services.commands import ai_edit

        self.assertEqual(ai_edit.PROPOSAL_TTL_SECONDS, 600.0)
        registry = ai_edit.ProposalRegistry()
        clock = mock.Mock(return_value=1000.0)
        with mock.patch.object(ai_edit, "monotonic", clock):
            fresh, old = (registry.add(self.command, "Make the wall 3.2 m high", WALL, 3000) for _ in range(2))
            clock.return_value = 1000.0 + 599.9
            self.assertIsNotNone(registry.take(fresh))  # still valid just before 600 s
            clock.return_value = 1000.0 + 600.0
            self.assertIsNone(registry.take(old))  # expired at 600 s
            self.assertIsNone(registry.take(old))  # and gone


if __name__ == "__main__":
    unittest.main()
