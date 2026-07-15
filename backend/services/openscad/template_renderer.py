"""
services/openscad/template_renderer.py

M08 OpenSCAD Engine — TemplateRenderer (FR-050).

Renders a Jinja2 .scad.j2 template with a validated ParameterMap into a
concrete, syntactically valid OpenSCAD (.scad) source file.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from services.openscad.models import ComponentTemplate, ParameterMap


class TemplateRenderError(RuntimeError):
    """Raised when Jinja2 rendering fails (e.g. missing required param)."""


class TemplateRenderer:
    """
    Wraps a Jinja2 Environment scoped to the backend/templates directory.
    """

    def __init__(self, templates_dir: Path) -> None:
        self._templates_dir = templates_dir
        # StrictUndefined ensures missing params raise loudly rather than
        # silently rendering as empty strings (SRS FR-050 acceptance
        # criteria: "No Jinja2 errors on missing params -> use defaults",
        # so callers must merge defaults from ParamField before calling).
        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template: ComponentTemplate, params: ParameterMap) -> str:
        """
        Render `template.template_filename` with `params`, returning the
        concrete .scad source text.

        Raises:
            TemplateRenderError: if the template is missing or a required
            placeholder has no value in `params`.
        """
        try:
            jinja_template = self._env.get_template(template.template_filename)
            return jinja_template.render(**params)
        except Exception as exc:  # noqa: BLE001 - surfaced as domain error
            raise TemplateRenderError(
                f"Failed to render template '{template.template_filename}': {exc}"
            ) from exc

    def merge_defaults(self, template: ComponentTemplate, params: ParameterMap) -> ParameterMap:
        """
        Fill in any parameters missing from `params` using the template's
        ParamField defaults, so render() always receives a complete map.
        """
        merged = {field.name: field.default for field in template.param_schema}
        merged.update(params)
        return merged
