"""
OAuth redirect callbacks for the interactive account-connect flow.

Google / Microsoft redirect the END USER's browser here after consent, so
these endpoints have no session dependency: the single-use ``state`` token
(issued by the authenticated connect-start endpoint) authenticates the
request. They render a small human-facing HTML page that reports the result
to the opener SPA window via postMessage.
"""

from __future__ import annotations

import html as html_escape_module
import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from api.services import accounts_service

router = APIRouter(prefix="/auth", tags=["oauth-callbacks"])


_CALLBACK_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>MailManager</title></head>
<body style="font-family: system-ui, sans-serif; display: flex; min-height: 90vh; align-items: center; justify-content: center;">
<div style="max-width: 28rem; text-align: center;">
<h3>{heading}</h3>
<p>{message}</p>
</div>
<script>
(function () {{
  var payload = {payload_json};
  try {{
    if (window.opener) {{
      window.opener.postMessage(payload, {target_origin_json});
    }}
  }} catch (err) {{ /* opener gone — the app falls back to polling */ }}
  if (payload.ok) {{
    setTimeout(function () {{ window.close(); }}, 1500);
  }}
}})();
</script>
</body>
</html>"""


def _render_callback_page(result: dict) -> HTMLResponse:
    """Render the result of a connect completion as the popup's final page."""
    ok = bool(result.get("ok"))
    message = str(result.get("message") or "")
    payload = {
        "source": "mailmanager-oauth",
        "ok": ok,
        "provider": result.get("provider"),
        "message": message,
    }
    html = _CALLBACK_PAGE_TEMPLATE.format(
        heading="Account connected" if ok else "Connection failed",
        message=html_escape_module.escape(message),
        payload_json=json.dumps(payload),
        target_origin_json=json.dumps(str(result.get("frontend_origin") or "*")),
    )
    return HTMLResponse(content=html)


@router.get("/google/callback", response_class=HTMLResponse)
def google_oauth_callback(
    state: str = "",
    code: str = "",
    error: str = "",
    error_description: str = "",
) -> HTMLResponse:
    """
    Complete a pending Google connect flow and report the result.
    """
    return _render_callback_page(
        accounts_service.complete_account_connect(state, code, error, error_description)
    )


@router.get("/outlook/callback", response_class=HTMLResponse)
def outlook_oauth_callback(
    state: str = "",
    code: str = "",
    error: str = "",
    error_description: str = "",
) -> HTMLResponse:
    """
    Complete a pending Outlook connect flow and report the result.
    """
    return _render_callback_page(
        accounts_service.complete_account_connect(state, code, error, error_description)
    )
