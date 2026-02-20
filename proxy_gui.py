#!/usr/bin/env python3
"""
Purple-themed desktop UI for Telegram Proxy Checker.

Features:
- Manual proxy input and scan
- Load proxies from local file
- Fetch proxies from GitHub raw URLs
- Optional auto-fetch every N hours
- Async high-concurrency scanner with timeout
- Saves working proxies (with latency) to working.txt
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from typing import Iterable

import aiohttp
import customtkinter as ctk

from telegram_proxy_checker import (
    CheckResult,
    ProxyEntry,
    _looks_like_secret,
    parse_proxy_line,
    test_proxy,
)


DEFAULT_INPUT_FILE = "proxies.txt"
DEFAULT_OUTPUT_FILE = "working.txt"
DEFAULT_TIMEOUT = 5.0
DEFAULT_CONCURRENCY = 300
DEFAULT_AUTO_FETCH_HOURS = 3.0

DEFAULT_SOURCE_URLS = [
    "https://raw.githubusercontent.com/SoliSpirit/mtproto/master/all_proxies.txt",
    "https://raw.githubusercontent.com/ALIILAPRO/MTProtoProxy/main/mtproto.txt",
]

# Purple palette
BG_MAIN = "#110a1f"
BG_PANEL = "#1a1130"
BG_CARD = "#20153a"
BG_INPUT = "#160f2a"
FG_TEXT = "#f4eaff"
FG_SUBTLE = "#c7b4e8"
ACCENT = "#8b5cf6"
ACCENT_HOVER = "#7c3aed"
ACCENT_STRONG = "#a78bfa"


class ProxyCheckerGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Telegram Proxy Checker - Purple UI")
        self._set_adaptive_window_size()
        self.minsize(1020, 700)
        self.configure(fg_color=BG_MAIN)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.scan_thread: threading.Thread | None = None
        self.fetch_thread: threading.Thread | None = None
        self.auto_fetch_job: str | None = None
        self.stop_requested = False

        self.total_count = 0
        self.completed_count = 0
        self.working_results: list[CheckResult] = []

        self._build_ui()
        self._set_status("Ready")
        self._log("UI initialized. Paste proxies or load from file, then click Start Scan.")

    # ---------------------------------------------------------------------
    # UI setup
    # ---------------------------------------------------------------------
    def _set_adaptive_window_size(self) -> None:
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1500, int(screen_w * 0.9))
        height = min(960, int(screen_h * 0.9))
        pos_x = max((screen_w - width) // 2, 0)
        pos_y = max((screen_h - height) // 2, 0)
        self.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=3, minsize=450)
        self.grid_columnconfigure(1, weight=5)
        self.grid_rowconfigure(0, weight=1)

        self.left_panel = ctk.CTkScrollableFrame(
            self,
            corner_radius=14,
            fg_color=BG_PANEL,
            scrollbar_button_color=ACCENT,
            scrollbar_button_hover_color=ACCENT_HOVER,
        )
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(14, 8), pady=14)
        self.left_panel.grid_columnconfigure(0, weight=1)

        self.right_panel = ctk.CTkFrame(self, corner_radius=14, fg_color=BG_PANEL)
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 14), pady=14)
        self.right_panel.grid_columnconfigure(0, weight=1)
        self.right_panel.grid_rowconfigure(3, weight=4)
        self.right_panel.grid_rowconfigure(5, weight=2)

        self._build_left_controls()
        self._build_right_results()

    def _build_left_controls(self) -> None:
        title = ctk.CTkLabel(
            self.left_panel,
            text="Telegram Proxy Checker",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=ACCENT_STRONG,
        )
        title.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 2))

        subtitle = ctk.CTkLabel(
            self.left_panel,
            text="Purple UI | Manual input | GitHub auto-fetch",
            text_color=FG_SUBTLE,
            font=ctk.CTkFont(size=13),
        )
        subtitle.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))

        input_label = ctk.CTkLabel(
            self.left_panel,
            text="Proxy Input (paste manually here)",
            text_color=FG_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        input_label.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 6))

        self.proxy_input = ctk.CTkTextbox(
            self.left_panel,
            height=220,
            fg_color=BG_INPUT,
            border_color=ACCENT,
            border_width=1,
            text_color=FG_TEXT,
            wrap="none",
        )
        self.proxy_input.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 10))
        self.proxy_input.insert("1.0", "# Paste proxies here, one per line\n")

        button_row = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        button_row.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 10))
        button_row.grid_columnconfigure((0, 1, 2), weight=1)

        self.load_btn = ctk.CTkButton(
            button_row,
            text="Load proxies.txt",
            command=self._load_from_file,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="white",
        )
        self.load_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.fetch_btn = ctk.CTkButton(
            button_row,
            text="Fetch GitHub",
            command=self._fetch_sources_now,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="white",
        )
        self.fetch_btn.grid(row=0, column=1, sticky="ew", padx=3)

        self.clear_btn = ctk.CTkButton(
            button_row,
            text="Clear Input",
            command=self._clear_input,
            fg_color="#5b21b6",
            hover_color="#4c1d95",
            text_color="white",
        )
        self.clear_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        source_label = ctk.CTkLabel(
            self.left_panel,
            text="GitHub Raw Source URLs (Telegram proxies only)",
            text_color=FG_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        source_label.grid(row=5, column=0, sticky="w", padx=14, pady=(0, 6))

        self.sources_input = ctk.CTkTextbox(
            self.left_panel,
            height=110,
            fg_color=BG_INPUT,
            border_color=ACCENT,
            border_width=1,
            text_color=FG_TEXT,
        )
        self.sources_input.grid(row=6, column=0, sticky="nsew", padx=14, pady=(0, 10))
        self._populate_default_sources()

        auto_row = ctk.CTkFrame(self.left_panel, fg_color=BG_CARD, corner_radius=10)
        auto_row.grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 10))
        auto_row.grid_columnconfigure(1, weight=1)

        self.auto_fetch_var = ctk.BooleanVar(value=False)
        self.auto_fetch_switch = ctk.CTkSwitch(
            auto_row,
            text="Auto Fetch",
            variable=self.auto_fetch_var,
            command=self._toggle_auto_fetch,
            progress_color=ACCENT,
            button_color=ACCENT_STRONG,
            button_hover_color=ACCENT,
        )
        self.auto_fetch_switch.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.interval_entry = ctk.CTkEntry(
            auto_row,
            width=160,
            fg_color=BG_INPUT,
            text_color=FG_TEXT,
            border_color=ACCENT,
        )
        self.interval_entry.insert(0, str(DEFAULT_AUTO_FETCH_HOURS))
        self.interval_entry.grid(row=0, column=1, padx=(8, 10), pady=10, sticky="ew")

        interval_label = ctk.CTkLabel(auto_row, text="hours", text_color=FG_SUBTLE)
        interval_label.grid(row=0, column=2, padx=(0, 10), pady=10, sticky="e")

        settings_frame = ctk.CTkFrame(self.left_panel, fg_color=BG_CARD, corner_radius=10)
        settings_frame.grid(row=8, column=0, sticky="ew", padx=14, pady=(0, 10))
        settings_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(settings_frame, text="Input File", text_color=FG_SUBTLE).grid(
            row=0, column=0, padx=10, pady=(10, 6), sticky="w"
        )
        self.input_file_entry = ctk.CTkEntry(
            settings_frame, fg_color=BG_INPUT, text_color=FG_TEXT, border_color=ACCENT
        )
        self.input_file_entry.insert(0, DEFAULT_INPUT_FILE)
        self.input_file_entry.grid(row=0, column=1, padx=10, pady=(10, 6), sticky="ew")

        ctk.CTkLabel(settings_frame, text="Output File", text_color=FG_SUBTLE).grid(
            row=1, column=0, padx=10, pady=6, sticky="w"
        )
        self.output_file_entry = ctk.CTkEntry(
            settings_frame, fg_color=BG_INPUT, text_color=FG_TEXT, border_color=ACCENT
        )
        self.output_file_entry.insert(0, DEFAULT_OUTPUT_FILE)
        self.output_file_entry.grid(row=1, column=1, padx=10, pady=6, sticky="ew")

        ctk.CTkLabel(settings_frame, text="Timeout (sec)", text_color=FG_SUBTLE).grid(
            row=2, column=0, padx=10, pady=6, sticky="w"
        )
        self.timeout_entry = ctk.CTkEntry(
            settings_frame, fg_color=BG_INPUT, text_color=FG_TEXT, border_color=ACCENT
        )
        self.timeout_entry.insert(0, str(DEFAULT_TIMEOUT))
        self.timeout_entry.grid(row=2, column=1, padx=10, pady=6, sticky="ew")

        ctk.CTkLabel(settings_frame, text="Concurrency", text_color=FG_SUBTLE).grid(
            row=3, column=0, padx=10, pady=(6, 10), sticky="w"
        )
        self.concurrency_entry = ctk.CTkEntry(
            settings_frame, fg_color=BG_INPUT, text_color=FG_TEXT, border_color=ACCENT
        )
        self.concurrency_entry.insert(0, str(DEFAULT_CONCURRENCY))
        self.concurrency_entry.grid(row=3, column=1, padx=10, pady=6, sticky="ew")

        self.strict_mode_var = ctk.BooleanVar(value=True)
        self.strict_mode_switch = ctk.CTkSwitch(
            settings_frame,
            text="Strict Mode (slower, more accurate)",
            variable=self.strict_mode_var,
            progress_color=ACCENT,
            button_color=ACCENT_STRONG,
            button_hover_color=ACCENT,
            text_color=FG_TEXT,
        )
        self.strict_mode_switch.grid(row=4, column=0, columnspan=2, padx=10, pady=(4, 10), sticky="w")

        action_row = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        action_row.grid(row=9, column=0, sticky="ew", padx=14, pady=(0, 14))
        action_row.grid_columnconfigure((0, 1, 2), weight=1)

        self.start_btn = ctk.CTkButton(
            action_row,
            text="Start Scan",
            command=self._start_scan,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="white",
        )
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.stop_btn = ctk.CTkButton(
            action_row,
            text="Stop",
            command=self._stop_scan,
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            text_color="white",
            state="disabled",
        )
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=3)

        self.save_btn = ctk.CTkButton(
            action_row,
            text="Save Working",
            command=self._save_working_now,
            fg_color="#5b21b6",
            hover_color="#4c1d95",
            text_color="white",
            state="disabled",
        )
        self.save_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0))

    def _build_right_results(self) -> None:
        stats_row = ctk.CTkFrame(self.right_panel, fg_color=BG_CARD, corner_radius=10)
        stats_row.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        stats_row.grid_columnconfigure((0, 1, 2), weight=1)

        self.total_label = ctk.CTkLabel(stats_row, text="Total: 0", text_color=FG_TEXT, font=ctk.CTkFont(size=14, weight="bold"))
        self.total_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.working_label = ctk.CTkLabel(stats_row, text="Working: 0", text_color="#86efac", font=ctk.CTkFont(size=14, weight="bold"))
        self.working_label.grid(row=0, column=1, padx=10, pady=10, sticky="w")

        self.dead_label = ctk.CTkLabel(stats_row, text="Dead: 0", text_color="#fca5a5", font=ctk.CTkFont(size=14, weight="bold"))
        self.dead_label.grid(row=0, column=2, padx=10, pady=10, sticky="w")

        progress_frame = ctk.CTkFrame(self.right_panel, fg_color=BG_CARD, corner_radius=10)
        progress_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        progress_frame.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(progress_frame, progress_color=ACCENT, fg_color="#2b1c4c")
        self.progress_bar.set(0)
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))

        self.progress_text = ctk.CTkLabel(progress_frame, text="0 / 0", text_color=FG_SUBTLE)
        self.progress_text.grid(row=1, column=0, padx=10, pady=(0, 2), sticky="w")

        self.status_text = ctk.CTkLabel(progress_frame, text="Ready", text_color=ACCENT_STRONG)
        self.status_text.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="w")

        result_label = ctk.CTkLabel(
            self.right_panel,
            text="Working Proxies",
            text_color=FG_TEXT,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        result_label.grid(row=2, column=0, sticky="w", padx=14, pady=(2, 6))

        table_frame = ctk.CTkFrame(self.right_panel, fg_color=BG_CARD, corner_radius=10)
        table_frame.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 10))
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        self._configure_tree_style()
        columns = ("idx", "type", "proxy", "latency")
        self.result_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        self.result_tree.heading("idx", text="#")
        self.result_tree.heading("type", text="Type")
        self.result_tree.heading("proxy", text="Proxy")
        self.result_tree.heading("latency", text="Latency (ms)")
        self.result_tree.column("idx", width=50, anchor="center")
        self.result_tree.column("type", width=90, anchor="center")
        self.result_tree.column("proxy", width=620, anchor="w")
        self.result_tree.column("latency", width=120, anchor="center")
        self.result_tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        self.result_tree.bind("<Double-1>", lambda _event: self._copy_selected_proxy())
        self.result_tree.bind("<Control-c>", self._on_tree_ctrl_c)

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.result_tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns", pady=10, padx=(0, 10))
        self.result_tree.configure(yscrollcommand=yscroll.set)

        table_actions = ctk.CTkFrame(table_frame, fg_color="transparent")
        table_actions.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        table_actions.grid_columnconfigure((0, 1), weight=1)

        self.copy_selected_btn = ctk.CTkButton(
            table_actions,
            text="Copy Selected",
            command=self._copy_selected_proxy,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="white",
            state="disabled",
        )
        self.copy_selected_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.copy_all_btn = ctk.CTkButton(
            table_actions,
            text="Copy All Working",
            command=self._copy_all_working_proxies,
            fg_color="#5b21b6",
            hover_color="#4c1d95",
            text_color="white",
            state="disabled",
        )
        self.copy_all_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        logs_label = ctk.CTkLabel(
            self.right_panel,
            text="Logs",
            text_color=FG_TEXT,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        logs_label.grid(row=4, column=0, sticky="w", padx=14, pady=(2, 6))

        self.logs_box = ctk.CTkTextbox(
            self.right_panel,
            fg_color=BG_CARD,
            border_color=ACCENT,
            border_width=1,
            text_color=FG_TEXT,
            state="disabled",
        )
        self.logs_box.grid(row=5, column=0, sticky="nsew", padx=14, pady=(0, 14))

    def _configure_tree_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=BG_INPUT,
            fieldbackground=BG_INPUT,
            foreground=FG_TEXT,
            rowheight=36,
            bordercolor=BG_INPUT,
            lightcolor=BG_INPUT,
            darkcolor=BG_INPUT,
            font=("Segoe UI", 12),
        )
        style.configure(
            "Treeview.Heading",
            background=ACCENT_HOVER,
            foreground="white",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 12, "bold"),
        )
        style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        style.map("Treeview.Heading", background=[("active", ACCENT)])

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.status_text.configure(text=text)

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logs_box.configure(state="normal")
        self.logs_box.insert("end", f"[{stamp}] {message}\n")
        self.logs_box.see("end")
        self.logs_box.configure(state="disabled")

    def _log_from_thread(self, message: str) -> None:
        self.after(0, lambda: self._log(message))

    def _read_float(self, value: str, default: float, minimum: float) -> float:
        try:
            parsed = float(value.strip())
            return max(minimum, parsed)
        except Exception:
            return default

    def _read_int(self, value: str, default: int, minimum: int) -> int:
        try:
            parsed = int(value.strip())
            return max(minimum, parsed)
        except Exception:
            return default

    def _get_proxy_lines(self) -> list[str]:
        return [line.strip() for line in self.proxy_input.get("1.0", "end").splitlines()]

    def _collect_entries(self, lines: Iterable[str]) -> tuple[list[ProxyEntry], int]:
        entries: list[ProxyEntry] = []
        seen: set[tuple[str, int, str, str]] = set()
        invalid = 0

        for line in lines:
            if not line or line.startswith("#"):
                continue
            parsed = parse_proxy_line(line)
            if parsed is None:
                invalid += 1
                continue
            key = parsed.key()
            if key in seen:
                continue
            seen.add(key)
            entries.append(parsed)

        return entries, invalid

    def _append_proxy_lines(self, lines: Iterable[str]) -> int:
        existing_entries, _ = self._collect_entries(self._get_proxy_lines())
        existing_keys = {entry.key() for entry in existing_entries}
        added = 0

        for line in lines:
            parsed = parse_proxy_line(line.strip())
            if parsed is None:
                continue
            key = parsed.key()
            if key in existing_keys:
                continue
            existing_keys.add(key)
            self.proxy_input.insert("end", parsed.display() + "\n")
            added += 1

        return added

    def _update_stats(self) -> None:
        working = len(self.working_results)
        dead = max(self.completed_count - working, 0)
        self.total_label.configure(text=f"Total: {self.total_count}")
        self.working_label.configure(text=f"Working: {working}")
        self.dead_label.configure(text=f"Dead: {dead}")
        self.progress_text.configure(text=f"{self.completed_count} / {self.total_count}")
        progress = 0.0 if self.total_count == 0 else self.completed_count / self.total_count
        self.progress_bar.set(progress)

    def _clear_results_table(self) -> None:
        for row_id in self.result_tree.get_children():
            self.result_tree.delete(row_id)
        self.copy_selected_btn.configure(state="disabled")
        self.copy_all_btn.configure(state="disabled")

    def _copy_to_clipboard(self, text: str) -> None:
        if not text.strip():
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _selected_tree_proxy(self) -> str | None:
        selected = self.result_tree.selection()
        if not selected:
            return None
        values = self.result_tree.item(selected[0], "values")
        if len(values) < 3:
            return None
        return str(values[2]).strip()

    def _copy_selected_proxy(self) -> None:
        proxy_text = self._selected_tree_proxy()
        if not proxy_text:
            self._log("No row selected in Working Proxies.")
            return
        self._copy_to_clipboard(proxy_text)
        self._log("Selected proxy copied to clipboard.")

    def _copy_all_working_proxies(self) -> None:
        if not self.working_results:
            self._log("No working proxies to copy.")
            return
        ordered = sorted(self.working_results, key=lambda item: item.latency_ms or 1e9)
        lines = [item.proxy.display() for item in ordered]
        self._copy_to_clipboard("\n".join(lines))
        self._log(f"Copied {len(lines)} working proxies to clipboard.")

    def _on_tree_ctrl_c(self, _event) -> str:
        self._copy_selected_proxy()
        return "break"

    # ---------------------------------------------------------------------
    # Input and source management
    # ---------------------------------------------------------------------
    def _clear_input(self) -> None:
        self.proxy_input.delete("1.0", "end")
        self.proxy_input.insert("1.0", "# Paste proxies here, one per line\n")
        self._log("Proxy input cleared.")

    def _load_from_file(self) -> None:
        file_path = Path(self.input_file_entry.get().strip() or DEFAULT_INPUT_FILE)
        if not file_path.exists():
            self._log(f"Input file not found: {file_path}")
            return

        lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        added = self._append_proxy_lines(lines)
        self._log(f"Loaded {added} proxies from {file_path}.")

    def _get_source_urls(self) -> list[str]:
        urls = []
        for line in self.sources_input.get("1.0", "end").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
        return urls

    def _populate_default_sources(self) -> None:
        self.sources_input.delete("1.0", "end")
        self.sources_input.insert("1.0", "\n".join(DEFAULT_SOURCE_URLS))

    def _fetch_sources_now(self) -> None:
        if self.fetch_thread and self.fetch_thread.is_alive():
            self._log("Fetch is already running.")
            return

        urls = self._get_source_urls()
        if not urls:
            self._populate_default_sources()
            urls = self._get_source_urls()
            self._log("No source URL configured. Default Telegram sources restored.")
            if not urls:
                self._log("No source URL configured.")
                return

        self.fetch_btn.configure(state="disabled")
        self._log(f"Fetching from {len(urls)} GitHub source(s)...")
        self.fetch_thread = threading.Thread(target=self._fetch_worker, args=(urls,), daemon=True)
        self.fetch_thread.start()

    def _fetch_worker(self, urls: list[str]) -> None:
        try:
            fetched_lines = asyncio.run(self._fetch_worker_async(urls))
        except Exception as exc:
            self._log_from_thread(f"Fetch worker failed: {exc}")
            fetched_lines = []
        self.after(0, lambda: self._on_fetch_done(fetched_lines))

    async def _fetch_worker_async(self, urls: list[str]) -> list[str]:
        headers = {"User-Agent": "Mozilla/5.0 ProxyCheckerGUI/1.0"}
        timeout = aiohttp.ClientTimeout(total=25, connect=10, sock_connect=10, sock_read=20)
        connector = aiohttp.TCPConnector(limit=max(8, min(64, len(urls) * 4)), ssl=False)
        semaphore = asyncio.Semaphore(min(10, max(2, len(urls))))

        async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:

            async def fetch_one(index: int, url: str) -> list[str]:
                async with semaphore:
                    try:
                        async with session.get(url, allow_redirects=True) as resp:
                            if resp.status >= 400:
                                raise RuntimeError(f"HTTP {resp.status}")
                            text = await resp.text(errors="ignore")

                        valid_lines: list[str] = []
                        for line in text.splitlines():
                            parsed = self._parse_telegram_fetch_line(line.strip())
                            if parsed is None:
                                continue
                            valid_lines.append(parsed.display())

                        self._log_from_thread(
                            f"[{index}/{len(urls)}] {url} -> {len(valid_lines)} telegram proxy/proxies"
                        )
                        return valid_lines
                    except Exception as exc:
                        self._log_from_thread(f"[{index}/{len(urls)}] Failed {url}: {exc}")
                        return []

            tasks = [fetch_one(idx, url) for idx, url in enumerate(urls, start=1)]
            chunks = await asyncio.gather(*tasks, return_exceptions=False)

        merged: list[str] = []
        for chunk in chunks:
            merged.extend(chunk)
        return merged

    def _on_fetch_done(self, fetched_lines: list[str]) -> None:
        added = self._append_proxy_lines(fetched_lines)
        self._log(f"Fetch complete. Added {added} new Telegram proxy/proxies.")
        self.fetch_btn.configure(state="normal")

    def _parse_telegram_fetch_line(self, line: str) -> ProxyEntry | None:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            return None

        lower = raw.lower()
        is_tg_url = "tg://proxy" in lower or "t.me/proxy" in lower
        is_mtproto_tagged = "mtproto" in lower or "mtproxy" in lower

        parts = [part.strip() for part in raw.split(":")]
        secret_candidate = None
        if len(parts) >= 4 and parts[2].lower() in {"mtproto", "mtproxy"}:
            secret_candidate = parts[3]
        elif len(parts) >= 3 and parts[2].lower() not in {"http", "https", "socks5", "socks"}:
            secret_candidate = parts[2]

        has_mtproto_secret = bool(secret_candidate and _looks_like_secret(secret_candidate))
        if not (is_tg_url or is_mtproto_tagged or has_mtproto_secret):
            return None

        parsed = parse_proxy_line(raw)
        if parsed is None:
            return None
        if parsed.proxy_type != "mtproto":
            return None
        if not parsed.secret:
            # Keep GitHub fetch focused on true Telegram shareable format (tg:// with secret).
            return None
        return parsed

    def _toggle_auto_fetch(self) -> None:
        if self.auto_fetch_var.get():
            self._log("Auto-fetch enabled.")
            self._schedule_auto_fetch(run_immediately=True)
            return

        if self.auto_fetch_job is not None:
            self.after_cancel(self.auto_fetch_job)
            self.auto_fetch_job = None
        self._log("Auto-fetch disabled.")

    def _schedule_auto_fetch(self, run_immediately: bool) -> None:
        if not self.auto_fetch_var.get():
            return

        if self.auto_fetch_job is not None:
            self.after_cancel(self.auto_fetch_job)
            self.auto_fetch_job = None

        hours = self._read_float(self.interval_entry.get(), DEFAULT_AUTO_FETCH_HOURS, 0.1)
        delay_ms = 1500 if run_immediately else int(hours * 3600 * 1000)
        self.auto_fetch_job = self.after(delay_ms, self._auto_fetch_tick)

    def _auto_fetch_tick(self) -> None:
        if not self.auto_fetch_var.get():
            return

        self._log("Auto-fetch trigger.")
        self._fetch_sources_now()
        self._schedule_auto_fetch(run_immediately=False)

    # ---------------------------------------------------------------------
    # Scan lifecycle
    # ---------------------------------------------------------------------
    def _start_scan(self) -> None:
        if self.scan_thread and self.scan_thread.is_alive():
            self._log("A scan is already running.")
            return

        proxies, invalid = self._collect_entries(self._get_proxy_lines())
        if not proxies:
            self._log("No valid proxies to test.")
            return

        timeout = self._read_float(self.timeout_entry.get(), DEFAULT_TIMEOUT, 0.5)
        concurrency = self._read_int(self.concurrency_entry.get(), DEFAULT_CONCURRENCY, 1)
        strict_mode = bool(self.strict_mode_var.get())

        self.stop_requested = False
        self.total_count = len(proxies)
        self.completed_count = 0
        self.working_results = []
        self._update_stats()
        self._clear_results_table()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.save_btn.configure(state="disabled")

        self._set_status("Scanning...")
        self._log(
            f"Scan started: total={len(proxies)}, timeout={timeout}s, concurrency={concurrency}, strict={strict_mode}, invalid_lines={invalid}"
        )

        self.scan_thread = threading.Thread(
            target=self._scan_worker, args=(proxies, timeout, concurrency, strict_mode), daemon=True
        )
        self.scan_thread.start()

    def _stop_scan(self) -> None:
        if not (self.scan_thread and self.scan_thread.is_alive()):
            return
        self.stop_requested = True
        self._set_status("Stopping...")
        self._log("Stop requested. Waiting for active tasks to exit.")

    def _scan_worker(self, proxies: list[ProxyEntry], timeout: float, concurrency: int, strict_mode: bool) -> None:
        try:
            results = asyncio.run(self._scan_async(proxies, timeout, concurrency, strict_mode))
            self.after(0, lambda: self._on_scan_finished(results, None))
        except Exception as exc:
            self.after(0, lambda: self._on_scan_finished([], str(exc)))

    async def _scan_async(
        self,
        proxies: list[ProxyEntry],
        timeout: float,
        concurrency: int,
        strict_mode: bool,
    ) -> list[CheckResult]:
        if not proxies:
            return []

        worker_count = max(1, min(concurrency, len(proxies)))
        connector_limit = max(16, min(worker_count * 2, 2000))
        connector = aiohttp.TCPConnector(limit=connector_limit, ssl=False)
        headers = {"User-Agent": "Mozilla/5.0 ProxyCheckerGUI/1.0"}
        results: list[CheckResult] = []
        queue_size = max(64, worker_count * 4)
        queue: asyncio.Queue[ProxyEntry | None] = asyncio.Queue(maxsize=queue_size)

        async with aiohttp.ClientSession(connector=connector, headers=headers) as session:

            async def producer() -> None:
                for proxy in proxies:
                    if self.stop_requested:
                        break
                    await queue.put(proxy)
                for _ in range(worker_count):
                    await queue.put(None)

            async def worker() -> None:
                while True:
                    proxy = await queue.get()
                    if proxy is None:
                        queue.task_done()
                        break

                    if self.stop_requested:
                        queue.task_done()
                        continue

                    try:
                        result = await test_proxy(proxy, session, timeout, strict=strict_mode)
                    except Exception as exc:
                        result = CheckResult(proxy=proxy, ok=False, latency_ms=None, reason=exc.__class__.__name__)

                    results.append(result)
                    self.after(0, lambda r=result: self._on_scan_progress(r))
                    queue.task_done()

            producer_task = asyncio.create_task(producer())
            workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
            await producer_task
            await queue.join()
            await asyncio.gather(*workers, return_exceptions=True)

        return results

    def _on_scan_progress(self, result: CheckResult) -> None:
        self.completed_count += 1

        if result.ok and result.latency_ms is not None:
            self.working_results.append(result)
            index = len(self.working_results)
            self.result_tree.insert(
                "",
                "end",
                values=(index, result.proxy.proxy_type.upper(), result.proxy.display(), f"{result.latency_ms:.1f}"),
            )
            self.copy_selected_btn.configure(state="normal")
            self.copy_all_btn.configure(state="normal")

        self._update_stats()

    def _on_scan_finished(self, _results: list[CheckResult], error: str | None) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

        if error:
            self._set_status("Error")
            self._log(f"Scan failed: {error}")
            return

        if self.stop_requested:
            self._set_status(f"Stopped ({self.completed_count}/{self.total_count})")
            self._log("Scan stopped by user.")
        else:
            self._set_status("Completed")
            self._log("Scan completed.")

        saved = self._save_working_file()
        if saved >= 0:
            output_file = self.output_file_entry.get().strip() or DEFAULT_OUTPUT_FILE
            self._log(f"Saved {saved} working proxy/proxies to {output_file}.")
            if saved > 0:
                self.save_btn.configure(state="normal")
                self.copy_all_btn.configure(state="normal")

    # ---------------------------------------------------------------------
    # Save output
    # ---------------------------------------------------------------------
    def _save_working_file(self) -> int:
        output_file = self.output_file_entry.get().strip() or DEFAULT_OUTPUT_FILE
        path = Path(output_file)
        ordered = sorted(self.working_results, key=lambda item: item.latency_ms or 1e9)

        lines = [
            "# Telegram Working Proxies",
            f"# Generated: {datetime.now().isoformat(timespec='seconds')}",
            f"# Total working: {len(ordered)}",
            "# Format: proxy | latency_ms",
            "",
        ]
        for item in ordered:
            if item.latency_ms is None:
                continue
            lines.append(f"{item.proxy.display()} | {item.latency_ms:.1f} ms")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(ordered)

    def _save_working_now(self) -> None:
        if not self.working_results:
            self._log("No working proxies to save yet.")
            return
        count = self._save_working_file()
        output_file = self.output_file_entry.get().strip() or DEFAULT_OUTPUT_FILE
        self._log(f"Saved {count} working proxy/proxies to {output_file}.")

    # ---------------------------------------------------------------------
    # App shutdown
    # ---------------------------------------------------------------------
    def _on_close(self) -> None:
        self.stop_requested = True
        if self.auto_fetch_job is not None:
            self.after_cancel(self.auto_fetch_job)
            self.auto_fetch_job = None
        self.destroy()


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = ProxyCheckerGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
