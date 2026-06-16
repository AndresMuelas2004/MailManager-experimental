from __future__ import annotations

from api.routers import oauth_callback_routers as cb


class TestJsonForHtmlScript:
    """The inline-<script> JSON serialiser must neutralise script breakout."""

    def test_escapes_script_breakout_characters(self):
        out = cb._json_for_html_script({"message": "</script><script>alert(1)</script>"})
        assert "</script>" not in out
        assert "<" not in out
        assert ">" not in out
        assert "\\u003c/script\\u003e" in out

    def test_escapes_ampersand(self):
        out = cb._json_for_html_script("a & b")
        assert "&" not in out
        assert "\\u0026" in out


class TestRenderCallbackPage:
    """The OAuth callback page must not let a reflected message inject markup."""

    def test_malicious_message_cannot_break_out_of_script(self):
        response = cb._render_callback_page(
            {
                "ok": False,
                "provider": "gmail",
                "message": "</script><script>alert(document.cookie)</script>",
                "frontend_origin": "http://localhost:5173",
            }
        )
        body = response.body.decode("utf-8")
        # The raw closing-script payload must not survive anywhere in the document.
        assert "</script><script>alert" not in body
        assert "\\u003c/script\\u003e" in body

    def test_sets_content_security_policy_header(self):
        response = cb._render_callback_page(
            {
                "ok": True,
                "provider": "gmail",
                "message": "ok",
                "frontend_origin": "http://localhost:5173",
            }
        )
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "script-src 'unsafe-inline'" in csp
