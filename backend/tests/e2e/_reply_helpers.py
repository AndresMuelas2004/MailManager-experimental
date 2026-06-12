"""Shared source-bootstrap helper for the reply-flow E2E tests.

The reply tests need a source message whose body is **retrievable from
the provider**, so the seeded reply quote (``<blockquote>``) is non-empty.
That is the very guarantee the forward tests already depend on, so this
helper reuses ``_forward_helpers.bootstrap_attachment_message`` to obtain
(or create) a self-addressed message known to round-trip a body, and
looks up its ``thread_id`` for the threading assertion. The reply flow
ignores the inherited attachment — only the body is quoted.

Why the obvious cheaper sources do **not** work on the real test
accounts (both learned the hard way):

- *"Reply to the most recent ALL_MAIL row"* — a message previously sent
  to an **external** recipient leaves only a body-less **shadow copy** in
  the Outlook account's ``ALL_MAIL`` (the real body lives on the ``SENT``
  item under a different id). Replying to that shadow yields only the
  attribution line (``build_quoted_body_html`` correctly omits the quote
  when the source body is empty).
- *"Self-send a plain email and reply to its sent id"* — the Outlook test
  account's address is a **Gmail** address, so the send never loops back
  to the Outlook mailbox; only ``SENT`` copies remain, and Graph returns
  an **empty body** for those through the reply-context ``$select=body``
  read. Gmail keeps a single id for the sent+received copy, so it would
  work there — but the source must be reliable for both providers.

The self-addressed message produced by the attachment bootstrap is the
one source proven to round-trip a body for **both** providers (the
forward flow asserts the same ``<blockquote>`` against it).

The leading underscore keeps pytest from collecting this file.
"""
from __future__ import annotations

import os

import psycopg2

from ._forward_helpers import bootstrap_attachment_message


def _db_conn():
    dsn = os.getenv("DATABASE_URL", "").strip()
    return psycopg2.connect(dsn=dsn)


def _fetch_thread_id(account_id: str, provider_message_id: str) -> str | None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT thread_id FROM email_metadata "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def bootstrap_reply_source(
    e2e_client, mailbox_id: str, account_id: str,
) -> tuple[str, str] | None:
    """Return ``(provider_message_id, thread_id)`` of a source message
    whose body is retrievable from the provider, or ``None`` when one
    cannot be obtained (the caller then skips the flow).
    """
    provider_message_id = bootstrap_attachment_message(
        e2e_client, mailbox_id, account_id,
    )
    if provider_message_id is None:
        return None
    thread_id = _fetch_thread_id(account_id, provider_message_id)
    if not thread_id:
        return None
    return provider_message_id, thread_id
