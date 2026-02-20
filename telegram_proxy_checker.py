#!/usr/bin/env python3
"""
High-performance Telegram proxy checker.

Features:
- Reads proxies from proxies.txt
- Supports HTTP, SOCKS5, and MTProto formats
- Async concurrent scanning with timeout handling
- Real-time progress bar (rich)
- Saves only working proxies with latency to working.txt
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import aiohttp
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table


DEFAULT_INPUT = "proxies.txt"
DEFAULT_OUTPUT = "working.txt"
DEFAULT_TIMEOUT = 5.0
DEFAULT_CONCURRENCY = 300
DEFAULT_STRICT = False

TELEGRAM_TEST_HOST = "www.telegram.org"
TELEGRAM_DC_IP = "149.154.167.50"
TELEGRAM_PORT = 443
TELEGRAM_API_URL = "https://api.telegram.org/"
TELEGRAM_WEB_URL = f"https://{TELEGRAM_TEST_HOST}/"
TELEGRAM_API_PROBE_URL = "https://api.telegram.org/bot000000:INVALID/getMe"
STRICT_MT_ATTEMPTS = 2

MT_SECRET_RE = re.compile(
    r"^(?:"
    r"(?:[dD][dD])?[0-9A-Fa-f]{32,256}"  # classic hex / dd-prefixed hex
    r"|"
    r"[eE][eE][A-Za-z0-9+/=_-]{8,512}"  # FakeTLS (ee + base64/base64url payload)
    r")$"
)

console = Console()


@dataclass(slots=True)
class ProxyEntry:
    host: str
    port: int
    proxy_type: str  # http | socks5 | mtproto
    secret: Optional[str]
    raw: str

    def key(self) -> tuple[str, int, str, str]:
        return (self.host.lower(), self.port, self.proxy_type, self.secret or "")

    def display(self) -> str:
        if self.proxy_type == "http":
            return f"http://{self.host}:{self.port}"
        if self.proxy_type == "socks5":
            return f"socks5://{self.host}:{self.port}"
        if self.secret:
            return f"tg://proxy?server={self.host}&port={self.port}&secret={self.secret}"
        return f"mtproto://{self.host}:{self.port}"


@dataclass(slots=True)
class CheckResult:
    proxy: ProxyEntry
    ok: bool
    latency_ms: Optional[float]
    reason: str = ""


def _safe_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _looks_like_secret(value: str) -> bool:
    cleaned = value.strip()
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    return bool(MT_SECRET_RE.fullmatch(cleaned))


def _normalize_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    return cleaned


def parse_proxy_line(line: str) -> Optional[ProxyEntry]:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    # Telegram links:
    # tg://proxy?server=...&port=...&secret=...
    # https://t.me/proxy?server=...&port=...&secret=...
    lower = raw.lower()
    if "tg://proxy" in lower or "t.me/proxy" in lower:
        parsed = urlparse(raw)
        params = parse_qs(parsed.query)
        host = (params.get("server", [None])[0] or "").strip().rstrip(".")
        port = _safe_int(params.get("port", [None])[0])
        secret = _normalize_secret(params.get("secret", [None])[0])
        if host and port:
            return ProxyEntry(host=host, port=port, proxy_type="mtproto", secret=secret, raw=raw)
        return None

    # URL-style proxies: http://, socks5://, mtproto://
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").strip().rstrip(".")
        port = parsed.port
        if not host or not port:
            return None

        scheme = (parsed.scheme or "").lower()
        secret = _normalize_secret(parse_qs(parsed.query).get("secret", [None])[0])
        if scheme in {"http", "https"}:
            return ProxyEntry(host=host, port=port, proxy_type="http", secret=None, raw=raw)
        if scheme in {"socks5", "socks"}:
            return ProxyEntry(host=host, port=port, proxy_type="socks5", secret=None, raw=raw)
        if scheme in {"mtproto", "mtproxy"}:
            return ProxyEntry(host=host, port=port, proxy_type="mtproto", secret=secret, raw=raw)
        return None

    # Traditional format:
    # host:port
    # host:port:type
    # host:port:type:secret
    # host:port:secret
    parts = [p.strip() for p in raw.split(":")]
    if len(parts) < 2:
        return None

    host = parts[0].rstrip(".")
    port = _safe_int(parts[1])
    if not host or not port:
        return None

    proxy_type = "http"
    secret = None

    if len(parts) == 2:
        # Default to MTProto when unknown short format is used for Telegram lists.
        proxy_type = "mtproto"
    elif len(parts) >= 3:
        third = parts[2].lower()
        if third in {"http", "https"}:
            proxy_type = "http"
        elif third in {"socks5", "socks"}:
            proxy_type = "socks5"
        elif third in {"mtproto", "mtproxy"}:
            proxy_type = "mtproto"
            if len(parts) >= 4:
                secret = _normalize_secret(parts[3])
        else:
            # host:port:secret => treat as MTProto
            if _looks_like_secret(parts[2]):
                proxy_type = "mtproto"
                secret = _normalize_secret(parts[2])
            else:
                proxy_type = "http"

    return ProxyEntry(host=host, port=port, proxy_type=proxy_type, secret=secret, raw=raw)


def load_proxies(path: Path) -> list[ProxyEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    proxies: list[ProxyEntry] = []
    seen: set[tuple[str, int, str, str]] = set()

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parsed = parse_proxy_line(line)
        if not parsed:
            continue
        key = parsed.key()
        if key in seen:
            continue
        seen.add(key)
        proxies.append(parsed)

    return proxies


def _result_from_exception(proxy: ProxyEntry, exc: BaseException) -> CheckResult:
    return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason=exc.__class__.__name__)


def _is_telegram_api_payload(payload_text: str) -> bool:
    try:
        data = json.loads(payload_text)
    except Exception:
        return False
    return isinstance(data, dict) and "ok" in data


def _is_telegram_web_payload(payload_text: str) -> bool:
    text = payload_text.lower()
    markers = ("telegram", "t.me", "tg://")
    return any(marker in text for marker in markers)


async def _test_http_url(
    proxy: ProxyEntry,
    session: aiohttp.ClientSession,
    timeout: float,
    url: str,
) -> CheckResult:
    start = time.perf_counter()
    proxy_url = f"http://{proxy.host}:{proxy.port}"
    timeout_cfg = aiohttp.ClientTimeout(total=timeout, connect=timeout, sock_connect=timeout, sock_read=timeout)
    try:
        async with session.get(
            url,
            proxy=proxy_url,
            timeout=timeout_cfg,
            allow_redirects=False,
            ssl=False,
        ) as resp:
            payload_bytes = await resp.content.read(32768)
            payload_text = payload_bytes.decode("utf-8", errors="ignore")
            is_api_endpoint = "api.telegram.org" in url.lower()

            if resp.status >= 500:
                return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason=f"HTTP {resp.status}")

            if is_api_endpoint:
                if not _is_telegram_api_payload(payload_text):
                    return CheckResult(
                        proxy=proxy,
                        ok=False,
                        latency_ms=None,
                        reason="Non-Telegram API payload",
                    )
            else:
                if resp.status >= 400:
                    return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason=f"HTTP {resp.status}")
                if resp.status == 200 and not _is_telegram_web_payload(payload_text):
                    return CheckResult(
                        proxy=proxy,
                        ok=False,
                        latency_ms=None,
                        reason="Non-Telegram web payload",
                    )

            latency = (time.perf_counter() - start) * 1000
            return CheckResult(proxy=proxy, ok=True, latency_ms=latency)
    except Exception as exc:
        return _result_from_exception(proxy, exc)


async def _test_http(proxy: ProxyEntry, session: aiohttp.ClientSession, timeout: float) -> CheckResult:
    return await _test_http_url(proxy, session, timeout, TELEGRAM_API_PROBE_URL)


async def _test_http_strict(proxy: ProxyEntry, session: aiohttp.ClientSession, timeout: float) -> CheckResult:
    checks = [TELEGRAM_WEB_URL, TELEGRAM_API_PROBE_URL]
    latencies: list[float] = []
    for url in checks:
        result = await _test_http_url(proxy, session, timeout, url)
        if not result.ok:
            return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason=f"Strict HTTP failed: {result.reason}")
        if result.latency_ms is not None:
            latencies.append(result.latency_ms)

    avg = sum(latencies) / len(latencies) if latencies else None
    return CheckResult(proxy=proxy, ok=True, latency_ms=avg)


def _build_socks5_target_request(target_host: str, target_port: int) -> bytes:
    try:
        ip_obj = ipaddress.ip_address(target_host)
        if ip_obj.version == 4:
            return b"\x05\x01\x00\x01" + ip_obj.packed + target_port.to_bytes(2, "big")
        return b"\x05\x01\x00\x04" + ip_obj.packed + target_port.to_bytes(2, "big")
    except ValueError:
        host_bytes = target_host.encode("idna")
        if not host_bytes or len(host_bytes) > 255:
            raise ValueError("Invalid SOCKS target host")
        return b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + target_port.to_bytes(2, "big")


async def _test_socks5_target(proxy: ProxyEntry, timeout: float, target_host: str, target_port: int) -> CheckResult:
    start = time.perf_counter()
    writer: Optional[asyncio.StreamWriter] = None

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(proxy.host, proxy.port), timeout=timeout)

        # Greeting: SOCKS5 + 1 auth method + no-auth
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        handshake = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        if handshake != b"\x05\x00":
            return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason="SOCKS auth failed")

        request = _build_socks5_target_request(target_host, target_port)
        writer.write(request)
        await writer.drain()

        response = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
        if response[1] != 0x00:
            return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason=f"SOCKS code {response[1]}")

        atyp = response[3]
        if atyp == 0x01:  # IPv4
            await asyncio.wait_for(reader.readexactly(6), timeout=timeout)
        elif atyp == 0x03:  # Domain
            domain_len = await asyncio.wait_for(reader.readexactly(1), timeout=timeout)
            await asyncio.wait_for(reader.readexactly(domain_len[0] + 2), timeout=timeout)
        elif atyp == 0x04:  # IPv6
            await asyncio.wait_for(reader.readexactly(18), timeout=timeout)

        latency = (time.perf_counter() - start) * 1000
        return CheckResult(proxy=proxy, ok=True, latency_ms=latency)
    except Exception as exc:
        return _result_from_exception(proxy, exc)
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def _test_socks5(proxy: ProxyEntry, timeout: float) -> CheckResult:
    return await _test_socks5_target(proxy, timeout, TELEGRAM_DC_IP, TELEGRAM_PORT)


async def _test_socks5_strict(proxy: ProxyEntry, timeout: float) -> CheckResult:
    checks = [
        (TELEGRAM_DC_IP, TELEGRAM_PORT),
        (TELEGRAM_TEST_HOST, TELEGRAM_PORT),
    ]
    latencies: list[float] = []
    for target_host, target_port in checks:
        result = await _test_socks5_target(proxy, timeout, target_host, target_port)
        if not result.ok:
            return CheckResult(
                proxy=proxy,
                ok=False,
                latency_ms=None,
                reason=f"Strict SOCKS5 failed on {target_host}: {result.reason}",
            )
        if result.latency_ms is not None:
            latencies.append(result.latency_ms)

    avg = sum(latencies) / len(latencies) if latencies else None
    return CheckResult(proxy=proxy, ok=True, latency_ms=avg)


async def _test_mtproto_probe(proxy: ProxyEntry, timeout: float, require_reply: bool) -> CheckResult:
    start = time.perf_counter()
    writer: Optional[asyncio.StreamWriter] = None

    try:
        # MTProto proxies should include secret in most real Telegram deployments.
        if not proxy.secret:
            return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason="Missing MTProto secret")
        if not _looks_like_secret(proxy.secret):
            return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason="Bad secret format")

        reader, writer = await asyncio.wait_for(asyncio.open_connection(proxy.host, proxy.port), timeout=timeout)

        # Transport probe: send randomized payload and verify socket behavior.
        payload = bytearray(os.urandom(64))
        while payload[0] == 0xEF:
            payload = bytearray(os.urandom(64))
        writer.write(payload)
        await writer.drain()

        if require_reply:
            try:
                data = await asyncio.wait_for(reader.read(16), timeout=min(max(timeout / 2, 0.5), 1.5))
            except asyncio.TimeoutError:
                data = b""

            if not data:
                # If no immediate reply, verify the tunnel stays writable.
                writer.write(os.urandom(16))
                await writer.drain()
                await asyncio.sleep(0.45)
                if reader.at_eof():
                    return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason="No MTProto response")
        else:
            await asyncio.sleep(0.25)
            if reader.at_eof():
                return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason="Connection closed")

        latency = (time.perf_counter() - start) * 1000
        return CheckResult(proxy=proxy, ok=True, latency_ms=latency)
    except Exception as exc:
        return _result_from_exception(proxy, exc)
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def _test_mtproto(proxy: ProxyEntry, timeout: float) -> CheckResult:
    return await _test_mtproto_probe(proxy, timeout, require_reply=False)


async def _test_mtproto_strict(proxy: ProxyEntry, timeout: float) -> CheckResult:
    latencies: list[float] = []
    for _ in range(STRICT_MT_ATTEMPTS):
        result = await _test_mtproto_probe(proxy, timeout, require_reply=True)
        if not result.ok:
            return CheckResult(proxy=proxy, ok=False, latency_ms=None, reason=f"Strict MTProto failed: {result.reason}")
        if result.latency_ms is not None:
            latencies.append(result.latency_ms)

    avg = sum(latencies) / len(latencies) if latencies else None
    return CheckResult(proxy=proxy, ok=True, latency_ms=avg)


async def test_proxy(
    proxy: ProxyEntry,
    session: aiohttp.ClientSession,
    timeout: float,
    strict: bool = False,
) -> CheckResult:
    if proxy.proxy_type == "http":
        return await (_test_http_strict(proxy, session, timeout) if strict else _test_http(proxy, session, timeout))
    if proxy.proxy_type == "socks5":
        return await (_test_socks5_strict(proxy, timeout) if strict else _test_socks5(proxy, timeout))
    return await (_test_mtproto_strict(proxy, timeout) if strict else _test_mtproto(proxy, timeout))


async def check_proxies(
    proxies: list[ProxyEntry],
    timeout: float,
    concurrency: int,
    strict: bool = DEFAULT_STRICT,
) -> list[CheckResult]:
    if not proxies:
        return []

    worker_count = max(1, min(concurrency, len(proxies)))
    connector_limit = max(16, min(worker_count * 2, 2000))
    connector = aiohttp.TCPConnector(limit=connector_limit, ssl=False)
    headers = {"User-Agent": "Mozilla/5.0 TelegramProxyChecker/1.0"}

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        results: list[CheckResult] = []
        queue_size = max(64, worker_count * 4)
        queue: asyncio.Queue[ProxyEntry | None] = asyncio.Queue(maxsize=queue_size)

        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]Checking[/bold]"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            task_id = progress.add_task("scan", total=len(proxies))

            async def producer() -> None:
                for proxy in proxies:
                    await queue.put(proxy)
                for _ in range(worker_count):
                    await queue.put(None)

            async def worker() -> None:
                while True:
                    proxy = await queue.get()
                    if proxy is None:
                        queue.task_done()
                        break
                    try:
                        result = await test_proxy(proxy, session, timeout, strict=strict)
                    except Exception as exc:
                        result = _result_from_exception(proxy, exc)
                    results.append(result)
                    progress.advance(task_id)
                    queue.task_done()

            producer_task = asyncio.create_task(producer())
            workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
            await producer_task
            await queue.join()
            await asyncio.gather(*workers, return_exceptions=True)

        return results


def save_working(results: list[CheckResult], output_path: Path) -> int:
    working = [r for r in results if r.ok and r.latency_ms is not None]
    working.sort(key=lambda r: r.latency_ms or 1e9)

    lines = [
        "# Telegram Working Proxies",
        f"# Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"# Total working: {len(working)}",
        "# Format: proxy | latency_ms",
        "",
    ]

    for item in working:
        lines.append(f"{item.proxy.display()} | {item.latency_ms:.1f} ms")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(working)


def show_summary(proxies: list[ProxyEntry], results: list[CheckResult], output_path: Path) -> None:
    total = len(proxies)
    working = [r for r in results if r.ok and r.latency_ms is not None]
    dead = total - len(working)
    alive_percent = (len(working) / total * 100.0) if total else 0.0

    summary = (
        f"[cyan]Total:[/cyan] {total}\n"
        f"[green]Working:[/green] {len(working)} ({alive_percent:.1f}%)\n"
        f"[red]Dead:[/red] {dead}\n"
        f"[yellow]Output:[/yellow] {output_path}"
    )
    console.print(Panel(summary, title="Scan Summary", border_style="cyan"))

    if not working:
        return

    working.sort(key=lambda r: r.latency_ms or 1e9)
    table = Table(title="Top Working Proxies (Lowest Latency)")
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Proxy", style="green")
    table.add_column("Latency (ms)", justify="right", style="yellow")

    for idx, item in enumerate(working[:15], start=1):
        table.add_row(str(idx), item.proxy.proxy_type, item.proxy.display(), f"{item.latency_ms:.1f}")
    console.print(table)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="High-performance Telegram proxy checker")
    parser.add_argument("-i", "--input", default=DEFAULT_INPUT, help="Input proxy list file (default: proxies.txt)")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help="Output working proxy file (default: working.txt)")
    parser.add_argument(
        "-t", "--timeout", type=float, default=DEFAULT_TIMEOUT, help="Timeout in seconds per proxy (default: 5)"
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Max concurrent checks (default: 300)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable strict verification (slower, more accurate)",
    )
    return parser


async def async_main() -> int:
    args = build_arg_parser().parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    console.print(Panel("[bold cyan]Telegram Proxy Checker[/bold cyan]", border_style="cyan"))
    console.print(f"[cyan]Loading proxies from[/cyan] {input_path} ...")

    try:
        proxies = load_proxies(input_path)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    if not proxies:
        console.print("[red]No valid proxies found in input file.[/red]")
        return 1

    type_counts = {
        "http": sum(1 for p in proxies if p.proxy_type == "http"),
        "socks5": sum(1 for p in proxies if p.proxy_type == "socks5"),
        "mtproto": sum(1 for p in proxies if p.proxy_type == "mtproto"),
    }
    console.print(
        "[green]Loaded[/green] "
        f"{len(proxies)} proxies  "
        f"(HTTP: {type_counts['http']}, SOCKS5: {type_counts['socks5']}, MTProto: {type_counts['mtproto']})"
    )
    console.print(
        f"[cyan]Starting scan[/cyan]  timeout={args.timeout}s  concurrency={args.concurrency}  "
        f"target={TELEGRAM_TEST_HOST}/{TELEGRAM_DC_IP}:{TELEGRAM_PORT}  strict={args.strict}"
    )

    started = time.perf_counter()
    results = await check_proxies(
        proxies,
        timeout=args.timeout,
        concurrency=max(1, args.concurrency),
        strict=args.strict,
    )
    elapsed = time.perf_counter() - started

    working_count = save_working(results, output_path)
    show_summary(proxies, results, output_path)
    console.print(f"[bold green]Done[/bold green] in {elapsed:.2f}s | working={working_count}")
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(async_main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user[/yellow]")
        exit_code = 130
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
