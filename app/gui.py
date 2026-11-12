from __future__ import annotations

import json
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


BG = "#12151C"
CARD = "#1B212C"
FIELD = "#10151D"
STROKE = "#2C3545"
TEXT = "#E8EDF5"
MUTED = "#8B95A8"
ACCENT = "#1FAE70"
ACCENT_HOVER = "#1A9660"
DANGER = "#C44B4B"
WARN = "#D3A23A"
PILL_IDLE = "#C9A227"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("Xray Wi‑Fi Share")
        self.geometry("920x820")
        self.minsize(820, 720)
        self.configure(fg_color=BG)

        self.engine = Engine()
        self._busy = False
        self._running = False
        self._show_password = False
        self._log_offset = 0
        self._poll_after: str | None = None
        self._last_status = ""
        self._last_error_shown = ""
        self._adapters_raw: list = []
        self._saved_adapter = ""
        self._servers: list[ParsedLink] = []
        self._saved_server = ""
        self._parse_token = 0

        ensure_dirs()
        self._build()
        self.refresh_adapters()
        self._follow_log_from_end()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self._focus_link)
        self.after(300, self._start_polling)

    def _card(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, border_width=1, border_color=STROKE)

    def _build(self) -> None:
        shell = ctk.CTkFrame(self, fg_color=BG)
        shell.pack(fill="both", expand=True, padx=22, pady=18)

        # —— Header ——
        head = ctk.CTkFrame(shell, fg_color="transparent")
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

        # —— Card: connection settings ——
        conn = self._card(shell)
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

        # left: link + servers
        left = ctk.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        ctk.CTkLabel(left, text="Ссылка / URL подписки / Raw JSON", text_color=MUTED).pack(anchor="w")

        link_row = ctk.CTkFrame(left, fg_color="transparent")
        link_row.pack(fill="x", pady=(6, 0))
        self.link_entry = ctk.CTkEntry(
            link_row,
            height=40,
            fg_color=FIELD,
            border_color=STROKE,
            text_color=TEXT,
            placeholder_text="vless://…  ·  https://sub…  ·  {\"resolvedProxyConfigs\":…}",
            font=ctk.CTkFont(family="Cascadia Mono", size=12),
        )
        self.link_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            link_row,
            text="⧉  Вставить",
            width=100,
            height=40,
            fg_color=STROKE,
            hover_color="#364257",
            command=self.paste_link,
        ).pack(side="left", padx=(8, 0))
        self.load_btn = ctk.CTkButton(
            link_row,
            text="Загрузить",
            width=100,
            height=40,
            fg_color=STROKE,
            hover_color="#364257",
            command=self.load_servers,
        )
        self.load_btn.pack(side="left", padx=(8, 0))

        srv_row = ctk.CTkFrame(left, fg_color="transparent")
        srv_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(srv_row, text="Сервер", text_color=MUTED).pack(anchor="w")
        self.server_var = tk.StringVar(value="— загрузи ссылку / JSON —")
        self.server_menu = ctk.CTkOptionMenu(
            srv_row,
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
            srv_row,
            text="Список серверов появится после загрузки подписки",
            text_color="#6F7A8C",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.server_hint.pack(fill="x", pady=(6, 0))

        self.link_entry.bind("<Control-v>", self._on_ctrl_v)
        self.link_entry.bind("<Control-V>", self._on_ctrl_v)
        self.link_entry.bind("<<Paste>>", self._on_ctrl_v)
        self.link_entry.bind("<Shift-Insert>", self._on_ctrl_v)
        self.link_entry.bind("<Return>", lambda _e: self.load_servers())

        # right: adapter
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
            height=40,
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
            text_color="#6F7A8C",
            font=ctk.CTkFont(size=12),
            anchor="w",
        )
        self.adapter_hint.pack(fill="x", pady=(8, 0))

        # —— Card: hotspot params ——
        ap = self._card(shell)
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
        self.ssid_entry = ctk.CTkEntry(ssid_f, height=40, fg_color=FIELD, border_color=STROKE)
        self.ssid_entry.pack(fill="x", pady=(6, 0))

        pw_f = ctk.CTkFrame(ap_grid, fg_color="transparent")
        pw_f.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(pw_f, text="Пароль (минимум 8 символов)", text_color=MUTED).pack(anchor="w")
        pw_row = ctk.CTkFrame(pw_f, fg_color="transparent")
        pw_row.pack(fill="x", pady=(6, 0))
        self.pass_entry = ctk.CTkEntry(pw_row, height=40, fg_color=FIELD, border_color=STROKE, show="•")
        self.pass_entry.pack(side="left", fill="x", expand=True)
        self.show_pw_btn = ctk.CTkButton(
            pw_row,
            text="Показать",
            width=100,
            height=40,
            fg_color=STROKE,
            hover_color="#364257",
            command=self.toggle_password,
        )
        self.show_pw_btn.pack(side="left", padx=(8, 0))

        # —— Actions ——
        actions = ctk.CTkFrame(shell, fg_color="transparent")
        actions.pack(fill="x", pady=(2, 10))

        self.start_btn = ctk.CTkButton(
            actions,
            text="⏻   Запустить",
            height=46,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=14),
            command=self.on_start,
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            actions,
            text="■   Остановить",
            height=46,
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
            height=46,
            fg_color="transparent",
            border_width=1,
            border_color=STROKE,
            hover_color="#2A1C1C",
            text_color=TEXT,
            command=self.on_force_kill,
        )
        self.kill_btn.pack(side="left", fill="x", expand=True)

        # —— Log ——
        log_card = self._card(shell)
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
            text_color="#C9D3E2",
            font=ctk.CTkFont(family="Cascadia Mono", size=12),
            activate_scrollbars=True,
        )
        self.log_box.pack(fill="both", expand=True, pady=(8, 0))
        tb = self.log_box._textbox  # noqa: SLF001
        tb.tag_configure("muted", foreground="#6F7A8C")
        tb.tag_configure("info", foreground=ACCENT)
        tb.tag_configure("warn", foreground=WARN)
        tb.tag_configure("error", foreground=DANGER)
        tb.tag_configure("plain", foreground="#C9D3E2")
        self.log_box.configure(state="disabled")

        self._apply_settings()
        self.ssid_entry.bind("<FocusOut>", lambda _e: self._persist_settings())
        self.pass_entry.bind("<FocusOut>", lambda _e: self._persist_settings())
        self.link_entry.bind("<FocusOut>", lambda _e: self._persist_settings())
        if self.link_entry.get().strip():
            self.after(500, self.load_servers)

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

    def _persist_settings(self) -> None:
        save_settings(
            {
                "link": self.link_entry.get().strip(),
                "ssid": self.ssid_entry.get().strip() or "Xray_WiFi_Share",
                "password": self.pass_entry.get(),
                "adapter": self.adapter_var.get(),
                "show_password": self._show_password,
                "selected_server": self.server_var.get() if self._servers else self._saved_server,
            }
        )

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
        self.server_hint.configure(text="Загрузка списка серверов…", text_color="#6F7A8C")

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
                    self._append_log(f"Подписка: {err}")
                    return
                self._set_servers(servers)

            try:
                self.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _server_label(self, item: ParsedLink, index: int) -> str:
        proto = item.kind.upper().replace("HYSTERIA2", "HY2").replace("HYSTERIA", "HY2")
        remark = item.remark or f"{item.server_host}:{item.server_port}"
        # keep unique labels for OptionMenu
        return f"{index + 1}. [{proto}] {remark}"

    def _set_servers(self, servers: list[ParsedLink], hint: str | None = None) -> None:
        self._servers = list(servers)
        if not servers:
            empty = "— нет серверов —"
            self.server_menu.configure(values=[empty])
            self.server_var.set(empty)
            self.server_hint.configure(
                text=hint or "Серверов не найдено",
                text_color=WARN,
            )
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
        self.server_hint.configure(
            text=hint or f"Доступно серверов: {len(servers)}",
            text_color=ACCENT,
        )
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
            self._append_log(f"Адаптеры: {exc}")
            adapters = []
        self._adapters_raw = adapters
        values = []
        for a in adapters:
            # cleaner label like the mockup
            values.append(a.description or a.name)
        if not values:
            values = ["Wi‑Fi адаптер не найден"]
        self.adapter_menu.configure(values=values)

        preferred = self._saved_adapter or ""
        chosen = values[0]
        if preferred:
            for item in values:
                if preferred == item or preferred in item or item in preferred:
                    chosen = item
                    break
            # also match old "name · desc" format
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
            self.adapter_hint.configure(
                text="⚠  Подключи USB Wi‑Fi адаптер",
                text_color=WARN,
            )
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
            self.adapter_hint.configure(
                text="?  Не удалось проверить SoftAP",
                text_color="#6F7A8C",
            )
        self._persist_settings()

    def _softap_supported(self) -> bool | None:
        try:
            r = run_cmd(["netsh", "wlan", "show", "drivers"])
            text = f"{r.stdout}\n{r.stderr}".lower()
            if "hosted network supported  : yes" in text or "поддержка размещенной сети  : да" in text.replace(
                "ё", "е"
            ):
                return True
            if "hosted network supported  : no" in text or "поддержка размещенной сети  : нет" in text.replace(
                "ё", "е"
            ):
                return False
            # looser match
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
            messagebox.showwarning(
                "Сервер",
                "Сначала нажми «Загрузить», чтобы получить список серверов",
            )
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

        wifi_name = self._selected_adapter_name()
        self._persist_settings()
        self._last_error_shown = ""
        self._append_log("——— новый запуск ———")
        self._append_log(f"Сервер: {server.remark} ({server.kind})")
        self._append_log("Запрос прав администратора для сетевого сервиса...")
        self._set_busy(True)
        # Prefer compact source for job: URL/link if short; omit huge JSON body
        job_link = link if len(link) < 4000 and not link.lstrip().startswith("{") else ""
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
        self._append_log("Остановка...")
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
        self._append_log("Принудительное завершение...")
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
        import ctypes

        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = "--job"
        else:
            exe = sys.executable
            params = f'"{Path(__file__).resolve().parent.parent / "main.py"}" --job'

        if is_admin():
            def worker() -> None:
                from .elevated_job import run_job

                run_job()

            threading.Thread(target=worker, daemon=True).start()
            return True

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
                    self._append_log(f"Ошибка: {message}")
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
        if LOG_FILE.exists():
            self._log_offset = LOG_FILE.stat().st_size
        else:
            self._log_offset = 0

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
                self._append_log_ui(line)

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

    def _line_tag(self, message: str) -> str:
        low = message.lower()
        if "ошиб" in low or "error" in low or "failed" in low:
            return "error"
        if "warn" in low or "⚠" in message:
            return "warn"
        if "info" in low or "готово" in low or "запущен" in low or "✓" in message:
            return "info"
        if message.startswith("———") or message.startswith("["):
            return "muted"
        return "plain"

    def _append_log_ui(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        # keep original if already looks timestamped from engine detail lines
        if message.startswith("[") or "——" in message[:4]:
            display = message
        else:
            display = f"{stamp}  {message}"
        tag = self._line_tag(message)
        self.log_box.configure(state="normal")
        self.log_box.insert("end", display + "\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _append_log(self, message: str) -> None:
        def ui() -> None:
            self._append_log_ui(message)

        try:
            self.after(0, ui)
        except Exception:
            pass

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
