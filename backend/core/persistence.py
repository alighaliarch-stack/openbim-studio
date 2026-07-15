"""
core/persistence.py

File-based persistence for the OpenBIM Studio MVP.

Per SDS Section 10.3 (Data Persistence Strategy):
  Write Path: property edit -> update MODEL_STATE in memory -> save_model()
              -> json.dump() to model.json (atomic write recommended)
  Read Path:  backend startup -> load model.json -> populate MODEL_STATE
              -> build spatial tree -> ready to serve API requests

This module owns *only* the raw JSON <-> disk boundary. Domain logic
(entity graph, spatial tree, mutation) lives in services/ifc/entity_store.py.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ModelPersistence:
    """
    Reads and writes the model.json file that backs the M10 IFC Semantic
    Layer's in-memory entity store.
    """

    def __init__(self, model_json_path: Path) -> None:
        self._path = model_json_path

    def load(self) -> dict[str, Any]:
        """
        Load and parse model.json.

        Returns:
            The raw JSON document as a dict with "project" and "entities" keys.

        Raises:
            FileNotFoundError: if model.json does not exist (FR-071).
            json.JSONDecodeError: if the file is corrupt.

        TODO: Add JSON-schema validation of the loaded document before
        handing it to the entity store.
        """
        if not self._path.exists():
            raise FileNotFoundError(f"model.json not found at {self._path}")

        with self._path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, document: dict[str, Any]) -> None:
        """
        Atomically persist `document` to model.json.

        Writes to a temp file in the same directory, then os.replace()s it
        over the target path, so a crash mid-write can never corrupt the
        on-disk model.

        TODO: Add a debounce/queue if write frequency becomes a bottleneck
        (not expected at MVP scale).
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".model_", suffix=".json.tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(document, f, indent=2)
            os.replace(tmp_name, self._path)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

        logger.debug("Persisted model to %s", self._path)
