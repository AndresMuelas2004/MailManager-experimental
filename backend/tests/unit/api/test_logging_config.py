"""
Unit tests for the logging configuration builder (#11a).

``build_logging_config`` is a pure function, so these tests assert on the
returned dict without mutating global logging state.
"""

from __future__ import annotations

from api.logging_config import build_logging_config


def test_build_logging_config_applies_level_to_root_and_uvicorn():
    cfg = build_logging_config("WARNING")
    assert cfg["root"]["level"] == "WARNING"
    assert cfg["loggers"]["uvicorn"]["level"] == "WARNING"
    assert cfg["loggers"]["uvicorn.access"]["level"] == "WARNING"


def test_build_logging_config_has_timestamped_formatter():
    cfg = build_logging_config("INFO")
    fmt = cfg["formatters"]["standard"]
    assert "%(asctime)s" in fmt["format"]
    assert "%(levelname)s" in fmt["format"]
    assert "%(name)s" in fmt["format"]


def test_build_logging_config_keeps_existing_loggers_and_disables_propagation():
    """Both flags are deliberately non-default; flipping them silently breaks prod logs."""
    cfg = build_logging_config("INFO")
    assert cfg["disable_existing_loggers"] is False
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert cfg["loggers"][name]["propagate"] is False
