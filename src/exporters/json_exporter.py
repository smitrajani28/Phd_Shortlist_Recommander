"""
Exports a ShortlistOutput to a human-readable, deterministic JSON file.

Filename strategy:
  <output_dir>/<student_id>.json      when student_id is set
  <output_dir>/output.json            fallback

Output guarantees:
  - indent=2 for human readability
  - sort_keys=True for deterministic diffs
  - UTF-8 encoding
  - Parent directories created automatically
"""

import json
import re
from pathlib import Path

from ..models.recommendation import ShortlistOutput
from ..utils.logger import get_logger

logger = get_logger(__name__)

PIPELINE_VERSION = "1.0.0"


class JsonExporter:
    """
    Serialises a ShortlistOutput model to a JSON file on disk.

    Args:
        output_dir: Base directory for output files. Created if absent.
    """

    def __init__(self, output_dir: Path = Path("sample_output")) -> None:
        self.output_dir = output_dir

    def export(self, output: ShortlistOutput, output_path: Path | None = None) -> Path:
        """
        Write the ShortlistOutput to a JSON file.

        Args:
            output:      Populated ShortlistOutput model.
            output_path: Explicit destination path. When None, the path is
                         derived from output.student_id (or "output" as fallback)
                         inside self.output_dir.

        Returns:
            The Path that was written.
        """
        path = output_path or self._resolve_path(output.student_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure metadata fields are current
        output.recommendation_count = len(output.recommendations)
        output.pipeline_version = PIPELINE_VERSION

        payload = json.loads(output.model_dump_json())   # dict via JSON round-trip
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")

        logger.info(
            "Exported shortlist: %s | Recommendations: %d",
            path, output.recommendation_count,
        )
        return path

    def _resolve_path(self, student_id: str) -> Path:
        """Return <output_dir>/<student_id>.json, falling back to output.json."""
        stem = _slugify(student_id) if student_id else "output"
        return self.output_dir / f"{stem}.json"


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _slugify(text: str) -> str:
    """Convert a name to a safe filename stem: lowercase, spaces → underscores."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug)
    return slug or "output"
