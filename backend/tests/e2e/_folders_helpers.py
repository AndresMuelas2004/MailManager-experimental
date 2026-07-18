"""Shared helpers for the folders + rules E2E flow (Gmail + Outlook mirrors).

Both ``test_folders_flow_gmail.py`` and ``test_folders_flow_outlook.py`` drive the
same folder / rule surface against a real provider, so the DB-side reads and the
apply-status poll live here (mirrors ``_favorites_helpers.py``).
"""

from __future__ import annotations

import os
import time

import psycopg2


def _db_conn():
    dsn = os.getenv("DATABASE_URL", "").strip()
    return psycopg2.connect(dsn=dsn)


def _select_from_email(account_id: str, provider_message_id: str) -> str | None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT from_email FROM email_metadata "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None
    finally:
        conn.close()


def _folder_listing_ids(client, folder_id: str) -> list[str]:
    resp = client.get(f"/folders/{folder_id}/emails", params={"limit": 50})
    assert resp.status_code == 200, resp.text
    return [item["provider_message_id"] for item in resp.json()["items"]]


def _poll_apply_until_done(client, rule_id: str, *, attempts: int = 15, delay: float = 2.0):
    """Poll the apply-status until the job goes inactive (completed/failed), or
    return ``None`` if it is still active after the bounded budget (the worker
    may not be draining in this environment — the caller then skips)."""
    for _ in range(attempts):
        resp = client.get(f"/rules/{rule_id}/apply-status")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if not body["active"]:
            return body
        time.sleep(delay)
    return None
