"""Unit tests for the folder Pydantic schemas (carpetas-y-reglas).

Pure validation contracts — no service, DB or provider access. Mirrors the
``VirtualMailboxCreate`` schema-boundary rules: ``extra="forbid"`` rejects
unknown keys, the name is stripped BEFORE ``min_length`` runs (a whitespace-only
name collapses to 422 here, not to a silently-empty folder), and the bounds
match the backend Pydantic ``Field`` limits that are authoritative over the
client guard.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.folder import (
    FolderAssignRequest,
    FolderCreate,
    FolderUpdate,
)


class TestFolderCreate:
    def test_minimal_valid_payload(self):
        model = FolderCreate(name="Universidad")
        assert model.name == "Universidad"
        assert model.color is None

    def test_name_is_stripped_before_validation(self):
        # Strip runs in ``mode="before"`` so surrounding whitespace never
        # reaches storage and a padded name is not a distinct folder.
        assert FolderCreate(name="  Facturas  ").name == "Facturas"

    def test_whitespace_only_name_collapses_to_422(self):
        # After the strip the name is "" — ``min_length=1`` rejects it. Without
        # the pre-strip a "   " name would create a blank folder.
        with pytest.raises(ValidationError):
            FolderCreate(name="   ")

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            FolderCreate(name="")

    def test_name_over_max_length_rejected(self):
        with pytest.raises(ValidationError):
            FolderCreate(name="x" * 121)

    def test_color_over_max_length_rejected(self):
        with pytest.raises(ValidationError):
            FolderCreate(name="Work", color="c" * 33)

    def test_unknown_key_rejected_by_extra_forbid(self):
        with pytest.raises(ValidationError):
            FolderCreate(name="Work", folderId="sneaky")

    def test_color_is_optional(self):
        assert FolderCreate(name="Work", color="#ff0000").color == "#ff0000"


class TestFolderUpdate:
    def test_all_fields_optional_empty_body_is_valid(self):
        # A partial PATCH with nothing set is a service-tolerated no-op.
        model = FolderUpdate()
        assert model.name is None
        assert model.color is None

    def test_name_stripped(self):
        assert FolderUpdate(name="  Renamed  ").name == "Renamed"

    def test_whitespace_only_name_rejected(self):
        with pytest.raises(ValidationError):
            FolderUpdate(name="   ")

    def test_unknown_key_rejected(self):
        with pytest.raises(ValidationError):
            FolderUpdate(unexpected=1)


class TestFolderAssignRequest:
    def test_requires_folder_id(self):
        with pytest.raises(ValidationError):
            FolderAssignRequest()

    def test_empty_folder_id_rejected(self):
        with pytest.raises(ValidationError):
            FolderAssignRequest(folder_id="")

    def test_unknown_key_rejected(self):
        with pytest.raises(ValidationError):
            FolderAssignRequest(folder_id="f1", extra="x")
