"""Project-wide pytest fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_searchbug_circuit_breaker():
    """Reset the SearchBug account-error breaker between every test so the
    tripped state from one test can't leak into the next. The breaker is a
    process-level flag in production by design — tests need explicit cleanup.
    """
    from services import searchbug_service
    searchbug_service.reset_circuit_breaker_for_tests()
    yield
    searchbug_service.reset_circuit_breaker_for_tests()


@pytest.fixture(autouse=True)
def _reset_run_metrics_columns_cache():
    """Same idea for the run_metrics column discovery cache."""
    from services import dedup_service
    dedup_service._reset_run_metrics_columns_cache_for_tests()
    yield
    dedup_service._reset_run_metrics_columns_cache_for_tests()
