"""
Integration-level tests for ShortlistPipeline.
"""

import pytest
from unittest.mock import MagicMock, patch
from src.pipelines.shortlist_pipeline import ShortlistPipeline
from src.utils.config import Settings


class TestShortlistPipeline:
    def setup_method(self):
        self.settings = Settings()
        self.pipeline = ShortlistPipeline(settings=self.settings)

    def test_pipeline_is_instantiated(self):
        assert self.pipeline is not None
        assert len(self.pipeline.retrievers) == 3

    def test_run_raises_on_missing_file(self, tmp_path):
        """run() should raise FileNotFoundError for a non-existent input."""
        missing = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError):
            self.pipeline.run(input_path=missing)
