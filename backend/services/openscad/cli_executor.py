"""services/openscad/cli_executor.py

M08 OpenSCAD Engine -- CLIExecutor (FR-051).

Writes a rendered .scad source to a temp file and invokes the OpenSCAD
CLI to produce an STL file, with a timeout to prevent hung processes.

Synchronous by design (Walking Skeleton / MVP stage): this subsystem has
no current consumer that runs inside an event loop, so a blocking
subprocess call has no concurrency cost to pay for today. See the
architectural review preceding this change for the full trade-off
analysis.

Migration path back to async, if a future concurrent server needs it:
swap `subprocess.run(...)` below for `asyncio.create_subprocess_exec`
(+ `asyncio.wait_for` for the timeout, catching `asyncio.TimeoutError`
instead of `subprocess.TimeoutExpired`), and restore `async def` on
`render_to_stl`. The public contract -- `CLIResult`, the constructor
signature, and `OpenSCADTimeoutError` -- would not need to change; only
the method's internals and its calling convention would. `OpenSCADEngine
.render()` would need the corresponding `async def`/`await` restored at
that point too.
"""

from __future__ import annotations

import logging
import subprocess
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
    """Thin synchronous wrapper around the `openscad` CLI binary.

    TODO(Phase 2): Pool/queue concurrent renders if this is ever called
    from a context handling multiple simultaneous requests (unchanged
    from the original design note -- still applies once a real API
    layer exists).
    """

    def __init__(self, openscad_binary: str, tmp_dir: Path, timeout_seconds: int = 30) -> None:
        self._binary = openscad_binary
        self._tmp_dir = tmp_dir
        self._timeout_seconds = timeout_seconds
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

    def render_to_stl(self, scad_source: str) -> CLIResult:
        """Write `scad_source` to a temp .scad file and invoke:
            openscad -o output.stl input.scad

        Returns:
            CLIResult with success flag, resolved stl_path (if any), and
            captured stdout/stderr for error reporting.

        Raises:
            OpenSCADTimeoutError: if the process exceeds
            `self._timeout_seconds` (FR-051: "Timeout after 30 seconds").
            `subprocess.run`'s own timeout handling kills the child
            process automatically before this is raised, matching the
            previous asyncio implementation's explicit `process.kill()`.
        """
        job_id = uuid.uuid4().hex
        scad_path = self._tmp_dir / f"{job_id}.scad"
        stl_path = self._tmp_dir / f"{job_id}.stl"

        scad_path.write_text(scad_source, encoding="utf-8")

        try:
            result = subprocess.run(
                [self._binary, "-o", str(stl_path), str(scad_path)],
                capture_output=True,
                timeout=self._timeout_seconds,
            )
        except FileNotFoundError as exc:
            # openscad binary not installed / not on PATH. Surface as a
            # normal failed CLIResult rather than crashing the caller,
            # so api/scad.py (once it exists) can return a clean
            # 200-with-error response.
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
        except subprocess.TimeoutExpired as exc:
            raise OpenSCADTimeoutError(
                f"OpenSCAD render exceeded {self._timeout_seconds}s timeout"
            ) from exc

        success = result.returncode == 0 and stl_path.exists()
        return CLIResult(
            success=success,
            stl_path=stl_path if success else None,
            stdout=result.stdout.decode("utf-8", errors="replace"),
            stderr=result.stderr.decode("utf-8", errors="replace"),
            returncode=result.returncode,
        )