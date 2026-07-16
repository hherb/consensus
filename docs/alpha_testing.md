# Consensus Alpha Testing Guide

Thanks for helping test Consensus! Two ways to install — pick one.

## Option 1: macOS app (easiest)

1. Download `Consensus-<version>.dmg` from the
   [releases page](https://github.com/hherb/consensus/releases).
2. Open the DMG and drag **Consensus** into **Applications**.
3. Launch Consensus from Applications or Spotlight.

The app is signed and notarized — it should open without warnings.

## Option 2: Python package (macOS, Linux, Windows)

Requires Python 3.11+. Install [uv](https://docs.astral.sh/uv/) first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then:

```bash
uv tool install consensus-app
consensus            # desktop app
consensus --web      # or: browser UI at http://127.0.0.1:8080
```

Upgrade later with `uv tool upgrade consensus-app`.

> Linux desktop mode needs GTK/WebKit libraries — see the README's
> installation section. If in doubt, use `consensus --web`.

## First-run setup

1. Open the **Providers** tab and add an API key for at least one provider
   (OpenRouter recommended — one key, many models).
2. Create entities (AI participants) in the **Profiles** tab, or use the
   defaults.
3. Start a discussion from the **New Discussion** tab.

## Where your data lives

- macOS: `~/Library/Application Support/consensus/`
- Linux: `~/.local/share/consensus/`

Delete that directory to reset the app completely.

## Known limitations (alpha)

- The Mac app cannot install extra Python packages for the
  `install_python_package` tool — use the `uv tool install` variant if a
  discussion needs additional libraries for code execution.
- First launch may take a few seconds while the database is created.

## Reporting problems

File issues at <https://github.com/hherb/consensus/issues> with:

- What you did, what you expected, what happened instead
- Your platform (macOS version / Linux distro) and install method (DMG or PyPI)
- Any error text. For the Mac app, logs appear in Console.app; for the CLI,
  copy the terminal output.
