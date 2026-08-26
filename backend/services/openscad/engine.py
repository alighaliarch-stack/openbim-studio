"""services/openscad/engine.py

M08 OpenSCAD Engine -- top-level facade.

Orchestrates the full render pipeline:
    ComponentRegistry.get(component_type)
    -> TemplateRenderer.merge_defaults(template, params)
    -> TemplateRenderer.render(template, merged_params)
    -> CLIExecutor.render_to_stl(scad_source)
    -> StlParser.parse_bounds(stl_path)
    -> RenderResult

Synchronous by design (Walking Skeleton / MVP stage), matching
CLIExecutor -- see that module's docstring for the full trade-off
analysis and the migration path back to async if a future concurrent
server needs it. This class's public contract (constructor signature,
`render(component_id, params) -> RenderResult`, and the exception
handling below) is unchanged by that decision; only the calling
convention (`def` instead of `async def`, no `await`) differs from the
previous version.
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

    def render(self, component_id: str, params: ParameterMap) -> RenderResult:
        """Render `component_id` with `params` end-to-end.

        Never raises for expected failure modes (missing component,
        template error, CLI timeout, bad STL); those are captured in the
        returned RenderResult.error.
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
            cli_result = self._executor.render_to_stl(scad_source)
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