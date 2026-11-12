from __future__ import annotations

import json
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from .admin import is_admin
from .engine import Engine
from .links import ParsedLink
from .netutil import run_cmd
from .paths import JOB_FILE, LOG_FILE, ROOT, STATUS_FILE, WORK, ensure_dirs
from .remnawave import parse_servers
from .settings import load_settings, save_settings


BG = "#0E1218"
CARD = "#161C26"
FIELD = "#0B1017"
STROKE = "#2A3344"
TEXT = "#E8EDF5"
MUTED = "#8B95A8"
HINT = "#6F7A8C"
ACCENT = "#1FAE70"
ACCENT_HOVER = "#19975F"
DANGER = "#C44B4B"
WARN = "#D3A23A"
PILL_IDLE = "#C9A227"
INFO_GREEN = "#2EE59D"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("Xray Wi‑Fi Share")
        self.geometry("960x860")
        self.minsize(860, 740)
        self.configure(fg_color=BG)

        self.engine = Engine()
        self._busy = False
        self._running = False
        self._show_password = False
        self._log_offset = 0
        self._poll_after: str | None = None
        self._anim_after: str | None = None
        self._last_status = ""
        self._last_error_shown = ""
        self._adapters_raw: list = []
        self._saved_adapter = ""
        self._servers: list[ParsedLink] = []
        self._saved_server = ""
        self._parse_token = 0
        self._log_mode = "brief"
        self._pill_pulse = 0
        self._fade_cards: list[ctk.CTkFrame] = []

        ensure_dirs()
        self._build()
        self.refresh_adapters()
        self._follow_log_from_end()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self._focus_link)
        self.after(180, self._start_polling)
        self.after(40, self._run_intro_animation)
        if is_admin():
            self._ui_log("INFO", "Запущено с правами администратора")
        else:
            self._ui_log("WARN", "Нет прав администратора — сетевые операции могут падать")

    def _card(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16, border_width=1, border_color=STROKE)
        self._fade_cards.append(frame)
        return frame

    def _build(self) -> None:
        self.shell = ctk.CTkFrame(self, fg_color=BG)
        self.shell.pack(fill="both", expand=True, padx=22, pady=18)

        # —— Header ——
        head = ctk.CTkFrame(self.shell, fg_color="transparent")
        head.pack(fill="x")

        title_row = ctk.CTkFrame(head, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(
            title_row,
            text="Xray Wi‑Fi Share",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=26),
            text_color=TEXT,
        ).pack(side="left")
        self.status_pill = ctk.CTkLabel(
            title_row,
            text="  Остановлено  ",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#1A1408",
            fg_color=PILL_IDLE,
            corner_radius=999,
        )
        self.status_pill.pack(side="right", pady=4)

        ctk.CTkLabel(
            head,
            text="Раздача VLESS / Hysteria2 / подписки через SoftAP",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=MUTED,
        ).pack(anchor="w", pady=(2, 0))

        # —— Connection ——
        conn = self._card(self.shell)
        conn.pack(fill="x", pady=(16, 10))
        conn_in = ctk.CTkFrame(conn, fg_color="transparent")
        conn_in.pack(fill="x", padx=18, pady=16)

        ctk.CTkLabel(
            conn_in,
            text="Настройки подключения",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=15),
            text_color=TEXT,
        ).pack(anchor="w")

        cols = ctk.CTkFrame(conn_in, fg_color="transparent")
        cols.pack(fill="x", pady=(12, 0))
        cols.columnconfigure(0, weight=3)
        cols.columnconfigure(1, weight=2)

        left = ctk.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        ctk.CTkLabel(left, text="Ссылка / URL подписки / Raw JSON", text_color=MUTED).pack(anchor="w")

        link_row = ctk.CTkFrame(left, fg_color="transparent")
        link_row.pack(fill="x", pady=(6, 0))
        self.link_entry = ctk.CTkEntry(
            link_row,
            height=42,
            fg_color=FIELD,
            border_color=STROKE,
            text_color=TEXT,
            placeholder_text="vless://uuid@server:443?...  ·  https://sub…  ·  JSON",
            font=ctk.CTkFont(family="Cascadia Mono", size=12),
        )
        self.link_entry.pack(side="left", fill="x", expand=True)
        self.paste_btn = ctk.CTkButton(
            link_row,
            text="⧉  Вставить",
            width=108,
            height=42,
            fg_color=STROKE,
            hover_color="#364257",
            command=self.paste_link,
        )
        self.paste_btn.pack(side="left", padx=(8, 0))
        self.load_btn = ctk.CTkButton(
            link_row,
            text="Загрузить",
            width=108,
            height=42,
            fg_color=STROKE,
            hover_color="#364257",
            command=self.load_servers,
        )
        self.load_btn.pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            left,
            text="Ссылка, URL подписки Remnawave или Raw JSON — затем выбери сервер",
            text_color=HINT,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(6, 0))

        srv = ctk.CTkFrame(left, fg_color="transparent")
        srv.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(srv, text="Сервер", text_color=MUTED).pack(anchor="w")
        self.server_var = tk.StringVar(value="— загрузи ссылку / JSON —")
        self.server_menu = ctk.CTkOptionMenu(
            srv,
            variable=self.server_var,
            values=["— загрузи ссылку / JSON —"],
            height=40,
            fg_color=FIELD,
            button_color=STROKE,
            button_hover_color="#364257",
            dropdown_fg_color=CARD,
            command=lambda _v: self._on_server_changed(),
        )
        self.server_menu.pack(fill="x", pady=(6, 0))
        self.server_hint = ctk.CTkLabel(
            srv,
            text="Список серверов появится после загрузки",
            text_color=HINT,
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.server_hint.pack(fill="x", pady=(6, 0))

        self.link_entry.bind("<Control-v>", self._on_ctrl_v)
        self.link_entry.bind("<Control-V>", self._on_ctrl_v)
        self.link_entry.bind("<<Paste>>", self._on_ctrl_v)
        self.link_entry.bind("<Shift-Insert>", self._on_ctrl_v)
        self.link_entry.bind("<Return>", lambda _e: self.load_servers())

        right = ctk.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        ad_top = ctk.CTkFrame(right, fg_color="transparent")
        ad_top.pack(fill="x")
        ctk.CTkLabel(ad_top, text="Wi‑Fi адаптер", text_color=MUTED).pack(side="left")
        ctk.CTkButton(
            ad_top,
            text="↻",
            width=34,
            height=24,
            fg_color="transparent",
            hover_color="#222836",
            text_color=MUTED,
            command=self.refresh_adapters,
        ).pack(side="right")

        self.adapter_var = tk.StringVar(value="")
        self.adapter_menu = ctk.CTkOptionMenu(
            right,
            variable=self.adapter_var,
            values=["—"],
            height=42,
            fg_color=FIELD,
            button_color=STROKE,
            button_hover_color="#364257",
            dropdown_fg_color=CARD,
            command=lambda _v: self._on_adapter_changed(),
        )
        self.adapter_menu.pack(fill="x", pady=(6, 0))

        self.adapter_hint = ctk.CTkLabel(
            right,
            text="Проверка режима точки доступа…",
            text_color=HINT,
            font=ctk.CTkFont(size=12),
            anchor="w",
        )
        self.adapter_hint.pack(fill="x", pady=(8, 0))

        # —— Hotspot ——
        ap = self._card(self.shell)
        ap.pack(fill="x", pady=(0, 10))
        ap_in = ctk.CTkFrame(ap, fg_color="transparent")
        ap_in.pack(fill="x", padx=18, pady=16)

        ctk.CTkLabel(
            ap_in,
            text="Параметры точки доступа",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=15),
            text_color=TEXT,
        ).pack(anchor="w")

        ap_grid = ctk.CTkFrame(ap_in, fg_color="transparent")
        ap_grid.pack(fill="x", pady=(12, 0))
        ap_grid.columnconfigure(0, weight=1)
        ap_grid.columnconfigure(1, weight=1)

        ssid_f = ctk.CTkFrame(ap_grid, fg_color="transparent")
        ssid_f.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        ctk.CTkLabel(ssid_f, text="SSID (имя сети)", text_color=MUTED).pack(anchor="w")
        self.ssid_entry = ctk.CTkEntry(ssid_f, height=42, fg_color=FIELD, border_color=STROKE)
        self.ssid_entry.pack(fill="x", pady=(6, 0))

        pw_f = ctk.CTkFrame(ap_grid, fg_color="transparent")
        pw_f.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(pw_f, text="Пароль (минимум 8 символов)", text_color=MUTED).pack(anchor="w")
        pw_row = ctk.CTkFrame(pw_f, fg_color="transparent")
        pw_row.pack(fill="x", pady=(6, 0))
        self.pass_entry = ctk.CTkEntry(pw_row, height=42, fg_color=FIELD, border_color=STROKE, show="•")
        self.pass_entry.pack(side="left", fill="x", expand=True)
        self.show_pw_btn = ctk.CTkButton(
            pw_row,
            text="Показать",
            width=100,
            height=42,
            fg_color=STROKE,
            hover_color="#364257",
            command=self.toggle_password,
        )
        self.show_pw_btn.pack(side="left", padx=(8, 0))

        # —— Actions ——
        actions = ctk.CTkFrame(self.shell, fg_color="transparent")
        actions.pack(fill="x", pady=(2, 10))

        self.start_btn = ctk.CTkButton(
            actions,
            text="⏻   Запустить",
            height=48,
            corner_radius=12,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=14),
            command=self.on_start,
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            actions,
            text="■   Остановить",
            height=48,
            corner_radius=12,
            fg_color="transparent",
            border_width=1,
            border_color=STROKE,
            hover_color="#222836",
            text_color=TEXT,
            state="disabled",
            command=self.on_stop,
        )
        self.stop_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.kill_btn = ctk.CTkButton(
            actions,
            text="☠   Принудительно завершить",
            height=48,
            corner_radius=12,
            fg_color="transparent",
            border_width=1,
            border_color=STROKE,
            hover_color="#2A1C1C",
            text_color=TEXT,
            command=self.on_force_kill,
        )
        self.kill_btn.pack(side="left", fill="x", expand=True)

        # —— Log ——
        log_card = self._card(self.shell)
        log_card.pack(fill="both", expand=True)
        log_in = ctk.CTkFrame(log_card, fg_color="transparent")
        log_in.pack(fill="both", expand=True, padx=14, pady=12)

        log_head = ctk.CTkFrame(log_in, fg_color="transparent")
        log_head.pack(fill="x")
        ctk.CTkLabel(
            log_head,
            text="Лог",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=14),
            text_color=TEXT,
        ).pack(side="left")

        self.log_mode_var = tk.StringVar(value="Краткий")
        self.log_mode_menu = ctk.CTkSegmentedButton(
            log_head,
            values=["Краткий", "Debug"],
            variable=self.log_mode_var,
            height=28,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=STROKE,
            unselected_hover_color="#364257",
            command=self._on_log_mode,
        )
        self.log_mode_menu.pack(side="left", padx=(14, 0))

        ctk.CTkButton(
            log_head,
            text="🗑  Очистить",
            width=110,
            height=28,
            fg_color="transparent",
            border_width=1,
            border_color=STROKE,
            hover_color="#222836",
            text_color=MUTED,
            command=self.clear_log,
        ).pack(side="right")

        self.log_box = ctk.CTkTextbox(
            log_in,
            fg_color=FIELD,
            border_color=STROKE,
            text_color=TEXT,
            font=ctk.CTkFont(family="Cascadia Mono", size=12),
            activate_scrollbars=True,
            corner_radius=10,
        )
        self.log_box.pack(fill="both", expand=True, pady=(8, 0))
        tb = self.log_box._textbox  # noqa: SLF001
        tb.tag_configure("time", foreground="#C9D3E2")
        tb.tag_configure("info", foreground=INFO_GREEN)
        tb.tag_configure("warn", foreground=WARN)
        tb.tag_configure("error", foreground=DANGER)
        tb.tag_configure("debug", foreground=HINT)
        tb.tag_configure("msg", foreground="#E8EDF5")
        tb.tag_configure("sep", foreground=HINT)
        self.log_box.configure(state="disabled")

        self._apply_settings()
        self.ssid_entry.bind("<FocusOut>", lambda _e: self._persist_settings())
        self.pass_entry.bind("<FocusOut>", lambda _e: self._persist_settings())
        self.link_entry.bind("<FocusOut>", lambda _e: self._persist_settings())
        if self.link_entry.get().strip():
            self.after(450, self.load_servers)

    # —— animations ——
    def _run_intro_animation(self) -> None:
        # soft fade-in by briefly dimming then restoring card borders
        for i, card in enumerate(self._fade_cards):
            delay = 40 + i * 70
            self.after(delay, lambda c=card: self._flash_card(c))
        self.after(80, lambda: self._pulse_button(self.start_btn, cycles=3))

    def _flash_card(self, card: ctk.CTkFrame) -> None:
        try:
            card.configure(border_color=ACCENT)
            self.after(180, lambda: card.configure(border_color=STROKE))
        except Exception:
            pass

    def _pulse_button(self, btn: ctk.CTkButton, cycles: int = 2) -> None:
        if cycles <= 0:
            return

        def restore() -> None:
            try:
                btn.configure(fg_color=ACCENT)
            except Exception:
                pass
            self.after(120, lambda: self._pulse_button(btn, cycles - 1))

        try:
            btn.configure(fg_color=ACCENT_HOVER)
            self.after(120, restore)
        except Exception:
            pass

    def _pulse_status(self) -> None:
        if not self._running:
            self._anim_after = None
            return
        self._pill_pulse = 1 - self._pill_pulse
        color = ACCENT if self._pill_pulse else "#168A58"
        try:
            self.status_pill.configure(fg_color=color)
        except Exception:
            pass
        self._anim_after = self.after(700, self._pulse_status)

    # —— settings ——
    def _apply_settings(self) -> None:
        cfg = load_settings()
        if cfg.get("link"):
            self.link_entry.delete(0, "end")
            self.link_entry.insert(0, str(cfg["link"]))
        ssid = str(cfg.get("ssid") or "Xray_WiFi_Share")
        self.ssid_entry.delete(0, "end")
        self.ssid_entry.insert(0, ssid)
        password = str(cfg.get("password") or "")
        self.pass_entry.delete(0, "end")
        if password:
            self.pass_entry.insert(0, password)
        self._show_password = bool(cfg.get("show_password"))
        self.pass_entry.configure(show="" if self._show_password else "•")
        self.show_pw_btn.configure(text="Скрыть" if self._show_password else "Показать")
        self._saved_adapter = str(cfg.get("adapter") or "")
        self._saved_server = str(cfg.get("selected_server") or "")
        mode = str(cfg.get("log_mode") or "brief")
        self._log_mode = "debug" if mode == "debug" else "brief"
        self.log_mode_var.set("Debug" if self._log_mode == "debug" else "Краткий")

    def _persist_settings(self) -> None:
        save_settings(
            {
                "link": self.link_entry.get().strip(),
                "ssid": self.ssid_entry.get().strip() or "Xray_WiFi_Share",
                "password": self.pass_entry.get(),
                "adapter": self.adapter_var.get(),
                "show_password": self._show_password,
                "selected_server": self.server_var.get() if self._servers else self._saved_server,
                "log_mode": self._log_mode,
            }
        )

    def _on_log_mode(self, value: str) -> None:
        self._log_mode = "debug" if value == "Debug" else "brief"
        self._persist_settings()
        self._ui_log("INFO", f"Режим лога: {'Debug' if self._log_mode == 'debug' else 'краткий'}")

    def _focus_link(self) -> None:
        try:
            self.link_entry.focus_set()
        except Exception:
            pass

    def toggle_password(self) -> None:
        self._show_password = not self._show_password
        self.pass_entry.configure(show="" if self._show_password else "•")
        self.show_pw_btn.configure(text="Скрыть" if self._show_password else "Показать")
        self._persist_settings()

    def paste_link(self) -> None:
        text = self._clipboard_text()
        if not text:
            messagebox.showinfo(
                "Буфер",
                "Не удалось прочитать буфер.\nСкопируй ссылку ещё раз и нажми «Вставить».",
            )
            return
        self.link_entry.delete(0, "end")
        self.link_entry.insert(0, text.strip())
        self._persist_settings()
        self._flash_card(self._fade_cards[0] if self._fade_cards else self.shell)
        self.load_servers()

    def _on_ctrl_v(self, _event=None):
        text = self._clipboard_text()
        if text:
            try:
                if self.link_entry.selection_present():
                    self.link_entry.delete("sel.first", "sel.last")
            except Exception:
                pass
            self.link_entry.insert("insert", text)
            self._persist_settings()
            self.after(50, self.load_servers)
        return "break"

    def load_servers(self) -> None:
        text = self.link_entry.get().strip()
        if not text:
            self._set_servers([], hint="Вставь ссылку, URL подписки или Raw JSON")
            return

        self._parse_token += 1
        token = self._parse_token
        self.load_btn.configure(state="disabled", text="…")
        self.server_hint.configure(text="Загрузка списка серверов…", text_color=HINT)
        self._ui_log("INFO", "Загрузка подписки…")

        def worker() -> None:
            err = ""
            servers: list[ParsedLink] = []
            try:
                servers = parse_servers(text)
            except Exception as exc:
                err = str(exc)

            def done() -> None:
                if token != self._parse_token:
                    return
                self.load_btn.configure(state="normal", text="Загрузить")
                if err:
                    self._set_servers([], hint=f"Не разобрать: {err}")
                    self._ui_log("ERROR", f"Подписка: {err}")
                    return
                self._set_servers(servers)
                self._ui_log("INFO", f"Серверов: {len(servers)}")

            try:
                self.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _server_label(self, item: ParsedLink, index: int) -> str:
        proto = item.kind.upper().replace("HYSTERIA2", "HY2").replace("HYSTERIA", "HY2")
        if proto in ("SS", "SHADOWSOCKS"):
            proto = "SS"
        remark = item.remark or f"{item.server_host}:{item.server_port}"
        return f"{index + 1}. [{proto}] {remark}"

    def _set_servers(self, servers: list[ParsedLink], hint: str | None = None) -> None:
        self._servers = list(servers)
        if not servers:
            empty = "— нет серверов —"
            self.server_menu.configure(values=[empty])
            self.server_var.set(empty)
            self.server_hint.configure(text=hint or "Серверов не найдено", text_color=WARN)
            return

        labels = [self._server_label(s, i) for i, s in enumerate(servers)]
        self.server_menu.configure(values=labels)
        chosen = labels[0]
        saved = self._saved_server
        if saved:
            for lab in labels:
                if saved == lab or saved in lab or (saved and saved in lab.split("] ", 1)[-1]):
                    chosen = lab
                    break
        self.server_var.set(chosen)
        self.server_hint.configure(text=hint or f"Доступно серверов: {len(servers)}", text_color=ACCENT)
        self._on_server_changed()

    def _on_server_changed(self) -> None:
        self._saved_server = self.server_var.get()
        self._persist_settings()

    def _selected_server(self) -> ParsedLink | None:
        if not self._servers:
            return None
        label = self.server_var.get()
        for i, item in enumerate(self._servers):
            if self._server_label(item, i) == label:
                return item
        return self._servers[0]

    def _clipboard_text(self) -> str:
        try:
            return self.clipboard_get()
        except Exception:
            try:
                return self.tk.call("clipboard", "get")
            except Exception:
                return ""

    def refresh_adapters(self) -> None:
        try:
            adapters = self.engine.wifi_adapters()
        except Exception as exc:
            self._ui_log("WARN", f"Адаптеры: {exc}")
            adapters = []
        self._adapters_raw = adapters
        values = [a.description or a.name for a in adapters] or ["Wi‑Fi адаптер не найден"]
        self.adapter_menu.configure(values=values)

        preferred = self._saved_adapter or ""
        chosen = values[0]
        if preferred:
            for item in values:
                if preferred == item or preferred in item or item in preferred:
                    chosen = item
                    break
            for a in adapters:
                label = a.description or a.name
                old = f"{a.name}  ·  {a.description}"
                if preferred in (old, a.name, a.description, label):
                    chosen = label
                    break
        self.adapter_var.set(chosen)
        self._on_adapter_changed()

    def _on_adapter_changed(self) -> None:
        label = self.adapter_var.get()
        if "не найден" in label.lower():
            self.adapter_hint.configure(text="⚠  Подключи USB Wi‑Fi адаптер", text_color=WARN)
            return
        supported = self._softap_supported()
        if supported is True:
            self.adapter_hint.configure(
                text="✓  Адаптер поддерживает режим точки доступа",
                text_color=ACCENT,
            )
        elif supported is False:
            self.adapter_hint.configure(
                text="⚠  SoftAP может быть недоступен на этом адаптере",
                text_color=WARN,
            )
        else:
            self.adapter_hint.configure(text="?  Не удалось проверить SoftAP", text_color=HINT)
        self._persist_settings()

    def _softap_supported(self) -> bool | None:
        try:
            r = run_cmd(["netsh", "wlan", "show", "drivers"])
            text = f"{r.stdout}\n{r.stderr}".lower().replace("ё", "е")
            if "hosted network supported  : yes" in text or "поддержка размещенной сети  : да" in text:
                return True
            if "hosted network supported  : no" in text or "поддержка размещенной сети  : нет" in text:
                return False
            if "размещенной сети" in text and "да" in text:
                return True
            if "hosted network supported" in text and "yes" in text:
                return True
        except Exception:
            return None
        return None

    def _selected_adapter_name(self) -> str:
        label = self.adapter_var.get()
        for a in self._adapters_raw:
            if (a.description or a.name) == label or a.name == label:
                return a.name
        return label.split("·", 1)[0].strip()

    def on_start(self) -> None:
        if self._busy:
            return
        link = self.link_entry.get().strip()
        adapter_label = self.adapter_var.get()
        ssid = self.ssid_entry.get().strip()
        password = self.pass_entry.get().strip()
        if not link:
            messagebox.showwarning("Ссылка", "Вставь vless/hy2, URL подписки или Raw JSON")
            return
        if not self._servers:
            messagebox.showwarning("Сервер", "Сначала нажми «Загрузить»")
            return
        server = self._selected_server()
        if not server:
            messagebox.showwarning("Сервер", "Выбери сервер из списка")
            return
        if "не найден" in adapter_label.lower():
            messagebox.showwarning("Адаптер", "Подключи USB Wi‑Fi и обнови список адаптеров")
            return
        if len(password) < 8:
            messagebox.showwarning("Пароль", "Пароль Wi‑Fi минимум 8 символов")
            return
        if not is_admin():
            messagebox.showerror("Админ", "Перезапусти приложение от имени администратора")
            return

        wifi_name = self._selected_adapter_name()
        self._persist_settings()
        self._last_error_shown = ""
        self._ui_log("INFO", "——— новый запуск ———")
        self._ui_log("INFO", f"Сервер: {server.remark}")
        self._ui_log("INFO", "Запуск сетевого сервиса…")
        self._set_busy(True)
        self._pulse_button(self.start_btn, cycles=2)

        job_link = link if len(link) < 4000 and not link.lstrip().startswith(("{", "[")) else ""
        self._write_job(
            {
                "action": "start",
                "link": job_link,
                "wifi_adapter": wifi_name,
                "ssid": ssid,
                "password": password,
                "outbound": server.outbound,
                "remark": server.remark,
                "kind": server.kind,
                "server_host": server.server_host,
                "server_port": server.server_port,
            }
        )
        if not self._spawn_elevated_job():
            self._set_busy(False)
            return
        self._set_status("Запуск", WARN)
        self._start_polling()

    def on_stop(self) -> None:
        if self._busy and not self._running:
            return
        self._ui_log("INFO", "Остановка…")
        self._set_busy(True)
        try:
            (WORK / "stop.flag").write_text("1", encoding="utf-8")
        except Exception:
            pass
        self._write_job({"action": "stop"})
        self._spawn_elevated_job()
        self._set_status("Остановка", WARN)
        self._start_polling()

    def on_force_kill(self) -> None:
        if not messagebox.askyesno(
            "Принудительно завершить",
            "Остановить раздачу, закрыть служебные процессы и выключить Wi‑Fi точку?",
        ):
            return
        self._ui_log("WARN", "Принудительное завершение…")
        self._set_busy(True)
        try:
            (WORK / "kill.flag").write_text("1", encoding="utf-8")
        except Exception:
            pass
        self._write_job({"action": "kill"})
        self._spawn_elevated_job()
        self._set_status("Kill", DANGER)
        self._start_polling()

    def clear_log(self) -> None:
        ensure_dirs()
        try:
            LOG_FILE.write_text("", encoding="utf-8")
        except Exception:
            pass
        self._log_offset = 0
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _write_job(self, payload: dict) -> None:
        ensure_dirs()
        JOB_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _spawn_elevated_job(self) -> bool:
        if is_admin():
            def worker() -> None:
                from .elevated_job import run_job

                run_job()

            threading.Thread(target=worker, daemon=True).start()
            return True

        import ctypes

        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = "--job"
        else:
            exe = sys.executable
            params = f'"{Path(__file__).resolve().parent.parent / "main.py"}" --job'

        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(ROOT), 1)
        if rc <= 32:
            messagebox.showerror("UAC", "Не удалось запросить права администратора")
            return False
        return True

    def _start_polling(self) -> None:
        if self._poll_after:
            try:
                self.after_cancel(self._poll_after)
            except Exception:
                pass
        self._poll_status()

    def _poll_status(self) -> None:
        self._drain_log_file()
        state = ""
        message = ""
        if STATUS_FILE.exists():
            try:
                data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
                state = str(data.get("state") or "")
                message = str(data.get("message") or "")
            except Exception:
                pass

        if state and state != self._last_status:
            self._last_status = state
            if state == "running":
                self._running = True
                self._set_status("Активно", ACCENT)
                self.start_btn.configure(state="disabled")
                self.stop_btn.configure(state="normal")
                self._busy = False
                if not self._anim_after:
                    self._pulse_status()
            elif state == "stopped":
                self._running = False
                self._set_status("Остановлено", PILL_IDLE)
                self._set_busy(False)
                self.start_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
            elif state == "error":
                self._running = False
                self._set_status("Ошибка", DANGER)
                self._set_busy(False)
                self.start_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
                if message and message != self._last_error_shown:
                    self._last_error_shown = message
                    self._ui_log("ERROR", message)
            elif state in ("starting", "stopping", "killing"):
                self._busy = True
                label = {
                    "starting": ("Запуск", WARN),
                    "stopping": ("Остановка", WARN),
                    "killing": ("Kill", DANGER),
                }[state]
                self._set_status(label[0], label[1])

        self._poll_after = self.after(400, self._poll_status)

    def _follow_log_from_end(self) -> None:
        ensure_dirs()
        self._log_offset = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0

    def _drain_log_file(self) -> None:
        if not LOG_FILE.exists():
            return
        try:
            data = LOG_FILE.read_bytes()
        except Exception:
            return
        if len(data) < self._log_offset:
            self._log_offset = 0
        chunk = data[self._log_offset :]
        if not chunk:
            return
        self._log_offset = len(data)
        text = chunk.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip():
                self._ingest_engine_line(line)

    def _ingest_engine_line(self, message: str) -> None:
        level, text = self._classify_engine_line(message)
        if self._log_mode == "brief" and level == "DEBUG":
            return
        if self._log_mode == "brief":
            brief = self._briefify(text)
            if brief is None:
                return
            text = brief
        self._ui_log(level if level != "DEBUG" else "DEBUG", text)

    def _classify_engine_line(self, message: str) -> tuple[str, str]:
        msg = message.strip()
        low = msg.lower()
        if msg.startswith("[xray]") or msg.startswith("[tun2socks]"):
            body = msg.split("]", 1)[-1].strip()
            if any(x in low for x in ("error", "failed", "fatal", "ошиб")):
                return "ERROR", body or msg
            if "warn" in low:
                return "WARN", body or msg
            return "DEBUG", body or msg
        if any(x in low for x in ("ошиб", "error", "failed", "fatal")):
            return "ERROR", msg
        if "warn" in low or "⚠" in msg:
            return "WARN", msg
        return "INFO", msg

    def _briefify(self, text: str) -> str | None:
        t = text.strip()
        low = t.lower()
        # drop noisy separators already handled
        if re.search(r"физический uplink", low):
            return "Uplink готов"
        if re.search(r"прямой маршрут", low):
            return "Маршрут к серверу готов"
        if re.search(r"xray socks", low):
            return "Xray core запущен"
        if re.search(r"tun адаптер", low):
            return "TUN инициализирован"
        if re.search(r"tun готов", low):
            return "TUN готов к раздаче"
        if re.search(r"wi.?fi точка", low):
            return re.sub(r".*SSID=", "Точка доступа создана · SSID=", t, flags=re.I)
        if re.search(r"раздача включена", low):
            return "Раздача включена"
        if re.search(r"готово\.|ожидание подключений", low):
            return "Ожидание подключений…"
        if re.search(r"остановлено", low):
            return "Остановлено"
        if re.search(r"force kill", low):
            return "Принудительно завершено"
        if re.search(r"очистк|^cleanup:", low):
            return None
        if re.search(r"протокол:|сервер:|запуск", low):
            return t
        if len(t) <= 90 and not t.startswith("["):
            return t
        return None

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="disabled")
        elif not self._running:
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")

    def _set_status(self, text: str, color: str) -> None:
        fg = "#0B1A12" if color in (ACCENT, PILL_IDLE, WARN) else TEXT
        if color == DANGER:
            fg = "#1A0808"
        self.status_pill.configure(text=f"  {text}  ", fg_color=color, text_color=fg)

    def _ui_log(self, level: str, message: str) -> None:
        def ui() -> None:
            self._append_log_ui(level, message)

        try:
            self.after(0, ui)
        except Exception:
            pass

    def _append_log(self, message: str) -> None:
        # compatibility for older call sites
        level, text = self._classify_engine_line(message)
        self._ui_log(level if level != "DEBUG" or self._log_mode == "debug" else "INFO", text)

    def _append_log_ui(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        lvl = (level or "INFO").upper()
        if lvl == "DEBUG" and self._log_mode != "debug":
            return
        if "——" in message:
            tag_level = "sep"
            lvl_disp = "INFO"
        else:
            tag_level = {
                "INFO": "info",
                "WARN": "warn",
                "WARNING": "warn",
                "ERROR": "error",
                "DEBUG": "debug",
            }.get(lvl, "info")
            lvl_disp = "WARN" if lvl in ("WARN", "WARNING") else lvl

        self.log_box.configure(state="normal")
        self.log_box.insert("end", stamp, "time")
        self.log_box.insert("end", " ")
        self.log_box.insert("end", f"{lvl_disp:<5}", tag_level)
        self.log_box.insert("end", " ")
        self.log_box.insert("end", message + "\n", "msg" if tag_level != "sep" else "sep")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def on_close(self) -> None:
        self._persist_settings()
        if self._running:
            if not messagebox.askyesno("Выход", "Раздача активна. Остановить и выйти?"):
                return
            try:
                (WORK / "stop.flag").write_text("1", encoding="utf-8")
            except Exception:
                pass
            self._write_job({"action": "stop"})
            self._spawn_elevated_job()
            time.sleep(0.6)
        self.destroy()


def run_app() -> None:
    app = App()
    app.mainloop()
