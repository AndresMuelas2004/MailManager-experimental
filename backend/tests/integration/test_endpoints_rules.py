"""
Integration tests for the internal rule engine surface (carpetas-y-reglas).

Real routers + services + DB. All rule endpoints are local-only (the apply
itself runs in the background worker, which is OFF in the suite — so an enqueued
job stays ``pending`` and the apply-status read reflects it deterministically).
Covers ``/rules`` CRUD, the two 422 shapes (schema ``{"detail"}`` on create vs
the service ``{"error":{"rule_validation_error"}}`` on a PATCH that clears both
conditions), target-folder ownership, and the apply enqueue + status read.
"""

from __future__ import annotations


_FOLDERS_URL = "/folders"
_RULES_URL = "/rules"
_RANDOM_UUID = "99999999-9999-4000-a000-999999999999"


def _create_folder(client, name="Universidad"):
    resp = client.post(_FOLDERS_URL, json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["folder_id"]


def _create_rule(client, folder_id, **overrides):
    body = {"match_from_email": "boss@example.com", "target_folder_id": folder_id}
    body.update(overrides)
    resp = client.post(_RULES_URL, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_and_list_rule(test_client):
    fid = _create_folder(test_client)
    rule = _create_rule(test_client, fid, match_subject_contains="invoice")
    assert rule["match_from_email"] == "boss@example.com"
    assert rule["target_folder_id"] == fid

    listed = test_client.get(_RULES_URL)
    assert listed.status_code == 200
    assert any(r["rule_id"] == rule["rule_id"] for r in listed.json())


def test_create_rule_normalises_from_email(test_client):
    fid = _create_folder(test_client)
    rule = _create_rule(test_client, fid, match_from_email="  Boss@Example.COM ")
    assert rule["match_from_email"] == "boss@example.com"


def test_create_rule_without_condition_is_422_detail_envelope(test_client):
    fid = _create_folder(test_client)
    resp = test_client.post(_RULES_URL, json={"target_folder_id": fid})
    # Schema-level 422 uses FastAPI's ``{"detail": [...]}`` envelope, NOT the
    # project ``{"error": {...}}`` one.
    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_create_rule_with_foreign_target_folder_is_404(test_client):
    resp = test_client.post(
        _RULES_URL, json={"match_from_email": "a@b.com", "target_folder_id": _RANDOM_UUID},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "folder_not_found"


def test_patch_clearing_both_conditions_is_rule_validation_error(test_client):
    fid = _create_folder(test_client)
    # Rule has ONLY the from_email condition; clearing it leaves no condition →
    # the service re-validation returns the project error envelope (not FastAPI).
    rule = _create_rule(test_client, fid, match_from_email="boss@example.com")
    resp = test_client.patch(
        f"{_RULES_URL}/{rule['rule_id']}", json={"match_from_email": None},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "rule_validation_error"


def test_patch_keeping_condition_updates(test_client):
    fid = _create_folder(test_client)
    rule = _create_rule(test_client, fid, match_from_email="boss@example.com")
    resp = test_client.patch(
        f"{_RULES_URL}/{rule['rule_id']}", json={"match_from_email": "new@example.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["match_from_email"] == "new@example.com"


def test_missing_rule_is_404(test_client):
    resp = test_client.get(f"{_RULES_URL}/{_RANDOM_UUID}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "rule_not_found"


def test_delete_rule_then_get_is_404(test_client):
    fid = _create_folder(test_client)
    rule = _create_rule(test_client, fid)
    deleted = test_client.delete(f"{_RULES_URL}/{rule['rule_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert test_client.get(f"{_RULES_URL}/{rule['rule_id']}").status_code == 404


def test_apply_status_none_when_never_applied(test_client):
    fid = _create_folder(test_client)
    rule = _create_rule(test_client, fid)
    resp = test_client.get(f"{_RULES_URL}/{rule['rule_id']}/apply-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "none"
    assert body["active"] is False


def test_apply_enqueues_job_and_status_reflects_it(test_client):
    fid = _create_folder(test_client)
    rule = _create_rule(test_client, fid)

    applied = test_client.post(f"{_RULES_URL}/{rule['rule_id']}/apply")
    assert applied.status_code == 200
    # The worker is OFF in the suite, so the freshly enqueued job stays pending.
    assert applied.json()["status"] == "pending"
    assert applied.json()["active"] is True

    status = test_client.get(f"{_RULES_URL}/{rule['rule_id']}/apply-status")
    assert status.json()["status"] == "pending"
    assert status.json()["active"] is True


def test_deleting_target_folder_cascades_the_rule(test_client):
    # ``rules.target_folder_id`` is ``ON DELETE CASCADE`` (migration 0045), so
    # deleting a folder removes every rule pointing at it — verified through the
    # API rather than raw SQL.
    fid = _create_folder(test_client, "ToDelete")
    rule = _create_rule(test_client, fid)
    assert test_client.delete(f"{_FOLDERS_URL}/{fid}").status_code == 200
    assert test_client.get(f"{_RULES_URL}/{rule['rule_id']}").status_code == 404
