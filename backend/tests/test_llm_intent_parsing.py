"""
tests/test_llm_intent_parsing.py

Day-29 LLM intent parsing tests (Ollama provider, proposal only).

The Ollama HTTP API is replaced by httpx.MockTransport, so neither Ollama
nor a model is needed. The real LLMClient, OutputParser, ContextBuilder,
Day-27 contract and EntityStore (on a temporary model) are used.

Proves:
- the Ollama request shape (model, no streaming, temperature 0, explicit
  num_ctx, JSON-schema format) and that provider failures become
  LLMClientError and a graceful AIResponse;
- every LLM command is validated by validate_command(); only accepted
  EditProperty commands are returned, so CreateComponent can never reach
  the frontend's /api/scad/render path;
- the proposal path never calls execute_command(), EntityStore.set_prop()
  or OpenSCAD, and leaves the model unchanged.

backend/data/model.json is never read or written.

Run from backend/:
    venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
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
from core.config import Settings  # noqa: E402
from core.persistence import ModelPersistence  # noqa: E402
from services.commands import contract, executor  # noqa: E402
from services.ifc.entity_store import EntityStore  # noqa: E402
from services.llm import chat_service as chat_service_module  # noqa: E402
from services.llm.chat_service import AIChatService  # noqa: E402
from services.llm.context_builder import ContextBuilder  # noqa: E402
from services.llm.llm_client import EDIT_PROPERTY_RESPONSE_SCHEMA, LLMClient, LLMClientError  # noqa: E402
from services.llm.models import AIRequest, CommandType, Intent, ModelContext  # noqa: E402
from services.llm.output_parser import OutputParser  # noqa: E402
from services.openscad.engine import OpenSCADEngine  # noqa: E402

QTO = "Qto_WallBaseQuantities"
WALL = "day29-wall-001"
DOOR = "day29-door-001"
STOREY = "day29-storey-001"
BASE_URL = "http://ollama.test"
MODEL = "llama3.2:3b"


def _seed_document() -> dict:
    return {
        "project": {"guid": "day29-project", "name": "Day 29 intent parsing", "description": ""},
        "entities": [
            {"guid": STOREY, "type": "IfcBuildingStorey", "name": "Ground Floor", "children_guids": [WALL, DOOR]},
            {
                "guid": WALL, "type": "IfcWall", "name": "Test Wall", "parent_guid": STOREY,
                "psets": {QTO: {"Length": 5000, "Height": 3000, "Thickness": 200}},
                "geometry": {"type": "stl", "url": "/geometry/day29-seed.stl",
                             "bounds": {"min": [0, 0, 0], "max": [5000, 200, 3000]}},
            },
            {
                "guid": DOOR, "type": "IfcDoor", "name": "Test Door", "parent_guid": STOREY,
                "psets": {"Qto_DoorBaseQuantities": {"Width": 900, "Height": 2100}},
            },
        ],
    }


def edit_params(prop: str = "Height", value: object = 3.2, unit: object = "m", guid: str = WALL) -> dict:
    return {"target_guid": guid, "pset": QTO, "property": prop, "value": value, "unit": unit}


def llm_reply(*commands: dict, intent: str = "edit_property", text: str = "ok") -> str:
    """The JSON text a model would put in Ollama's message.content."""
    return json.dumps({"intent": intent, "commands": list(commands), "text": text})


def ollama_body(content: str, **extra) -> dict:
    return {"model": MODEL, "message": {"role": "assistant", "content": content}, "done": True, **extra}


class _OllamaStub:
    """httpx.MockTransport handler recording requests to a fake Ollama."""

    def __init__(self, respond) -> None:
        self._respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result = self._respond(request)
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def replying(content: str) -> _OllamaStub:
    return _OllamaStub(lambda _req: httpx.Response(200, json=ollama_body(content)))


def ollama_client(stub: _OllamaStub, **kwargs) -> LLMClient:
    return LLMClient("ollama", None, base_url=BASE_URL, model=MODEL, transport=stub.transport, **kwargs)


def _request(prompt: str = "Make the selected wall 3.2 m high.") -> AIRequest:
    return AIRequest(prompt=prompt, model_context=ModelContext(selected_guid=WALL))


class OllamaClientTest(unittest.IsolatedAsyncioTestCase):
    """The Ollama branch of LLMClient: request shape and failure mapping."""

    async def test_success_returns_content_that_parses_and_validates(self) -> None:
        stub = replying(llm_reply({"type": "EditProperty", "params": edit_params()}))
        raw = await ollama_client(stub).complete(_request())

        (sent,) = stub.requests
        self.assertEqual((sent.method, str(sent.url)), ("POST", f"{BASE_URL}/api/chat"))
        payload = json.loads(sent.content)
        self.assertEqual(payload["model"], MODEL)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["options"], {"temperature": 0, "num_ctx": 8192})
        self.assertEqual(payload["format"], EDIT_PROPERTY_RESPONSE_SCHEMA)
        self.assertEqual([m["role"] for m in payload["messages"]], ["system", "user"])
        self.assertIn("Make the selected wall 3.2 m high.", payload["messages"][1]["content"])
        self.assertIn(WALL, payload["messages"][1]["content"])
        self.assertNotIn("Authorization", sent.headers)

        (command,) = OutputParser().parse(raw).commands
        self.assertEqual(command.type, CommandType.EDIT_PROPERTY)
        tmp = Path(tempfile.mkdtemp(prefix="openbim-day29-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "model.json").write_text(json.dumps(_seed_document()), encoding="utf-8")
        store = EntityStore(ModelPersistence(tmp / "model.json"))
        store.load()
        self.assertEqual(contract.validate_command(command, store).value_mm, 3200)

    async def test_no_api_key_is_required(self) -> None:
        raw = await ollama_client(replying(llm_reply(intent="query"))).complete(_request())
        self.assertIn('"commands": []', raw)

    async def test_provider_failures_raise_llm_client_error(self) -> None:
        cases = [
            ("connection refused", lambda r: httpx.ConnectError("connection refused", request=r), "Could not reach Ollama"),
            ("timeout", lambda r: httpx.ReadTimeout("timed out", request=r), "did not respond within"),
            ("HTTP 500", lambda _r: httpx.Response(500, text="boom"), "HTTP 500"),
            ("model missing 404", lambda _r: httpx.Response(404, json={"error": "model not found"}), "HTTP 404"),
            ("body not JSON", lambda _r: httpx.Response(200, text="<html>"), "not JSON"),
            ("body not an object", lambda _r: httpx.Response(200, json=["x"]), "empty response"),
            ("no message", lambda _r: httpx.Response(200, json={"done": True}), "empty response"),
            ("empty content", lambda _r: httpx.Response(200, json=ollama_body("  ")), "empty response"),
            ("context filled", lambda _r: httpx.Response(200, json=ollama_body("{}", prompt_eval_count=8192)),
             "context too large"),
        ]
        for label, respond, message in cases:
            with self.subTest(label):
                with self.assertRaises(LLMClientError) as caught:
                    await ollama_client(_OllamaStub(respond)).complete(_request())
                self.assertIn(message, str(caught.exception))

    async def test_missing_configuration_raises_llm_client_error(self) -> None:
        with self.assertRaises(LLMClientError):
            await LLMClient("ollama", None).complete(_request())

    def test_schema_matches_the_day27_contract(self) -> None:
        item = EDIT_PROPERTY_RESPONSE_SCHEMA["properties"]["commands"]["items"]
        params = item["properties"]["params"]["properties"]
        self.assertEqual(item["properties"]["type"]["enum"], [CommandType.EDIT_PROPERTY.value])
        (editable,) = contract.EDITABLE_PROPERTIES.values()
        self.assertEqual(params["pset"]["enum"], list(editable))
        self.assertEqual(set(params["property"]["enum"]), set(editable[QTO]))
        self.assertEqual(set(params["unit"]["enum"]), set(contract.UNIT_TO_MM))
        self.assertEqual(set(params), set(contract.EditPropertyParams.model_fields))

    def test_settings_defaults_select_llama_and_keep_existing_providers(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            settings = Settings()
        self.assertEqual(settings.llm_provider, "anthropic")
        self.assertEqual(settings.ollama_base_url, "http://127.0.0.1:11434")
        self.assertEqual(settings.ollama_model, "llama3.2:3b")
        self.assertEqual(settings.ollama_num_ctx, 8192)
        env = {"LLM_PROVIDER": "ollama", "OLLAMA_MODEL": "gemma3:4b", "OLLAMA_TIMEOUT_SECONDS": "30"}
        with mock.patch.dict("os.environ", env, clear=True):
            settings = Settings()
        self.assertEqual((settings.llm_provider, settings.ollama_model, settings.ollama_timeout_seconds),
                         ("ollama", "gemma3:4b", 30.0))


class _ProposalTestCase(unittest.IsolatedAsyncioTestCase):
    """Real AIChatService over a temporary model, with a stubbed Ollama."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="openbim-day29-"))
        self.model_path = self._tmp / "model.json"
        self.model_path.write_text(json.dumps(_seed_document(), indent=2), encoding="utf-8")
        self.store = EntityStore(ModelPersistence(self.model_path))
        self.store.load()
        self.before = self.model_path.read_bytes()

        # Keep expected provider-failure warnings out of the test output.
        patcher = mock.patch.object(chat_service_module.logger, "warning")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def service(self, stub: _OllamaStub) -> AIChatService:
        return AIChatService(ContextBuilder(self.store), ollama_client(stub), OutputParser(), self.store)

    async def ask(self, stub: _OllamaStub, prompt: str = "Make the selected wall 3.2 m high.", selected=WALL):
        return await self.service(stub).send_prompt(prompt, selected)

    def assert_model_unchanged(self) -> None:
        self.assertEqual(self.model_path.read_bytes(), self.before)
        self.assertEqual(self.store.get_entity(WALL).psets[QTO], {"Length": 5000, "Height": 3000, "Thickness": 200})


class ProposalTest(_ProposalTestCase):
    async def test_valid_command_becomes_a_canonical_millimetre_proposal(self) -> None:
        response = await self.ask(replying(llm_reply({"type": "EditProperty", "params": edit_params()},
                                                     text="I have changed the wall")))

        self.assertEqual(response.intent, Intent.EDIT_PROPERTY)
        (proposal,) = response.commands
        self.assertEqual(proposal.type, CommandType.EDIT_PROPERTY)
        self.assertEqual(proposal.params, edit_params("Height", 3200, "mm"))
        self.assertIn("Proposed (not applied)", response.text)
        self.assertIn("Height", response.text)
        self.assertIn("3200 mm", response.text)
        self.assertNotIn("I have changed the wall", response.text)
        # The canonical proposal re-validates to the same edit.
        self.assertEqual(contract.validate_command(proposal, self.store).value_mm, 3200)
        self.assert_model_unchanged()

    async def test_provider_failures_give_a_graceful_response(self) -> None:
        cases = [
            ("connection", _OllamaStub(lambda r: httpx.ConnectError("refused", request=r))),
            ("timeout", _OllamaStub(lambda r: httpx.ReadTimeout("slow", request=r))),
            ("HTTP 500", _OllamaStub(lambda _r: httpx.Response(500))),
            ("invalid JSON content", replying("not json {")),
            ("wrong shape", replying(json.dumps({"commands": "x"}))),
        ]
        for label, stub in cases:
            with self.subTest(label):
                response = await self.ask(stub)
                self.assertEqual((response.intent, response.commands), (Intent.QUERY, []))
                self.assertIn("couldn't process that request", response.text)
        self.assert_model_unchanged()

    async def test_no_command_reply_is_reported_as_no_change(self) -> None:
        response = await self.ask(replying(llm_reply(intent="query", text="Deleting walls is not supported.")),
                                  prompt="Delete the wall.")
        self.assertEqual((response.intent, response.commands), (Intent.QUERY, []))
        self.assertEqual(response.text, "Deleting walls is not supported.\nNo change was proposed.")
        self.assert_model_unchanged()

    async def test_stale_selection_is_reported_without_calling_the_llm(self) -> None:
        stub = replying(llm_reply())
        response = await self.ask(stub, selected="wall-deleted-meanwhile")
        self.assertEqual((response.intent, response.commands), (Intent.QUERY, []))
        self.assertIn("no longer exists", response.text)
        self.assertEqual(stub.requests, [])


class RejectionTest(_ProposalTestCase):
    """Commands the contract rejects never come back as commands."""

    CASES = [
        ("CreateComponent", {"type": "CreateComponent", "params": {"component_type": "wall", "length": 1}},
         contract.RejectionReason.UNSUPPORTED_COMMAND),
        ("UpdateSelection", {"type": "UpdateSelection", "params": {"guid": WALL}},
         contract.RejectionReason.UNSUPPORTED_COMMAND),
        ("missing target_guid", {"type": "EditProperty",
                                 "params": {k: v for k, v in edit_params().items() if k != "target_guid"}},
         contract.RejectionReason.MALFORMED_COMMAND),
        ("extra SCAD parameter", {"type": "EditProperty", "params": {**edit_params(), "scad": "cube(1);"}},
         contract.RejectionReason.MALFORMED_COMMAND),
        ("unsupported property", {"type": "EditProperty", "params": edit_params("Width")},
         contract.RejectionReason.UNSUPPORTED_PROPERTY),
        ("invalid unit", {"type": "EditProperty", "params": edit_params(unit="ft")},
         contract.RejectionReason.UNSUPPORTED_UNIT),
        ("string value", {"type": "EditProperty", "params": edit_params(value="3.2")},
         contract.RejectionReason.INVALID_VALUE),
        ("zero", {"type": "EditProperty", "params": edit_params(value=0)},
         contract.RejectionReason.VALUE_OUT_OF_RANGE),
        ("negative", {"type": "EditProperty", "params": edit_params(value=-1)},
         contract.RejectionReason.VALUE_OUT_OF_RANGE),
        ("over 100 m", {"type": "EditProperty", "params": edit_params("Length", 500, "m")},
         contract.RejectionReason.VALUE_OUT_OF_RANGE),
        ("nonexistent entity", {"type": "EditProperty", "params": edit_params(guid="wall-invented-by-llm")},
         contract.RejectionReason.UNKNOWN_ENTITY),
        ("door target", {"type": "EditProperty", "params": edit_params(guid=DOOR)},
         contract.RejectionReason.UNSUPPORTED_TARGET_TYPE),
    ]

    async def test_rejections(self) -> None:
        for label, command, reason in self.CASES:
            with self.subTest(label):
                response = await self.ask(replying(llm_reply(command)))
                self.assertEqual((response.intent, response.commands), (Intent.QUERY, []))
                self.assertIn(f"Rejected {command['type']}: {reason.value}", response.text)
                self.assertIn("No change was proposed.", response.text)
        self.assert_model_unchanged()

    async def test_only_the_valid_command_of_a_mixed_reply_is_returned(self) -> None:
        create = {"type": "CreateComponent", "params": {"component_type": "door"}}
        valid = {"type": "EditProperty", "params": edit_params("Thickness", 250, "mm")}
        # Day-30: the prompt must state the proposed quantity (provenance check).
        response = await self.ask(replying(llm_reply(create, valid)), prompt="Make the wall 250 mm thick.")
        self.assertEqual([c.type for c in response.commands], [CommandType.EDIT_PROPERTY])
        self.assertEqual(response.commands[0].params["value"], 250)
        self.assertIn("Rejected CreateComponent", response.text)
        self.assert_model_unchanged()


class ProposalOnlyInvariantTest(_ProposalTestCase):
    """The AI path proposes; it never executes, edits or renders."""

    async def test_proposal_path_never_executes(self) -> None:
        replies = [
            llm_reply({"type": "EditProperty", "params": edit_params()}),
            llm_reply({"type": "CreateComponent", "params": {"component_type": "wall"}}),
        ]
        with mock.patch.object(executor, "execute_command") as execute, \
             mock.patch("services.commands.execute_command") as execute_reexport, \
             mock.patch.object(EntityStore, "set_prop") as set_prop, \
             mock.patch.object(OpenSCADEngine, "render") as render:
            for reply in replies:
                await self.ask(replying(reply))
        for spy in (execute, execute_reexport, set_prop, render):
            spy.assert_not_called()
        self.assert_model_unchanged()

    def test_ai_modules_cannot_reach_execution_or_geometry(self) -> None:
        forbidden_imports = ("services.commands.executor", "services.openscad", "services.geometry", "api")
        # Day-31: also the AI executor entry point and the model-mutating
        # EntityStore / persistence methods.
        forbidden_names = {"execute_command", "execute_ai_proposal", "set_prop", "add_entity", "save",
                           "executor", "render"}
        # Day-31: ai_edit.py is the proposal-validation module chat_service imports.
        modules = [Path("services", "llm", n) for n in
                   ("chat_service.py", "llm_client.py", "output_parser.py", "context_builder.py")]
        for module_path in modules + [Path("services", "commands", "ai_edit.py")]:
            name = module_path.as_posix()
            tree = ast.parse((BACKEND_ROOT / module_path).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for module in modules:
                    with self.subTest(file=name, module=module):
                        self.assertFalse(any(module == p or module.startswith(p + ".") for p in forbidden_imports))
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        self.assertNotIn(alias.name, forbidden_names, f"{name} imports {alias.name}")
                if isinstance(node, (ast.Attribute, ast.Name)):
                    used = node.attr if isinstance(node, ast.Attribute) else node.id
                    self.assertNotIn(used, forbidden_names, f"{name} uses {used}")


class RouteTest(_ProposalTestCase):
    """POST /api/ai/prompt cannot return a command that would reach /api/scad/render."""

    def client_for(self, stub: _OllamaStub) -> TestClient:
        app = FastAPI()
        app.include_router(api_router)
        app.state.entity_store = self.store
        app.state.ai_chat_service = self.service(stub)
        return TestClient(app)

    async def test_create_component_is_not_returned_and_nothing_renders(self) -> None:
        stub = replying(llm_reply({"type": "CreateComponent",
                                   "params": {"component_type": "wall", "code": "cube(1);"}},
                                  intent="create_component"))
        with mock.patch.object(OpenSCADEngine, "render") as render:
            response = self.client_for(stub).post("/api/ai/prompt", json={"prompt": "Create a door", "selected_guid": WALL})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # AIChatPanel only routes CreateComponent to /api/scad/render; none is returned.
        self.assertEqual(body["commands"], [])
        self.assertEqual(body["intent"], "query")
        self.assertIn("unsupported_command", body["text"])
        render.assert_not_called()
        self.assert_model_unchanged()

    async def test_route_returns_only_edit_property_proposals(self) -> None:
        stub = replying(llm_reply({"type": "EditProperty", "params": edit_params("Length", 4, "m")}))
        # Day-31: an AI edit needs the target wall to be selected, so the
        # request now sends the selection (as AIChatPanel does).
        with mock.patch.object(EntityStore, "set_prop") as set_prop:
            client = self.client_for(stub)
            response = client.post("/api/ai/prompt", json={"prompt": "Make the wall 4 metres long", "selected_guid": WALL})
            unselected = client.post("/api/ai/prompt", json={"prompt": "Make the wall 4 metres long"})
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c["type"] for c in body["commands"]], ["EditProperty"])
        self.assertEqual(body["commands"][0]["params"], edit_params("Length", 4000, "mm"))
        self.assertEqual((unselected.status_code, unselected.json()["commands"]), (200, []))
        self.assertIn("no_selection", unselected.json()["text"])
        set_prop.assert_not_called()
        self.assert_model_unchanged()

    async def test_stale_selection_is_not_a_server_error(self) -> None:
        response = self.client_for(replying(llm_reply())).post(
            "/api/ai/prompt", json={"prompt": "Make it 3 m high", "selected_guid": "gone"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["commands"], [])


if __name__ == "__main__":
    unittest.main()
