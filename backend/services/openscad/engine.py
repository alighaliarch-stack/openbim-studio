"""
services/openscad/engine.py

M08 OpenSCAD Engine — top-level facade.

Orchestrates the full render pipeline described in SDS Flow 1:
    ComponentRegistry.get(component_type)
    -> TemplateRenderer.render(template, params)
    -> CLIExecutor.render_to_stl(scad_source)
    -> StlParser.parse_bounds(stl_path)
    -> RenderResult

This is the single object api/scad.py depends on; it composes the other
M08 collaborators so callers don't need to wire them individually.
"""

from __future__ import annotations

import time

from services.openscad.cli_executor import CLIExecutor, OpenSCADTimeoutError
from services.openscad.component_registry import ComponentRegistry
from services.openscad.models import ParameterMap, RenderResult, RenderStatus
from services.openscad.stl_parser import StlParseError, StlParser
from services.openscad.template_renderer import TemplateRenderer, TemplateRenderError


class OpenSCADEngine:
    """Facade tying together ComponentRegistry, TemplateRenderer, CLIExecutor, StlParser."""

    def __init__(
        self,
        registry: ComponentRegistry,
        renderer: TemplateRenderer,
        executor: CLIExecutor,
        parser: StlParser,
    ) -> None:
        self._registry = registry
        self._renderer = renderer
        self._executor = executor
        self._parser = parser

    async def render(self, component_id: str, params: ParameterMap) -> RenderResult:
        """
        Render `component_id` with `params` end-to-end.

        Never raises for expected failure modes (missing component,
        template error, CLI timeout, bad STL); those are captured in the
        returned RenderResult.error so api/scad.py can return a clean
        500-with-message per SDS 9.3 Error Response Format.

        TODO: Wire M10 entity creation/geometry-update here once the AI
        Chat Module (M09) needs a single call that both renders and
        updates the entity graph (Flow 2, "CreateComponent" branch).
        """
        start = time.perf_counter()

        try:
            template = self._registry.get(component_id)
        except KeyError as exc:
            return RenderResult(status=RenderStatus.FAILED, error=str(exc))

        merged_params = self._renderer.merge_defaults(template, params)

        try:
            scad_source = self._renderer.render(template, merged_params)
        except TemplateRenderError as exc:
            return RenderResult(status=RenderStatus.FAILED, error=str(exc))

        try:
            cli_result = await self._executor.render_to_stl(scad_source)
        except OpenSCADTimeoutError as exc:
            return RenderResult(status=RenderStatus.FAILED, error=str(exc))

        if not cli_result.success or cli_result.stl_path is None:
            return RenderResult(status=RenderStatus.FAILED, error=cli_result.stderr)

        try:
            bounds = self._parser.parse_bounds(cli_result.stl_path)
        except StlParseError as exc:
            return RenderResult(status=RenderStatus.FAILED, error=str(exc))

        elapsed_ms = (time.perf_counter() - start) * 1000
        return RenderResult(
            status=RenderStatus.SUCCESS,
            stl_path=str(cli_result.stl_path),
            bounds=bounds,
            render_time_ms=elapsed_ms,
        )
