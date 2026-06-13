import datetime
import json

TZ_LOCAL = datetime.datetime.now().astimezone().tzinfo


CLAUDE_WEB_TIMEOUT_SEC = 15


def epoch_to_local(epoch: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(epoch, tz=TZ_LOCAL)


class ClaudeWebError(Exception):
    """Claude web fetch error.

    kind: "generic" | "cloudflare" | "auth" | "timeout"
    """

    def __init__(self, message, kind="generic"):
        super().__init__(message)
        self.kind = kind


def _claude_web_context(referer: str) -> tuple[str, dict]:
    try:
        import browser_cookie3
    except ImportError:
        raise ClaudeWebError(
            "browser_cookie3 not installed, run: pip install browser-cookie3"
        )

    cookies = []
    errs = []
    for name, loader in [("Chrome", browser_cookie3.chrome), ("Firefox", browser_cookie3.firefox)]:
        try:
            jar = loader(domain_name=".claude.ai")
            cookies = [(c.name, c.value) for c in jar]
            if cookies:
                break
        except Exception as e:
            errs.append(f"{name}: {e}")

    if not cookies:
        detail = f" ({'; '.join(errs)})" if errs else ""
        raise ClaudeWebError(
            f"cannot read browser cookies{detail}, please log in to claude.ai first"
        )

    cookie_dict = dict(cookies)
    org_id = cookie_dict.get("lastActiveOrg", "")
    if not org_id:
        raise ClaudeWebError(
            "could not read org ID from cookie, please open claude.ai in your browser"
        )

    cookie_header = "; ".join(f"{n}={v}" for n, v in cookies)
    headers = {
        "Cookie": cookie_header,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://claude.ai",
        "Referer": referer,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    return org_id, headers


def _claude_web_get(path: str, headers: dict, timeout: int) -> dict:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"https://claude.ai{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()[:600].decode(errors="replace")
        is_cf = bool(e.headers.get("cf-mitigated"))
        if not is_cf:
            low = raw.lower()
            is_cf = any(
                marker in low
                for marker in ("just a moment", "challenge-platform", "/cdn-cgi/")
            )
        if is_cf:
            raise ClaudeWebError(
                "claude.ai is showing a Cloudflare human-verification challenge; "
                "open claude.ai in your browser, pass it, then retry",
                kind="cloudflare",
            )
        if e.code in (401, 403):
            raise ClaudeWebError(
                "claude.ai session expired, please re-login in your browser",
                kind="auth",
            )
        raise ClaudeWebError(f"HTTP {e.code}: {raw[:300]}")
    except Exception as e:
        raise ClaudeWebError(str(e))

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ClaudeWebError(f"non-JSON response: {body[:300].decode(errors='replace')}")


def live_claude_usage(timeout: int = CLAUDE_WEB_TIMEOUT_SEC) -> dict:
    """Read Claude usage quota through browser session cookies."""

    org_id, headers = _claude_web_context("https://claude.ai/settings/usage")
    return _claude_web_get(f"/api/organizations/{org_id}/usage", headers, timeout)


def live_claude_plan(timeout: int = CLAUDE_WEB_TIMEOUT_SEC) -> str | None:
    """Read the active Claude organization plan when available."""

    org_id, headers = _claude_web_context("https://claude.ai/settings/billing")
    data = _claude_web_get(f"/api/organizations/{org_id}", headers, timeout)
    capabilities = set(data.get("capabilities") or [])
    raven_type = data.get("raven_type")
    if raven_type == "enterprise":
        return "Enterprise"
    if raven_type == "team":
        return "Team"
    if "claude_max" in capabilities:
        return "Max"
    if "claude_pro" in capabilities:
        return "Pro"
    if "raven" in capabilities:
        return "Enterprise"
    if "chat" in capabilities:
        return "Free"
    return None


class CodexWebError(Exception):
    pass


class CodexAuthError(CodexWebError):
    """Not signed in to ChatGPT or no Codex access."""


def _load_chatgpt_cookies():
    try:
        import browser_cookie3
    except ImportError:
        raise CodexWebError(
            "browser_cookie3 not installed, run: pip install browser-cookie3"
        )

    errs = []
    for name, loader in [("Chrome", browser_cookie3.chrome), ("Firefox", browser_cookie3.firefox)]:
        try:
            jar = loader(domain_name=".chatgpt.com")
            cookies = [(c.name, c.value) for c in jar]
            if cookies:
                return cookies
        except Exception as e:
            errs.append(f"{name}: {e}")

    detail = f" ({'; '.join(errs)})" if errs else ""
    raise CodexWebError(
        f"cannot read chatgpt.com cookies{detail}, please log in to chatgpt.com in your browser"
    )


_CHATGPT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _chatgpt_headers(
    cookie_header: str,
    *,
    referer: str = "https://chatgpt.com/codex/cloud/settings/analytics",
    bearer: str = None,
) -> dict:
    return {
        "Cookie": cookie_header,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": _CHATGPT_UA,
        "Referer": referer,
        "Origin": "https://chatgpt.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        **({"Authorization": f"Bearer {bearer}"} if bearer else {}),
    }


def _get_chatgpt_access_token(cookie_header: str, timeout: int) -> str:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "https://chatgpt.com/api/auth/session",
        headers=_chatgpt_headers(cookie_header),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise CodexWebError(f"session HTTP {e.code}")
    except Exception as e:
        raise CodexWebError(f"session: {e}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise CodexWebError("session: non-JSON response")

    token = data.get("accessToken")
    if not token:
        raise CodexWebError("please log in to chatgpt.com in your browser")
    return token


def _normalize_web_rate_limits(data: dict) -> dict:
    rl = data.get("rate_limit") or {}

    def win(w):
        if not w:
            return None
        wsec = w.get("limit_window_seconds")
        return {
            "used_percent": w.get("used_percent", 0),
            "window_minutes": wsec // 60 if wsec else None,
            "resets_at": w.get("reset_at"),
        }

    return {
        "limit_id": None,
        "limit_name": None,
        "primary": win(rl.get("primary_window")),
        "secondary": win(rl.get("secondary_window")),
        "credits": data.get("credits"),
        "plan_type": data.get("plan_type"),
        "rate_limit_reached_type": rl.get("rate_limit_reached_type"),
    }


def live_codex_web_usage(timeout: int = CLAUDE_WEB_TIMEOUT_SEC):
    """Read Codex usage quota from the read-only chatgpt.com web endpoint."""

    import urllib.error
    import urllib.request

    cookies = _load_chatgpt_cookies()
    cookie_header = "; ".join(f"{n}={v}" for n, v in cookies)
    token = _get_chatgpt_access_token(cookie_header, timeout)
    req = urllib.request.Request(
        "https://chatgpt.com/backend-api/codex/usage",
        headers=_chatgpt_headers(cookie_header, bearer=token),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise CodexAuthError(
                f"HTTP {e.code}: not signed in to ChatGPT or no Codex access "
                "(subscription may be required)"
            )
        raise CodexWebError(f"HTTP {e.code}")
    except Exception as e:
        raise CodexWebError(str(e))

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise CodexWebError("non-JSON response")
    return datetime.datetime.now(datetime.timezone.utc), _normalize_web_rate_limits(data)
