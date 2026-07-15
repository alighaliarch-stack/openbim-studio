"""
services/openscad — M08 OpenSCAD Engine.

Translates parametric component definitions and user parameters into
OpenSCAD source, executes the CLI, and returns geometry (SDS Section 4 / M08).

Public contract:
    render(template, params) -> RenderResult (stl path + bounds)
"""

from services.openscad.cli_executor import CLIExecutor
from services.openscad.component_registry import ComponentRegistry
from services.openscad.engine import OpenSCADEngine
from services.openscad.models import ComponentTemplate, RenderResult
from services.openscad.stl_parser import StlParser
from services.openscad.template_renderer import TemplateRenderer

__all__ = [
    "CLIExecutor",
    "ComponentRegistry",
    "ComponentTemplate",
    "OpenSCADEngine",
    "RenderResult",
    "StlParser",
    "TemplateRenderer",
]
