# ai-limit — menu bar app (details)

Requirements, data sources, caveats, and build instructions for the macOS menu
bar app. For the overview and one-line install, see the [main README](../README.md).

## Requirements

- macOS
- Chrome or Firefox signed in to [claude.ai](https://claude.ai) — for Claude quota
- Chrome or Firefox signed in to [chatgpt.com](https://chatgpt.com) — recommended path for Codex quota
- Python 3.8+ — only needed to run from source or build the app

## How it works

ai-limit only reads your **existing local** Claude / ChatGPT browser session and
local usage records. It does not provide subscriptions and does not bypass any
quota limits — nothing leaves your machine.

- If Claude Code is available and signed in, Claude Code quota is shown.
- If ChatGPT / Codex is available and signed in, Codex quota is shown.
- A monitor that is unavailable or not signed in shows a ⚠️ warning. You can hide
  either one from the menu under **Monitors**.
- If both are unavailable, the menu bar shows `ai-limit ⚠️` or the matching error.

## Data sources

### Claude Code

| Data | Source |
|------|--------|
| Token usage details | `~/.claude/projects/**/*.jsonl` |
| Live quota | Browser cookie → `claude.ai/api/organizations/{orgId}/usage` |

Quota reading needs an active browser session on claude.ai. If the cookie is
missing or expired, it falls back gracefully with an error message and a direct
link.

### Codex

Tried in priority order:

| Priority | Data | Source | Triggers 5h window? |
|------|------|--------|------|
| 1 | Live quota | Browser cookie → `chatgpt.com/backend-api/codex/usage` | ❌ No |
| 2 | Live quota | `codex app-server` WebSocket → `account/rateLimits/read` | ⚠️ **Yes** |
| 3 | Local fallback | `~/.codex/sessions/**/*.jsonl` | ❌ No |

The browser path (1) reuses the same analytics endpoint that powers the
chatgpt.com dashboard. It returns **merged Cloud + CLI usage**, is read-only, and
does not open a new window. This is the recommended default.

> **⚠️ Side-effect warning (Codex protocol limitation):** When path 1 fails (not
> signed in to chatgpt.com / cookies expired / network issue), ai-limit falls
> back to `codex app-server`. That path sends an `initialize` call, which OpenAI
> counts as a session start — if the current 5-hour window has already expired,
> **this triggers a new 5-hour rolling window**. This is inherent to how the
> Codex CLI exposes its data; there is no workaround at the tool level.

## Notes

- **macOS only**: browser cookie reading relies on the system Keychain to decrypt Chrome cookies.
- **Unofficial API**: Claude quota comes from an internal claude.ai endpoint, not an official API — it may break with future updates.
- `<synthetic>` model entries are error placeholders written by Claude Code on API failures; they are excluded from all statistics.
- Per-model output share is only available for Claude Code; Codex does not expose a per-model breakdown.

## Build from source

py2app **must** run on **Homebrew** Python — not Anaconda. Anaconda's C
extensions depend on private dylibs that py2app won't bundle, and the resulting
app crashes at launch with missing-symbol errors.

```bash
cd src
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install -r ../requirements.txt py2app
.venv/bin/python setup.py py2app    # builds dist/ai-limit.app
bash make-dmg.sh                     # packages dist/ai-limit-<version>.dmg
```

Run the build from inside `src/` — `setup.py` references `ai-limit-app.py`
and `ai-limit.icns` by paths relative to this directory.

## License

Project code: [Apache License 2.0](../LICENSE). Bundles `browser-cookie3`, which is licensed under LGPL.
