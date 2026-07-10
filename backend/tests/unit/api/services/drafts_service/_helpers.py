"""Constantes, builders y helper de patch compartidos por los tests espejo de ``drafts_service``."""

from __future__ import annotations

from datetime import datetime

from core.email import EmailManager
from tests.shared.email_fakes import FakeEmailClient


_MAILBOX_ID = "mb1"
_ACCOUNT_ID = "acc1"
_USER_ID = "user1"
_PROVIDER = "gmail"
_LABEL = f"{_MAILBOX_ID}__{_ACCOUNT_ID}"


def _fake_account(account_id=_ACCOUNT_ID, provider=_PROVIDER) -> dict:
    return {
        "account_id": account_id,
        "mailbox_id": _MAILBOX_ID,
        "provider": provider,
        "display_label": f"{provider}:{account_id}",
    }


def _persisted_row(
    *,
    provider_draft_id: str = "fake_draft_1",
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    bcc_recipients: list[str] | None = None,
    subject: str = "Hello draft",
    body: str = "body",
    attachments: list[dict] | None = None,
) -> dict:
    # Phase 2.5: list_drafts queries now bring ``attachments`` pre-aggregated
    # (json_agg subquery) so ``_draft_out_from_row`` can skip the per-row
    # follow-up fetch. Tests therefore include the field by default; legacy
    # single-draft endpoints that did NOT carry it still work via the
    # fallback branch in ``_draft_out_from_row``.
    return {
        "provider_draft_id": provider_draft_id,
        "account_id": _ACCOUNT_ID,
        "to_recipients": to_recipients if to_recipients is not None else ["to@example.com"],
        "cc_recipients": cc_recipients if cc_recipients is not None else [],
        "bcc_recipients": bcc_recipients if bcc_recipients is not None else [],
        "subject": subject,
        "body": body,
        "created_at": datetime(2024, 1, 1, 12, 0, 0),
        "updated_at": datetime(2024, 1, 1, 12, 0, 0),
        "attachments": attachments if attachments is not None else [],
    }


def _patch_common(monkeypatch, module, *, fake_client_kwargs=None):
    """Apply common monkeypatches for drafts_service tests.

    ``module`` es el submódulo que EJECUTA la función bajo test (``gestion``,
    ``envio``, ``adjuntos``): los name-rebind (``ensure_mailbox_access``,
    ``build_manager_for_accounts``, ``load_wrapped_*``) deben apuntar a él, y
    los stores compartidos se parchean por método sobre el mismo singleton.
    """
    monkeypatch.setattr(
        module, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        module.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    monkeypatch.setattr(
        module, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        module, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        module.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )

    kwargs = fake_client_kwargs or {}

    def _build(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id", ""))
            aid = str(acc.get("account_id", ""))
            label = f"{mid}__{aid}"
            manager.add_client(FakeEmailClient(
                label,
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **kwargs,
            ))
        return manager

    monkeypatch.setattr(module, "build_manager_for_accounts", _build)

    # Default draft_store.create returns a deterministic row
    monkeypatch.setattr(
        module.draft_store, "create",
        lambda row: _persisted_row(
            provider_draft_id=row.get("provider_draft_id", "fake_draft_1"),
            to_recipients=row.get("to_recipients", []),
            cc_recipients=row.get("cc_recipients", []),
            bcc_recipients=row.get("bcc_recipients", []),
            subject=row.get("subject", ""),
            body=row.get("body", ""),
        ),
    )
