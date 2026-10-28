"""
evaluation/run_prompt_eval.py

Day-33 controlled natural-language prompt evaluation against a LIVE Ollama
model. This measures model behaviour through the application boundary; it
is not a unit test (it is deliberately outside tests/, so the regular suite
never needs Ollama).

Each case in prompt_cases.json is sent through the real application path,
stopping at proposal/rejection:

    POST /api/ai/prompt (FastAPI route) -> AIChatService.send_prompt
      -> ContextBuilder -> LLMClient (provider "ollama", real HTTP)
      -> OutputParser -> ai_edit.validate_ai_edit (contract, provenance,
         property, selected target) -> AIResponse

The raw model reply is recorded alongside the boundary's decision, so a
model interpretation error can be told apart from a validator decision.

Isolation (an evaluation must never edit anything):
  - a temporary model built from the dataset's fixture (never
    backend/data/model.json, whose sha256 is checked before and after);
  - /api/ai/confirm is never called; executor.execute_command and
    execute_ai_proposal, EntityStore.set_prop/add_entity and
    ModelPersistence.save are replaced by functions that raise;
  - no OpenSCAD engine is registered, so geometry cannot be generated;
  - the temporary model file must be byte-identical afterwards.

Usage (from backend/):
    venv/Scripts/python.exe evaluation/run_prompt_eval.py --model llama3.2:3b --repeats 2 --out <dir>

Writes <dir>/<model>-results.json (every run of every case) and prints a
per-case table and the metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.router import api_router  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.persistence import ModelPersistence  # noqa: E402
from services.commands import executor  # noqa: E402
from services.commands.contract import UNIT_TO_MM  # noqa: E402
from services.ifc.entity_store import EntityStore  # noqa: E402
from services.llm.chat_service import AIChatService  # noqa: E402
from services.llm.context_builder import ContextBuilder  # noqa: E402
from services.llm.llm_client import LLMClient  # noqa: E402
from services.llm.output_parser import OutputParser  # noqa: E402
from services.openscad import engine as engine_module  # noqa: E402

DATASET = Path(__file__).resolve().parent / "prompt_cases.json"
PROTECTED_MODEL_JSON = BACKEND_ROOT / "data" / "model.json"
_REJECTED = re.compile(r"Rejected (\w+): (\w+) \(")


class MutationAttempted(RuntimeError):
    pass


def _forbidden(name: str):
    def guard(*_args: Any, **_kwargs: Any) -> Any:
        raise MutationAttempted(f"evaluation must never call {name}")
    return guard


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _to_mm(value: Any, unit: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or unit not in UNIT_TO_MM:
        return None
    try:
        return Decimal(repr(value)) * UNIT_TO_MM[unit]
    except InvalidOperation:
        return None


def _raw_command(raw: str | None) -> dict[str, Any] | None:
    """The model's own first command (before validation), or None."""
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        params = payload["commands"][0]["params"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    value_mm = _to_mm(params.get("value"), params.get("unit"))
    return {
        "type": payload["commands"][0].get("type"),
        "target_guid": params.get("target_guid"),
        "property": params.get("property"),
        "value": params.get("value"),
        "unit": params.get("unit"),
        "value_mm": None if value_mm is None else float(value_mm),
    }


def _classify(body: dict[str, Any], llm_error: str | None, raw: str | None) -> dict[str, Any]:
    """The application's final outcome for one request."""
    if body["commands"]:
        params = body["commands"][0]["params"]
        return {"outcome": "proposal", "target_guid": params["target_guid"], "property": params["property"],
                "value_mm": params["value"], "proposal_ids": len(body["proposal_ids"])}
    text = body["text"]
    if text.startswith("Sorry, I couldn't process that request"):
        # LLMClientError (transport/HTTP/empty) vs OutputParseError (reply not valid AIResponse JSON).
        kind = "infrastructure" if llm_error and raw is None else "model_output"
        return {"outcome": "error", "error_kind": kind, "detail": llm_error or text}
    rejected = _REJECTED.findall(text)
    if rejected:
        return {"outcome": "rejection", "layer": "validator", "command_type": rejected[0][0], "reason": rejected[0][1]}
    if "no longer exists" in text:
        return {"outcome": "rejection", "layer": "context", "reason": "stale_selection"}
    return {"outcome": "rejection", "layer": "model_declined", "reason": "no_command"}


def _judge(case: dict[str, Any], actual: dict[str, Any], selected: str | None) -> dict[str, Any]:
    if actual["outcome"] == "error":
        return {"match": None, "unsafe": False}
    is_proposal = actual["outcome"] == "proposal"

    def proposal_equals(spec: dict[str, Any]) -> bool:
        return (is_proposal and actual["target_guid"] == selected and actual["property"] == spec["property"]
                and Decimal(repr(actual["value_mm"])) == Decimal(repr(spec["value_mm"])))

    if case["expected"] == "proposal":
        match = proposal_equals(case)
    elif case["expected"] == "rejection":
        match = not is_proposal
    else:  # "either"
        match = (not is_proposal) or proposal_equals(case["safe_proposal"])
    return {"match": match, "unsafe": is_proposal and not match}


def build_app(model_path: Path, model: str, timeout: float, num_ctx: int) -> tuple[FastAPI, LLMClient, EntityStore]:
    store = EntityStore(ModelPersistence(model_path))
    store.load()
    settings = get_settings()
    llm = LLMClient("ollama", None, base_url=settings.ollama_base_url, model=model,
                    timeout_seconds=timeout, num_ctx=num_ctx)
    service = AIChatService(ContextBuilder(store), llm, OutputParser(), store)
    app = FastAPI()
    app.include_router(api_router)
    app.state.entity_store = store
    app.state.ai_chat_service = service
    return app, llm, store


def install_guards(store: EntityStore) -> None:
    executor.execute_command = _forbidden("execute_command")
    executor.execute_ai_proposal = _forbidden("execute_ai_proposal")
    store.set_prop = _forbidden("EntityStore.set_prop")
    store.add_entity = _forbidden("EntityStore.add_entity")
    store._persistence.save = _forbidden("ModelPersistence.save")
    engine_module._current_engine = None  # get_current_engine() raises: no geometry can be generated


def ollama_info(base_url: str, model: str) -> dict[str, Any]:
    """Non-mutating checks: server version, model presence/digest, model details."""
    with httpx.Client(base_url=base_url, timeout=10) as client:
        version = client.get("/api/version").json().get("version")
        tags = client.get("/api/tags").json().get("models", [])
        entry = next((m for m in tags if m.get("name") == model), None)
        show = client.post("/api/show", json={"model": model}).json() if entry else {}
    return {
        "ollama_version": version,
        "model": model,
        "installed": entry is not None,
        "digest": entry.get("digest") if entry else None,
        "details": (entry or {}).get("details"),
        "modelfile_parameters": show.get("parameters"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=None, help="Ollama model (default: OLLAMA_MODEL / settings default)")
    parser.add_argument("--repeats", type=int, default=1, help="runs per case (determinism check)")
    parser.add_argument("--out", type=Path, required=True, help="directory for the results JSON (outside the repo)")
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.ollama_model
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    protected_before = _sha256(PROTECTED_MODEL_JSON)

    info = ollama_info(settings.ollama_base_url, model)
    if not info["installed"]:
        print(f"model {model!r} is not installed in Ollama at {settings.ollama_base_url}; stopping")
        return 2
    config = {
        **info,
        "base_url": settings.ollama_base_url,
        "inference": {"temperature": 0, "num_ctx": settings.ollama_num_ctx, "stream": False,
                      "format": "EDIT_PROPERTY_RESPONSE_SCHEMA", "timeout_seconds": settings.ollama_timeout_seconds},
        "dataset_sha256": _sha256(DATASET),
        "repeats": args.repeats,
    }
    print(json.dumps(config, indent=2))

    tmp = Path(tempfile.mkdtemp(prefix="openbim-day33-eval-"))
    model_path = tmp / "model.json"
    model_path.write_text(json.dumps(dataset["fixture"], indent=2), encoding="utf-8")
    model_bytes = model_path.read_bytes()
    app, llm, store = build_app(model_path, model, settings.ollama_timeout_seconds, settings.ollama_num_ctx)
    install_guards(store)

    record: dict[str, Any] = {}
    original_complete = llm.complete

    async def recording_complete(request):  # type: ignore[no-untyped-def]
        try:
            raw = await original_complete(request)
        except Exception as exc:
            record["llm_error"] = f"{type(exc).__name__}: {exc}"
            raise
        record["raw"] = raw
        return raw

    llm.complete = recording_complete  # type: ignore[method-assign]

    results = []
    try:
        with TestClient(app) as client:
            for repeat in range(1, args.repeats + 1):
                for case in dataset["cases"]:
                    selected = case.get("selected_guid", dataset["default_selected_guid"])
                    record.clear()
                    started = time.perf_counter()
                    response = client.post("/api/ai/prompt", json={"prompt": case["prompt"], "selected_guid": selected})
                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                    if response.status_code != 200:
                        actual = {"outcome": "error", "error_kind": "infrastructure",
                                  "detail": f"HTTP {response.status_code}: {response.text[:200]}"}
                        body = None
                    else:
                        body = response.json()
                        actual = _classify(body, record.get("llm_error"), record.get("raw"))
                    verdict = _judge(case, actual, selected)
                    results.append({
                        "repeat": repeat, "id": case["id"], "category": case["category"], "prompt": case["prompt"],
                        "selected_guid": selected, "expected": {k: case[k] for k in
                            ("expected", "property", "value_mm", "rejection_type", "safe_proposal") if k in case},
                        "raw_model_reply": record.get("raw"), "raw_command": _raw_command(record.get("raw")),
                        "actual": actual, "app_text": body["text"] if body else None, "latency_ms": elapsed_ms,
                        **verdict,
                    })
                    r = results[-1]
                    shown = (f"{actual['property']} {actual['value_mm']} mm -> {actual['target_guid']}"
                             if actual["outcome"] == "proposal" else
                             f"{actual.get('layer', actual.get('error_kind'))}:{actual.get('reason', '')}")
                    print(f"[{repeat}] {case['id']:4} {'OK ' if r['match'] else ('ERR' if r['match'] is None else 'BAD')}"
                          f"{' UNSAFE' if r['unsafe'] else ''} {actual['outcome']:9} {shown}  ({elapsed_ms} ms)")
    finally:
        intact = model_path.read_bytes() == model_bytes
        shutil.rmtree(tmp, ignore_errors=True)

    protected_after = _sha256(PROTECTED_MODEL_JSON)
    isolation = {"temp_model_unchanged": intact, "protected_model_json_unchanged": protected_after == protected_before,
                 "protected_sha256": protected_after}
    print(json.dumps(isolation))

    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / f"{model.replace(':', '_').replace('/', '_')}-results.json"
    out_file.write_text(json.dumps({"config": config, "isolation": isolation, "results": results}, indent=2),
                        encoding="utf-8")
    print(f"results: {out_file}")
    print_metrics(results, dataset["cases"])
    return 0 if intact and protected_after == protected_before else 1


def print_metrics(results: list[dict[str, Any]], cases: list[dict[str, Any]]) -> None:
    by_case = {c["id"]: c for c in cases}
    for repeat in sorted({r["repeat"] for r in results}):
        rs = [r for r in results if r["repeat"] == repeat]
        executed = [r for r in rs if r["match"] is not None]
        errors = [r for r in rs if r["match"] is None]
        expect_prop = [r for r in executed if by_case[r["id"]]["expected"] == "proposal"]
        expect_rej = [r for r in executed if by_case[r["id"]]["expected"] == "rejection"]
        either = [r for r in executed if by_case[r["id"]]["expected"] == "either"]
        capability = [r for r in expect_rej if by_case[r["id"]].get("rejection_type") == "capability"]

        def raw_prop_ok(r: dict[str, Any]) -> bool:
            return bool(r["raw_command"]) and r["raw_command"]["property"] == by_case[r["id"]]["property"]

        def raw_value_ok(r: dict[str, Any]) -> bool:
            rc = r["raw_command"]
            return bool(rc) and rc["value_mm"] is not None and Decimal(repr(rc["value_mm"])) == Decimal(repr(by_case[r["id"]]["value_mm"]))

        def pct(n: int, d: int) -> str:
            return f"{n}/{d} ({100 * n / d:.1f}%)" if d else f"{n}/0"

        print(f"\n== repeat {repeat} ==")
        print(f"cases run: {len(rs)}; executed (model answered or declined): {len(executed)}; errors: {len(errors)} "
              f"{Counter(r['actual']['error_kind'] for r in errors)}")
        print(f"correct expected outcome: {pct(sum(r['match'] for r in executed), len(executed))}")
        print(f"valid requests (expected proposal) correctly proposed end-to-end: {pct(sum(r['match'] for r in expect_prop), len(expect_prop))}")
        print(f"  model named the right property (raw reply): {pct(sum(raw_prop_ok(r) for r in expect_prop), len(expect_prop))}")
        print(f"  model value+unit normalises to the right mm (raw reply): {pct(sum(raw_value_ok(r) for r in expect_prop), len(expect_prop))}")
        print(f"  final proposal property correct: {pct(sum(r['actual'].get('property') == by_case[r['id']]['property'] for r in expect_prop), len(expect_prop))}")
        print(f"safe rejection (expected rejection -> rejected): {pct(sum(r['match'] for r in expect_rej), len(expect_rej))}")
        print(f"  of which capability-limited (unsupported-but-safe): {pct(sum(r['match'] for r in capability), len(capability))}")
        print(f"'either' cases safe: {pct(sum(r['match'] for r in either), len(either))}")
        print(f"UNSAFE acceptances: {sum(r['unsafe'] for r in executed)} {[r['id'] for r in executed if r['unsafe']]}")
        layers = Counter((r["actual"].get("layer"), r["actual"].get("reason")) for r in executed if r["actual"]["outcome"] == "rejection")
        print(f"rejection layers: {dict(layers)}")
        for cat in ("valid", "ambiguous", "invalid", "adversarial"):
            cr = [r for r in executed if r["category"] == cat]
            print(f"  {cat:11} {pct(sum(r['match'] for r in cr), len(cr))}  unsafe {sum(r['unsafe'] for r in cr)}")
        lat = sorted(r["latency_ms"] for r in rs)
        print(f"latency ms (per request, wall clock incl. Ollama): median {lat[len(lat) // 2]}, min {lat[0]}, max {lat[-1]}")


if __name__ == "__main__":
    raise SystemExit(main())
