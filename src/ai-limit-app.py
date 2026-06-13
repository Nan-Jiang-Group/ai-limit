#!/usr/bin/env python3
"""ai-limit menu bar app (rumps version)

Standalone macOS app — no SwiftBar dependency, with its own icon and process.
py2app packaging: cd src && python3 setup.py py2app
"""
import datetime
import json
import pathlib
import threading
import webbrowser

import rumps
import AppKit

from usage import (
    __version__,
    live_claude_plan,
    live_claude_usage,
    live_codex_web_usage,
    ClaudeWebError,
    CodexWebError,
    CodexAuthError,
    TZ_LOCAL,
    epoch_to_local,
)


from i18n import (
    LANG_CODES as _LANG_CODES,
    LANG_NAMES as _LANG_NAMES,
    TRANSLATIONS as _TRANSLATIONS,
    DATE_WORDS as _DATE_WORDS,
)


def _detect_system_lang() -> str:
    """GUI apps use Cocoa preferred languages (NSLocale), not POSIX LANG/locale —
    after py2app packaging the app is launched by Launch Services, where POSIX
    locale env vars usually don't reflect the user's actual choice in
    System Settings → Language & Region."""
    aliases = {"no": "nb", "nn": "nb", "iw": "he", "in": "id"}
    try:
        for entry in AppKit.NSLocale.preferredLanguages() or ():
            code = str(entry).lower().replace("_", "-")
            if code.startswith("zh") or code.startswith("yue"):
                if any(t in code for t in ("hant", "-tw", "-hk", "-mo")):
                    return "zh-Hant"
                return "zh"
            base = code.split("-")[0]
            base = aliases.get(base, base)
            if base in _LANG_CODES:
                return base
    except Exception:
        pass
    return "en"


_SYSTEM_LANG = _detect_system_lang()

# ── Constants ────────────────────────────────────────────────────────────────

_STATE_PATH   = pathlib.Path.home() / ".ai-limit-menubar.json"
_CACHE_PATH   = pathlib.Path.home() / ".ai-limit-menubar-cache.json"
_CACHE_TTL    = 55
_REFRESH_SEC  = 60
_DISPLAY_MODES = ("5h", "7d")
_BAR_STYLES    = ("text", "battery")
_THEMES        = ("system", "xcode", "dracula", "material")
_LANGS         = _LANG_CODES + ("auto",)
_SERVICES      = ("claude", "codex")
_MENU_MIN_WIDTH = 170
_PROJECT_URL   = "https://github.com/Nan-Jiang-Group/ai-limit"
_AUTHOR_URL_EN = "https://github.com/Nan-Jiang-Group"
_LAUNCH_AGENT_LABEL = "com.nanjianggroup.ai-limit"
_LAUNCH_AGENT_PLIST = pathlib.Path.home() / "Library/LaunchAgents" / f"{_LAUNCH_AGENT_LABEL}.plist"
_APP_EXECUTABLE     = pathlib.Path("/Applications/ai-limit.app/Contents/MacOS/ai-limit")

_THEME_LABELS = {
    "system": "System",
    "xcode": "Xcode",
    "dracula": "Dracula",
    "material": "Material Design",
}

_THEME_PALETTES = {
    "xcode": {
        "claude": "#0A84FF",
        "codex": "#30D158",
        "percent": "#5E5CE6",
        "warning": "#FF453A",
        "muted": "#8E8E93",
    },
    "dracula": {
        "claude": "#BD93F9",
        "codex": "#50FA7B",
        "percent": "#8BE9FD",
        "warning": "#FF5555",
        "muted": "#F8F8F2",
    },
    "material": {
        "claude": "#2196F3",
        "codex": "#009688",
        "percent": "#FFC107",
        "warning": "#F44336",
        "muted": "#607D8B",
    },
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def _login_item_enabled():
    return _LAUNCH_AGENT_PLIST.exists()

def _set_login_item(enabled: bool):
    if enabled:
        _LAUNCH_AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
        _LAUNCH_AGENT_PLIST.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCH_AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_APP_EXECUTABLE}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
""",
            encoding="utf-8",
        )
    else:
        try:
            _LAUNCH_AGENT_PLIST.unlink()
        except FileNotFoundError:
            pass

def _t(lang, text):
    """Translate an English UI string via i18n.TRANSLATIONS (English fallback)."""
    if lang == "en":
        return text
    return _TRANSLATIONS.get(text, {}).get(lang, text)

def _paren(base, detail, lang):
    """Compose "base（detail）" with fullwidth parens for Chinese, "base (detail)" otherwise."""
    return f"{base}（{detail}）" if lang.startswith("zh") else f"{base} ({detail})"

def _theme_label(theme, lang):
    label = _THEME_LABELS.get(theme, theme)
    return _t(lang, label) if theme == "system" else label

def _native_bar(pct, width=4):
    filled = round(max(0, min(100, pct)) / 100 * width)
    return "▰" * filled + "▱" * (width - filled)

def _bar_text_title(items):
    parts = [
        f"{label} ⚠️" if err else f"{label} {pct}%"
        for label, pct, err in items
    ]
    return " | ".join(parts) if parts else "ai-limit ⚠️"

def _ns_color(hex_color):
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return AppKit.NSColor.labelColor()
    red = int(value[0:2], 16) / 255
    green = int(value[2:4], 16) / 255
    blue = int(value[4:6], 16) / 255
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1)

def _fmt_plan(plan, lang="en"):
    if not plan or plan == "?":
        return ""
    plan = str(plan).replace("_", " ").title()
    if lang.startswith("zh"):
        return f" 方案：{plan}"
    return f" {_t(lang, 'Plan')}: {plan}"

def _fmt_reset_dt(dt, lang, show_weekday=True):
    if not show_weekday:
        return f"{dt:%-I:%M %p}"
    today = datetime.datetime.now(TZ_LOCAL).date()
    days = (dt.date() - today).days
    words = _DATE_WORDS.get(lang, _DATE_WORDS["en"])
    if days == 0:    wd = words["today"]
    else:            wd = words["weekdays"][dt.weekday()]
    return f"{dt:%-I:%M %p}  {wd}"

def _fmt_reset_epoch(epoch, lang="en", show_weekday=True):
    try:
        return _fmt_reset_dt(epoch_to_local(int(epoch)), lang, show_weekday)
    except Exception:
        return "?"

def _fmt_reset_iso(iso, lang="en", show_weekday=True):
    try:
        return _fmt_reset_dt(
            datetime.datetime.fromisoformat(iso).astimezone(TZ_LOCAL),
            lang,
            show_weekday,
        )
    except Exception:
        return "?"

# ── State / cache ────────────────────────────────────────────────────────────

def _load_state():
    # lang: "auto" (default) = follow system, resolved via NSLocale at each launch;
    # any LANG_CODES entry = explicitly chosen in the menu, permanently overrides
    # the system language.
    state = {
        "global": "5h",
        "bar_style": "text",
        "theme": "system",
        "lang": "auto",
        "services": list(_SERVICES),
    }
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if raw.get("global") in _DISPLAY_MODES:
                state["global"] = raw["global"]
            if raw.get("bar_style") in _BAR_STYLES:
                state["bar_style"] = raw["bar_style"]
            if raw.get("theme") in _THEMES:
                state["theme"] = raw["theme"]
            if raw.get("lang") in _LANGS:
                state["lang"] = raw["lang"]
            if isinstance(raw.get("services"), list):
                svc = [s for s in raw["services"] if s in _SERVICES]
                if svc:
                    state["services"] = svc
    except Exception:
        pass
    return state

def _save_state(state):
    try:
        _STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass

def _load_cache():
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        age = datetime.datetime.now().timestamp() - float(raw.get("cached_at", 0))
        if age <= _CACHE_TTL:
            return raw.get("claude"), raw.get("codex")
    except Exception:
        pass
    return None, None

def _save_cache(claude, codex):
    try:
        _CACHE_PATH.write_text(
            json.dumps({
                "cached_at": datetime.datetime.now().timestamp(),
                "claude": claude,
                "codex": codex,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

# ── Data fetching ────────────────────────────────────────────────────────────

def _fetch_claude(lang):
    import socket, urllib.error
    try:
        data = live_claude_usage()
        five_h = data.get("five_hour") or {}
        seven_d = data.get("seven_day") or {}
        try:
            plan = live_claude_plan()
        except Exception:
            plan = None
        return {
            "5h_left":  int(round(100 - float(five_h.get("utilization", 0)))),
            "7d_left":  int(round(100 - float(seven_d.get("utilization", 0)))),
            "5h_reset": five_h.get("resets_at"),
            "7d_reset": seven_d.get("resets_at"),
            "plan":     plan,
        }
    except ClaudeWebError as e:
        kind = getattr(e, "kind", "generic")
        if kind == "cloudflare":
            msg = _t(lang, "Pass claude.ai human-check in browser")
        elif kind == "auth":
            msg = _t(lang, "Re-login at claude.ai in browser")
        else:
            msg = str(e)
            if "JSON" in msg or "DOCTYPE" in msg or "html" in msg.lower():
                msg = _t(lang, "Network error or re-login at claude.ai required")
        return {"error": msg}
    except (socket.timeout, TimeoutError):
        return {"error": _t(lang, "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _t(lang, "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

def _fetch_codex(lang):
    import socket, urllib.error
    try:
        _ts, rl = live_codex_web_usage()
        primary   = rl.get("primary") or {}
        secondary = rl.get("secondary") or {}
        return {
            "5h_left":  int(round(100 - primary.get("used_percent", 0))),
            "7d_left":  int(round(100 - secondary.get("used_percent", 0))),
            "5h_reset": primary.get("resets_at"),
            "7d_reset": secondary.get("resets_at"),
            "plan":     rl.get("plan_type") or "?",
        }
    except CodexAuthError:
        return {"error": _t(lang, "No Codex access (subscription required or re-login needed)")}
    except CodexWebError as e:
        msg = str(e)
        if "timed out" in msg or "urlopen" in msg:
            msg = _t(lang, "Network timeout, please retry later")
        return {"error": msg}
    except (socket.timeout, TimeoutError):
        return {"error": _t(lang, "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _t(lang, "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

# ── AppKit helpers ───────────────────────────────────────────────────────────

def _status_button(app):
    """Return NSStatusItem.button(); rumps stores it under different attributes across versions."""
    # rumps 0.4 keeps it at _nsapp.nsstatusitem, but it varies between versions; probe once
    candidates = ("_status_item", "_status_bar_item", "_nsstatusitem")
    for attr in candidates:
        item = getattr(app, attr, None)
        if item and hasattr(item, "button"):
            return item.button()
    # rumps 0.4.x path: app._nsapp.nsstatusitem
    nsapp = getattr(app, "_nsapp", None)
    if nsapp is not None:
        item = getattr(nsapp, "nsstatusitem", None)
        if item and hasattr(item, "button"):
            return item.button()
    # Fallback: scan all app attributes for something whose .button() looks right
    for name in dir(app):
        if name.startswith("__"):
            continue
        try:
            item = getattr(app, name)
        except Exception:
            continue
        if item is not None and hasattr(item, "button") and callable(getattr(item, "button", None)):
            try:
                btn = item.button()
                if hasattr(btn, "setTitle_") and hasattr(btn, "setImage_"):
                    return btn
            except Exception:
                continue
    return None


def _set_bar_title(app, text):
    """Plain-text title (fallback when SF Symbols are unavailable)."""
    btn = _status_button(app)
    if btn is not None:
        btn.setImage_(None)
        btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_(""))
        btn.setTitle_(text)
        btn.setImagePosition_(0)  # NSNoImage
        return
    app.title = text


def _set_bar_attributed_title(app, attributed):
    btn = _status_button(app)
    if btn is not None:
        btn.setImage_(None)
        btn.setTitle_("")
        btn.setAttributedTitle_(attributed)
        btn.setImagePosition_(0)  # NSNoImage
        return
    app.title = str(attributed.string())


def _bar_attrs(theme, role, font):
    attrs = {AppKit.NSFontAttributeName: font}
    palette = _THEME_PALETTES.get(theme)
    if palette:
        attrs[AppKit.NSForegroundColorAttributeName] = _ns_color(
            palette.get(role) or palette.get("muted") or "#000000"
        )
    return attrs


def _render_themed_text_title(items, theme):
    font = AppKit.NSFont.menuBarFontOfSize_(0)
    mas = AppKit.NSMutableAttributedString.alloc().init()

    def append_text(s, role="muted"):
        mas.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(
                s, _bar_attrs(theme, role, font)
            )
        )

    for i, (label, pct, err) in enumerate(items):
        if i > 0:
            append_text(" | ", "muted")
        role = "claude" if label == "Claude" else "codex"
        append_text(label, role)
        if err:
            append_text(" ⚠️", "warning")
        else:
            append_text(f" {pct}%", "percent")

    if mas.length() == 0:
        append_text("ai-limit ", "muted")
        append_text("⚠️", "warning")
    return mas


def _sf_battery_image(pct, point_size=14):
    """Return the SF Symbol battery NSImage for a percentage (quantized to 5 levels).

    Granularity: 0(<13) / 25 / 50 / 75 / 100(≥88).
    No coloring here — the image goes into the composite as a template, and
    AppKit decides the actual color in the status bar context alongside the
    system Wi-Fi and battery icons (vibrancy / light-dark adaptation).
    """
    if pct >= 88:
        name = "battery.100"
    elif pct >= 63:
        name = "battery.75"
    elif pct >= 38:
        name = "battery.50"
    elif pct >= 13:
        name = "battery.25"
    else:
        name = "battery.0"
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        point_size, AppKit.NSFontWeightMedium
    )
    return img.imageWithSymbolConfiguration_(cfg)


def _battery_attachment(pct, font):
    """Wrap the SF Symbol battery in an NSTextAttachment so it can flow inline
    with text inside an NSAttributedString.

    With the image set as template, the menu bar treats it as a system icon
    (vibrancy + light-dark adaptation), rendered in the same pipeline as the
    Wi-Fi / system battery icons.
    """
    bat = _sf_battery_image(pct)
    if bat is None:
        return None
    bat.setTemplate_(True)
    attach = AppKit.NSTextAttachment.alloc().init()
    attach.setImage_(bat)
    sz = bat.size()
    # Vertical tweak: roughly align the battery's centerline with the text's
    y_offset = (font.capHeight() - sz.height) / 2
    attach.setBounds_(AppKit.NSMakeRect(0, y_offset, sz.width, sz.height))
    return AppKit.NSAttributedString.attributedStringWithAttachment_(attach)


def _render_battery_title(items, theme="system"):
    """Build the status bar attributed title: text is rendered natively by
    NSStatusBarButton (getting system vibrancy and light-dark adaptation),
    with batteries as inline template image attachments.

    The old approach drew the whole thing as a bitmap (NSImage.lockFocus +
    labelColor), but bitmap text is rasterized grayscale, missing the status
    bar text vibrancy — visually darker than the system clock and menu text.
    """
    font = AppKit.NSFont.menuBarFontOfSize_(0)
    mas = AppKit.NSMutableAttributedString.alloc().init()

    def append_text(s, role="muted"):
        mas.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(
                s, _bar_attrs(theme, role, font)
            )
        )

    for i, (label, pct, err) in enumerate(items):
        prefix = "  " if i > 0 else ""
        role = "claude" if label == "Claude" else "codex"
        if err:
            append_text(f"{prefix}{label}", role)
            append_text(" ⚠️", "warning")
            continue
        append_text(prefix, "muted")
        append_text(label, role)
        append_text(" ", "muted")
        bat_attach = _battery_attachment(pct, font)
        if bat_attach is not None:
            mas.appendAttributedString_(bat_attach)

    if mas.length() == 0:
        append_text("ai-limit ", "muted")
        append_text("⚠️", "warning")
    return mas


def _set_bar_with_battery_icons(app, items, theme="system"):
    """Install the attributed title (text + battery attachments) on the status bar button."""
    btn = _status_button(app)
    if btn is None:
        raise RuntimeError("no status button")
    btn.setImage_(None)
    btn.setTitle_("")
    btn.setAttributedTitle_(_render_battery_title(items, theme))

def _noop(_):
    """Side-effect-free callback, used only so macOS renders action-less menu
    items in the regular text color. AppKit auto-grays items with
    NSMenuItem.target=nil — setEnabled_(True) doesn't help; attaching a real
    callback (even a no-op) is what makes macOS treat the item as normal."""
    pass


def _disable(menu_item):
    """Make a menu item explicitly gray (only for deliberately secondary info like "last refresh")."""
    menu_item._menuitem.setEnabled_(False)
    return menu_item


def _inert(menu_item):
    """Attach a no-op callback so macOS renders the item in regular text color (not gray); clicking does nothing."""
    menu_item.set_callback(_noop)
    return menu_item

def _set_right_detail_title(menu_item, left, right):
    """Render a menu item as left text plus a right-aligned detail string."""
    title = f"{left}\t{right}"
    menu_item.title = title
    try:
        tab_x = _MENU_MIN_WIDTH - 18
        paragraph = AppKit.NSMutableParagraphStyle.alloc().init()
        paragraph.setTabStops_([
            AppKit.NSTextTab.alloc().initWithType_location_(
                AppKit.NSRightTabStopType, tab_x
            )
        ])
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.menuFontOfSize_(0),
            AppKit.NSParagraphStyleAttributeName: paragraph,
        }
        right_attrs = dict(attrs)
        right_attrs[AppKit.NSForegroundColorAttributeName] = AppKit.NSColor.secondaryLabelColor()
        attributed = AppKit.NSMutableAttributedString.alloc().init()
        attributed.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(left, attrs)
        )
        attributed.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_("\t", attrs)
        )
        attributed.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(right, right_attrs)
        )
        menu_item._menuitem.setAttributedTitle_(attributed)
    except Exception:
        pass

def _detail_text(mode, pct, reset, lang):
    if lang == "en":
        return f"  {mode}\t{pct:>3}% left  |  resets {reset}"
    return f"  {mode}\t{pct:>3}% {_t(lang, 'left')}  |  {_t(lang, 'resets')} {reset}"

# ── Main app ─────────────────────────────────────────────────────────────────

class AiLimitApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)
        self._state = _load_state()
        self._claude = None
        self._codex  = None
        # Background threads drop fetch results here; the main-thread _apply_pending timer picks them up
        self._pending = None
        self._pending_lock = threading.Lock()
        self._build_menu()

    def _lang(self):
        """Currently effective language: an explicit menu choice (persisted) wins;
        "Follow System" (or an old state file without the field) is resolved via
        NSLocale at each launch — the detected value is never written back to
        state, so saving other preferences can't fossilize it into a fake
        user choice."""
        choice = self._state["lang"]
        return choice if choice in _LANG_CODES else _SYSTEM_LANG

    # ── Menu construction ────────────────────────────────────────────────────

    def _build_menu(self):
        lang = self._lang()

        # Claude section. The plan header is a disabled (gray) row; detail rows
        # get no-op callbacks so they keep normal text color.
        self._claude_header = _disable(rumps.MenuItem("Claude Code"))
        self._claude_5h     = _inert(rumps.MenuItem("  5h  …"))
        self._claude_7d     = _inert(rumps.MenuItem("  7d  …"))

        # CodeX section. The plan header is a disabled (gray) row.
        self._codex_header = _disable(rumps.MenuItem("CodeX"))
        self._codex_5h     = _inert(rumps.MenuItem("  5h  …"))
        self._codex_7d     = _inert(rumps.MenuItem("  7d  …"))

        # Combined row: "Refresh now" on the left, last-refresh time on the
        # right. The whole row is clickable and triggers a refresh.
        self._refresh_item = rumps.MenuItem("…", callback=self._force_refresh)

        # "Menu bar display" submenu
        self._mode_5h = rumps.MenuItem(_t(lang, "5 hours"),
                                       callback=self._set_mode_5h)
        self._mode_7d = rumps.MenuItem(_t(lang, "7 days"),
                                       callback=self._set_mode_7d)
        self._mode_menu = rumps.MenuItem(_t(lang, "Menu bar display"))
        self._mode_menu.add(self._mode_5h)
        self._mode_menu.add(self._mode_7d)

        # Menu bar style submenu
        self._style_text = rumps.MenuItem(_t(lang, "Text percentage"),
                                          callback=lambda _: self._set_bar_style("text"))
        self._style_battery = rumps.MenuItem(_t(lang, "Battery icon"),
                                             callback=lambda _: self._set_bar_style("battery"))
        self._style_menu = rumps.MenuItem(_t(lang, "Menu bar style"))
        self._style_menu.add(self._style_text)
        self._style_menu.add(self._style_battery)

        # Theme submenu
        self._theme_menu = rumps.MenuItem(_t(lang, "Theme"))
        self._theme_items = {}
        for theme in _THEMES:
            item = rumps.MenuItem(
                _theme_label(theme, lang),
                callback=lambda _, t=theme: self._set_theme(t),
            )
            self._theme_items[theme] = item
            self._theme_menu.add(item)

        # Language submenu — "Follow System" plus one entry per supported language
        self._lang_auto = rumps.MenuItem(_t(lang, "Follow System"),
                                         callback=lambda _: self._set_lang("auto"))
        self._lang_menu = rumps.MenuItem(_t(lang, "Language"))
        self._lang_menu.add(self._lang_auto)
        self._lang_items = {}
        for code in _LANG_CODES:
            item = rumps.MenuItem(_LANG_NAMES[code],
                                  callback=lambda _, c=code: self._set_lang(c))
            self._lang_items[code] = item
            self._lang_menu.add(item)

        # Monitors submenu
        self._svc_claude = rumps.MenuItem("Claude Code", callback=self._toggle_claude)
        self._svc_codex  = rumps.MenuItem("CodeX",       callback=self._toggle_codex)
        self._svc_menu = rumps.MenuItem(_t(lang, "Monitors"))
        self._svc_menu.add(self._svc_claude)
        self._svc_menu.add(self._svc_codex)

        # Launch at login
        self._login_item = rumps.MenuItem(
            _t(lang, "Launch at Login"),
            callback=self._toggle_login_item,
        )
        self._update_login_item_check()

        # Settings submenu
        self._settings_menu = rumps.MenuItem(_t(lang, "Settings..."))
        self._settings_menu.add(self._mode_menu)
        self._settings_menu.add(self._style_menu)
        self._settings_menu.add(self._theme_menu)
        self._settings_menu.add(self._lang_menu)
        self._settings_menu.add(self._svc_menu)
        self._settings_menu.add(self._login_item)

        # About submenu
        self._about_menu   = rumps.MenuItem(_t(lang, "About"))
        self._about_ver    = rumps.MenuItem(f"ai-limit {__version__}",
                                            callback=lambda _: webbrowser.open(_PROJECT_URL))
        self._about_author = rumps.MenuItem(
            _t(lang, "Maintainer: Nan-Jiang Group"),
            callback=lambda _: webbrowser.open(_AUTHOR_URL_EN),
        )
        self._about_menu.add(self._about_ver)
        self._about_menu.add(self._about_author)

        # Star on GitHub (lives in the About submenu; added after _about_menu exists)
        self._star_item = rumps.MenuItem(
            _t(lang, "⭐ Star on GitHub"),
            callback=lambda _: webbrowser.open(_PROJECT_URL),
        )
        self._about_menu.add(self._star_item)

        # Quit
        self._quit_item = rumps.MenuItem(
            _t(lang, "Quit"),
            callback=rumps.quit_application,
        )

        self.menu = [
            self._claude_header,
            self._claude_5h,
            self._claude_7d,
            None,
            self._codex_header,
            self._codex_5h,
            self._codex_7d,
            None,
            self._refresh_item,
            None,
            self._settings_menu,
            self._about_menu,
            None,
            self._quit_item,
        ]
        # NSMenu otherwise shrinks to the longest label, causing visible width jumps.
        self.menu._menu.setMinimumWidth_(_MENU_MIN_WIDTH)
        self._update_mode_checks()
        self._update_style_checks()
        self._update_theme_checks()
        self._update_lang_checks()
        self._update_service_checks()

    # ── Data updates ─────────────────────────────────────────────────────────
    #
    # Principle: network fetches always run on background threads and must never
    # block the main UI thread, otherwise macOS shows a spinning cursor when
    # opening the menu.
    # Flow:
    #   main thread trigger → immediately redraw from _load_cache() (instant response)
    #                       → start background thread _async_refresh()
    #   background thread   → call _fetch_claude / _fetch_codex (takes seconds)
    #                       → put results into self._pending (with lock)
    #   main-thread timer   → _apply_pending checks _pending every 0.4s; applies + redraws

    @rumps.timer(0.3)
    def _init_render(self, sender):
        """Right after launch: redraw from cache + fetch fresh data once in the background."""
        self._refresh_from_cache()
        self._kick_background_fetch()
        sender.stop()

    @rumps.timer(_REFRESH_SEC)
    def _auto_refresh(self, _):
        """Background fetch every 60s."""
        self._kick_background_fetch()

    @rumps.timer(0.4)
    def _apply_pending(self, _):
        """Main-thread handoff point: apply data fetched by the background thread to the UI.

        Key point: when a service is disabled, don't wipe the old in-memory data.
        The background thread returns None for a disabled service, meaning
        "didn't fetch", not "clear" — keep the last value, so re-enabling the
        service instantly shows its most recent cache in the menu bar instead
        of a 1–2s network-fetch wait.
        """
        with self._pending_lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            return
        claude, codex = pending
        if claude is not None:
            self._claude = claude
        if codex is not None:
            self._codex = codex
        _save_cache(self._claude, self._codex)
        self._render()

    def _refresh_from_cache(self):
        """Instant main-thread operation: redraw from the short-lived cache, no network."""
        claude, codex = _load_cache()
        # No filtering by services — keep both datasets in memory; _render controls what the UI shows
        if claude is not None:
            self._claude = claude
        if codex is not None:
            self._codex = codex
        self._render()

    def _kick_background_fetch(self):
        """Start a background thread to fetch data; never touch UI objects inside the thread."""
        t = threading.Thread(target=self._async_refresh, daemon=True)
        t.start()

    def _async_refresh(self):
        """Background thread: fetch data → write the shared variable. Must not call any rumps/AppKit UI."""
        lang = self._lang()
        services = self._state.get("services") or list(_SERVICES)
        claude = _fetch_claude(lang) if "claude" in services else None
        codex  = _fetch_codex(lang)  if "codex"  in services else None
        with self._pending_lock:
            self._pending = (claude, codex)

    def _render(self):
        lang     = self._lang()
        mode     = self._state["global"]
        theme    = self._state.get("theme", "system")
        services = self._state.get("services") or list(_SERVICES)
        show_claude = "claude" in services
        show_codex  = "codex"  in services
        claude = self._claude or {}
        codex  = self._codex  or {}

        # Menu bar title: [Claude 68% ⌬]  [CodeX 99% ⌬]
        # The battery is a native SF Symbol — Apple's own iPhone-style glyph, vector so it never blurs
        bar_items = []
        if show_claude:
            if "error" in claude:
                bar_items.append(("Claude", 0, True))
            elif claude:
                pct = claude["5h_left"] if mode == "5h" else claude["7d_left"]
                bar_items.append(("Claude", pct, False))
        if show_codex:
            if "error" in codex:
                bar_items.append(("CodeX", 0, True))
            elif codex:
                pct = codex["5h_left"] if mode == "5h" else codex["7d_left"]
                bar_items.append(("CodeX", pct, False))
        if self._state.get("bar_style") == "battery":
            try:
                _set_bar_with_battery_icons(self, bar_items, theme)
            except Exception:
                # Fall back to the ▰▱ text version when SF Symbols are unavailable (very old macOS)
                parts = [
                    f"{lbl} ⚠️" if err else f"{lbl} {pct}% {_native_bar(pct)}"
                    for lbl, pct, err in bar_items
                ]
                _set_bar_title(self, " | ".join(parts) if parts else "ai-limit ⚠️")
        elif theme != "system":
            _set_bar_attributed_title(self, _render_themed_text_title(bar_items, theme))
        else:
            _set_bar_title(self, _bar_text_title(bar_items))

        # Claude section — hidden entirely when the service is disabled
        self._claude_header._menuitem.setHidden_(not show_claude)
        self._claude_5h._menuitem.setHidden_(not show_claude)
        self._claude_7d._menuitem.setHidden_(not show_claude)
        if show_claude:
            if "error" in claude:
                self._claude_header.title = "Claude Code ⚠️"
                self._claude_5h.title = f"  {claude['error'][:60]}"
                self._claude_7d._menuitem.setHidden_(True)
            elif claude:
                plan = _fmt_plan(claude.get("plan"), lang)
                self._claude_header.title = f"Claude Code{plan}"
                c5_reset = _fmt_reset_iso(claude["5h_reset"], lang, show_weekday=False)
                c7_reset = _fmt_reset_iso(claude["7d_reset"], lang)
                self._claude_5h.title = _detail_text("5h", claude["5h_left"], c5_reset, lang)
                self._claude_7d.title = _detail_text("7d", claude["7d_left"], c7_reset, lang)

        # CodeX section
        self._codex_header._menuitem.setHidden_(not show_codex)
        self._codex_5h._menuitem.setHidden_(not show_codex)
        self._codex_7d._menuitem.setHidden_(not show_codex)
        if show_codex:
            if "error" in codex:
                self._codex_header.title = "CodeX ⚠️"
                self._codex_5h.title = f"  {codex['error'][:60]}"
                self._codex_7d._menuitem.setHidden_(True)
            elif codex:
                plan = _fmt_plan(codex.get("plan"), lang)
                self._codex_header.title = f"CodeX{plan}"
                x5_reset = _fmt_reset_epoch(codex["5h_reset"], lang, show_weekday=False)
                x7_reset = _fmt_reset_epoch(codex["7d_reset"], lang)
                self._codex_5h.title = _detail_text("5h", codex["5h_left"], x5_reset, lang)
                self._codex_7d.title = _detail_text("7d", codex["7d_left"], x7_reset, lang)

        # Combined row: action label left, last-refresh time right.
        now = datetime.datetime.now(TZ_LOCAL).strftime("%-I:%M %p")
        _set_right_detail_title(
            self._refresh_item,
            f" ↻ {_t(lang, 'Refresh now')} ",
            f"  {_t(lang, 'Last refresh')} {now}",
        )

    # ── Mode / language switching ────────────────────────────────────────────

    def _set_mode_5h(self, _):
        self._state["global"] = "5h"
        _save_state(self._state)
        self._update_mode_checks()
        self._render()  # Only the display window changed, data is the same — just redraw

    def _set_mode_7d(self, _):
        self._state["global"] = "7d"
        _save_state(self._state)
        self._update_mode_checks()
        self._render()

    def _update_mode_checks(self):
        lang = self._lang()
        mode = self._state["global"]
        label_5h = _t(lang, "5 hours")
        label_7d = _t(lang, "7 days")
        self._mode_5h.title = ("✓ " if mode == "5h" else "  ") + label_5h
        self._mode_7d.title = ("✓ " if mode == "7d" else "  ") + label_7d
        self._mode_menu.title = _paren(
            _t(lang, "Menu bar display"),
            label_5h if mode == "5h" else label_7d,
            lang,
        )

    def _set_bar_style(self, style):
        self._state["bar_style"] = style
        _save_state(self._state)
        self._update_style_checks()
        self._render()  # Same data, different bar rendering — just redraw

    def _update_style_checks(self):
        lang = self._lang()
        style = self._state["bar_style"]
        label_text    = _t(lang, "Text percentage")
        label_battery = _t(lang, "Battery icon")
        self._style_text.title    = ("✓ " if style == "text"    else "  ") + label_text
        self._style_battery.title = ("✓ " if style == "battery" else "  ") + label_battery
        self._style_menu.title = _paren(
            _t(lang, "Menu bar style"),
            label_text if style == "text" else label_battery,
            lang,
        )

    def _set_theme(self, theme):
        self._state["theme"] = theme
        _save_state(self._state)
        self._update_theme_checks()
        self._render()

    def _update_theme_checks(self):
        lang = self._lang()
        theme = self._state.get("theme", "system")
        for key, item in self._theme_items.items():
            item.title = ("✓ " if theme == key else "  ") + _theme_label(key, lang)
        self._theme_menu.title = _paren(
            _t(lang, "Theme"),
            _theme_label(theme, lang),
            lang,
        )

    def _set_lang(self, code):
        """code: "auto" or any entry of _LANG_CODES."""
        self._state["lang"] = code
        _save_state(self._state)
        self._update_lang_checks()
        # Redraw all i18n text (detail rows / section headers / "last refresh" etc.)
        self._update_mode_checks()
        self._update_style_checks()
        self._update_theme_checks()
        self._update_service_checks()
        self._refresh_static_labels()
        self._render()

    def _refresh_static_labels(self):
        """After a language switch, update all menu text that doesn't depend on data."""
        lang = self._lang()
        self._settings_menu.title = _t(lang, "Settings...")
        self._about_menu.title  = _t(lang, "About")
        self._about_author.title = _t(lang, "Maintainer: Nan-Jiang Group")
        self._update_login_item_check()
        self._star_item.title    = _t(lang, "⭐ Star on GitHub — support the author")
        self._quit_item.title    = _t(lang, "Quit")

    def _update_lang_checks(self):
        choice = self._state["lang"]
        lang = self._lang()
        follow = _t(lang, "Follow System")
        self._lang_auto.title = ("✓ " if choice == "auto" else "  ") + follow
        for code, item in self._lang_items.items():
            item.title = ("✓ " if choice == code else "  ") + _LANG_NAMES[code]
        self._lang_menu.title = _paren(
            _t(lang, "Language"), _LANG_NAMES.get(choice, follow), lang
        )

    # ── Service toggling ─────────────────────────────────────────────────────

    def _toggle_claude(self, _):
        self._toggle_service("claude")

    def _toggle_codex(self, _):
        self._toggle_service("codex")

    def _toggle_service(self, service):
        svc = list(self._state.get("services") or list(_SERVICES))
        if service in svc:
            svc.remove(service)
        else:
            svc.append(service)
        if not svc:
            # Both services can't be off at once; fall back to keeping the one just toggled off
            svc = [service]
        self._state["services"] = svc
        _save_state(self._state)
        self._update_service_checks()
        # Redraw immediately with existing data (hide/show the section), no UI stall;
        # a newly enabled service uses its ≤55s cache if present, otherwise waits for the fetch below
        self._render()
        # Async background refresh (if the newly enabled service has no cache, it appears a few seconds later)
        self._kick_background_fetch()

    def _toggle_login_item(self, _):
        _set_login_item(not _login_item_enabled())
        self._update_login_item_check()

    def _update_login_item_check(self):
        lang = self._lang()
        enabled = _login_item_enabled()
        suffix = " ✓" if enabled else ""
        self._login_item.title = _t(lang, "Launch at Login") + suffix

    def _update_service_checks(self):
        lang = self._lang()
        svc = self._state.get("services") or list(_SERVICES)
        self._svc_claude.title = ("✓ " if "claude" in svc else "  ") + "Claude Code"
        self._svc_codex.title  = ("✓ " if "codex"  in svc else "  ") + "CodeX"
        summary = _t(lang, "Both") if len(svc) == 2 else (
            "Claude Code" if "claude" in svc else "CodeX"
        )
        self._svc_menu.title = _paren(_t(lang, "Monitors"), summary, lang)

    # ── Force refresh ────────────────────────────────────────────────────────

    def _force_refresh(self, _):
        try:
            _CACHE_PATH.unlink()
        except Exception:
            pass
        # Background fetch, no UI stall; new data lands in the menu within seconds via _apply_pending
        self._kick_background_fetch()


if __name__ == "__main__":
    AiLimitApp().run()
