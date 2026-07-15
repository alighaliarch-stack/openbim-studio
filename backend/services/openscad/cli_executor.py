"""
services/openscad/cli_executor.py

M08 OpenSCAD Engine — CLIExecutor (FR-051).

Writes a rendered .scad source to a temp file and invokes the OpenSCAD CLI
to produce an STL file, with a timeout to prevent hung processes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CLIResult:
    """Outcome of a single OpenSCAD CLI invocation."""

    success: bool
    stl_path: Path | None
    stdout: str
    stderr: str
    returncode: int | None


class OpenSCADTimeoutError(RuntimeError):
    """Raised when the OpenSCAD CLI does not finish within the timeout."""


class CLIExecutor:
    """
    Thin async wrapper around the `openscad` CLI binary.

    TODO(Phase 2): Pool / queue concurrent renders (RenderQueue in the
    full architecture doc uses Celery); MVP runs renders serially per
    request.
    """

    def __init__(self, openscad_binary: str, tmp_dir: Path, timeout_seconds: int = 30) -> None:
        self._binary = openscad_binary
        self._tmp_dir = tmp_dir
        self._timeout_seconds = timeout_seconds
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

    async def render_to_stl(self, scad_source: str) -> CLIResult:
        """
        Write `scad_source` to a temp .scad file and invoke:
            openscad -o output.stl input.scad

        Returns:
            CLIResult with success flag, resolved stl_path (if any), and
            captured stdout/stderr for error reporting.

        Raises:
            OpenSCADTimeoutError: if the process exceeds
            `self._timeout_seconds` (FR-051: "Timeout after 30 seconds").
        """
        job_id = uuid.uuid4().hex
        scad_path = self._tmp_dir / f"{job_id}.scad"
        stl_path = self._tmp_dir / f"{job_id}.stl"

        scad_path.write_text(scad_source, encoding="utf-8")

        try:
            process = await asyncio.create_subprocess_exec(
                self._binary,
                "-o",
                str(stl_path),
                str(scad_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            # openscad binary not installed / not on PATH. Surface as a
            # normal failed CLIResult rather than crashing the request, so
            # api/scad.py can return a clean 200-with-error response.
            return CLIResult(
                success=False,
                stl_path=None,
                stdout="",
                stderr=(
                    f"OpenSCAD binary '{self._binary}' not found. "
                    "Install OpenSCAD and/or set OPENSCAD_BINARY. "
                    f"({exc})"
                ),
                returncode=None,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            raise OpenSCADTimeoutError(
                f"OpenSCAD render exceeded {self._timeout_seconds}s timeout"
            ) from exc

        success = process.returncode == 0 and stl_path.exists()
        return CLIResult(
            success=success,
            stl_path=stl_path if success else None,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            returncode=process.returncode,
        )
