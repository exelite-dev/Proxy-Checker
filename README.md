# Telegram Proxy Checker

High-performance Telegram proxy checker with a modern desktop GUI.

[🇮🇷 نسخه فارسی](README_FA.md)

The app can:
- Paste and test proxies manually
- Load proxies from `proxies.txt`
- Fetch fresh proxies from GitHub raw source URLs
- Auto-fetch sources every N hours
- Scan proxies concurrently and save working ones with latency

## Main Features

- Desktop GUI with purple theme (`customtkinter`)
- Supports `MTProto`, `HTTP`, and `SOCKS5`
- Async worker-pool scanning (memory-friendlier for large proxy lists)
- Controlled connector limits (`TCPConnector(limit ~= concurrency * 2)`)
- Timeout handling (default: 5 seconds)
- Strict Mode for deeper validation (slower, more accurate)
- Real-time progress and live result table
- Output file with working proxies + ping: `working.txt`

## Requirements

- Python 3.10+
- Windows / Linux / macOS

Install:

```bash
pip install -r requirements.txt
```

## Quick Start

### Windows launcher (GUI)

Double-click `run.bat` or run:

```powershell
.\run.bat
```

This opens the purple UI (`proxy_gui.py`).

### CLI mode (fallback / optional)

```bash
python telegram_proxy_checker.py -i proxies.txt -o working.txt -t 5 -c 300
```

Strict CLI mode:

```bash
python telegram_proxy_checker.py -i proxies.txt -o working.txt -t 5 -c 300 --strict
```

## Input Formats

You can paste any of these formats in the GUI input box:

```text
tg://proxy?server=1.2.3.4&port=443&secret=dd...
https://t.me/proxy?server=1.2.3.4&port=443&secret=dd...
1.2.3.4:443:mtproto:dd...
1.2.3.4:1080:socks5
1.2.3.4:8080:http
```

See `proxies.sample.txt` for examples.

## Output

Working proxies are saved to `working.txt` in this format:

```text
tg://proxy?server=1.2.3.4&port=443&secret=dd... | 275.4 ms
```

## Accuracy Notes

- No proxy scanner can guarantee 100% validity at all times.
- Proxy status can change in seconds due to bans, overload, routing changes, or Telegram filtering.
- Use `Strict Mode` for better accuracy (slower scan).
- For production use, re-check working proxies periodically and use rotation/fallback logic.
- MTProto verification is probe-based (not a full Telegram client handshake), so edge-case false positives can still happen.

## Publish To GitHub

Recommended before first push:

1. Keep your real proxy list private (`proxies.txt` is already ignored).
2. Keep only sample input (`proxies.sample.txt`) in the repository.

Typical commands:

```bash
git init
git add .
git commit -m "Initial release: Telegram Proxy Checker GUI + strict mode"
git branch -M main
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```

## Project Structure

- `proxy_gui.py`: main GUI app
- `telegram_proxy_checker.py`: CLI scanner engine
- `run.bat`: Windows launcher (GUI first, CLI fallback)
- `requirements.txt`: dependencies
- `proxies.sample.txt`: sample input list
- `README_FA.md`: Persian documentation
- `legacy/` is not part of the current ZIP release

## Security Note

`proxies.txt` is ignored by `.gitignore` by default because it may contain sensitive/private proxy data.
