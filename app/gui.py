from __future__ import annotations

import json
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from .admin import is_admin
from .engine import Engine
from .paths import JOB_FILE, LOG_FILE, ROOT, STATUS_FILE, WORK, ensure_dirs
from .settings import load_settings, save_settings


BG = "#12151C"
CARD = "#1A1F2B"
FIELD = "#0E1218"
STROKE = "#2A3344"
TEXT = "#E8EDF5"
MUTED = "#8B95A8"
ACCENT = "#2BB673"
ACCENT_HOVER = "#239A61"
DANGER = "#C44B4B"
DANGER_HOVER = "#A63D3D"
WARN = "#D3A23A"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("Xray Wi‑Fi Share")
        self.geometry("820x700")
        self.minsize(740, 620)
        self.configure(fg_color=BG)

        self.engine = Engine()  # only for adapter list in GUI process
        self._busy = False
        self._running = False
        self._show_password = False
        self._log_offset = 0
        self._poll_after: str | None = None
        self._last_status = ""
        self._last_error_shown = ""

        ensure_dirs()
        self._build()
        self.refresh_adapters()
        self._follow_log_from_end()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self._focus_link)
        self.after(300, self._start_polling)

    def _build(self) -> None:
        shell = ctk.CTkFrame(self, fg_color=BG)
        shell.pack(fill="both", expand=True, padx=20, pady=18)

        head = ctk.CTkFrame(shell, fg_color="transparent")
        head.pack(fill="x")

        ctk.CTkLabel(
            head,
            text="Xray Wi‑Fi Share",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=24),
            text_color=TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text="Раздай свой VLESS / Hysteria2 как обычную Wi‑Fi сеть",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=MUTED,
        ).pack(anchor="w", pady=(2, 0))

        self.status_pill = ctk.CTkLabel(
            head,
            text="  остановлено  ",
            font=ctk.CTkFont(size=12),
            text_color="#0B1A12",
            fg_color="#3D4A5C",
            corner_radius=999,
        )
        self.status_pill.pack(anchor="w", pady=(10, 0))

        card = ctk.CTkFrame(shell, fg_color=CARD, corner_radius=16, border_width=1, border_color=STROKE)
        card.pack(fill="x", pady=(14, 10))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=16)

        link_row = ctk.CTkFrame(inner, fg_color="transparent")
        link_row.pack(fill="x")
        ctk.CTkLabel(link_row, text="Ссылка VLESS / Hysteria2", text_color=MUTED).pack(side="left")
        ctk.CTkButton(
            link_row,
            text="Вставить",
            width=96,
            height=28,
            fg_color=STROKE,
            hover_color="#364257",
            command=self.paste_link,
        ).pack(side="right")
        ctk.CTkButton(
            link_row,
            text="Очистить",
            width=88,
            height=28,
            fg_color="transparent",
            border_width=1,
            border_color=STROKE,
            hover_color="#222836",
            command=lambda: self.link_entry.delete(0, "end"),
        ).pack(side="right", padx=(0, 8))

        self.link_entry = ctk.CTkEntry(
            inner,
            height=42,
            fg_color=FIELD,
            border_color=STROKE,
            text_color=TEXT,
            placeholder_text="vless://...  или  hysteria2://...",
            font=ctk.CTkFont(family="Cascadia Mono", size=13),
        )
        self.link_entry.pack(fill="x", pady=(8, 0))
        self.link_entry.bind("<Control-v>", self._on_ctrl_v)
        self.link_entry.bind("<Control-V>", self._on_ctrl_v)
        self.link_entry.bind("<<Paste>>", self._on_ctrl_v)
        self.link_entry.bind("<Shift-Insert>", self._on_ctrl_v)

        grid = ctk.CTkFrame(inner, fg_color="transparent")
        grid.pack(fill="x", pady=(14, 0))
        grid.columnconfigure(0, weight=3)
        grid.columnconfigure(1, weight=2)
        grid.columnconfigure(2, weight=2)

        ad_f = ctk.CTkFrame(grid, fg_color="transparent")
        ad_f.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ctk.CTkLabel(ad_f, text="Wi‑Fi адаптер", text_color=MUTED).pack(anchor="w")
        self.adapter_var = tk.StringVar(value="")
        self.adapter_menu = ctk.CTkOptionMenu(
            ad_f,
            variable=self.adapter_var,
            values=["—"],
            height=38,
            fg_color=FIELD,
            button_color=STROKE,
            button_hover_color="#364257",
            dropdown_fg_color=CARD,
        )
        self.adapter_menu.pack(fill="x", pady=(6, 0))

        ssid_f = ctk.CTkFrame(grid, fg_color="transparent")
        ssid_f.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ctk.CTkLabel(ssid_f, text="Имя сети (SSID)", text_color=MUTED).pack(anchor="w")
        self.ssid_entry = ctk.CTkEntry(ssid_f, height=38, fg_color=FIELD, border_color=STROKE)
        self.ssid_entry.pack(fill="x", pady=(6, 0))

        pw_f = ctk.CTkFrame(grid, fg_color="transparent")
        pw_f.grid(row=0, column=2, sticky="ew")
        ctk.CTkLabel(pw_f, text="Пароль Wi‑Fi", text_color=MUTED).pack(anchor="w")
        pw_row = ctk.CTkFrame(pw_f, fg_color="transparent")
        pw_row.pack(fill="x", pady=(6, 0))
        self.pass_entry = ctk.CTkEntry(pw_row, height=38, fg_color=FIELD, border_color=STROKE, show="•")
        self.pass_entry.pack(side="left", fill="x", expand=True)
        self.show_pw_btn = ctk.CTkButton(
            pw_row,
            text="Показать",
            width=92,
            height=38,
            fg_color=STROKE,
            hover_color="#364257",
            command=self.toggle_password,
        )
        self.show_pw_btn.pack(side="left", padx=(8, 0))

        tools = ctk.CTkFrame(inner, fg_color="transparent")
        tools.pack(fill="x", pady=(14, 0))
        ctk.CTkButton(
            tools,
            text="Обновить адаптеры",
            height=34,
            width=160,
            fg_color=STROKE,
            hover_color="#364257",
            command=self.refresh_adapters,
        ).pack(side="left")

        actions = ctk.CTkFrame(shell, fg_color="transparent")
        actions.pack(fill="x", pady=(4, 10))

        self.start_btn = ctk.CTkButton(
            actions,
            text="Старт",
            height=44,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=14),
            command=self.on_start,
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            actions,
            text="Стоп",
            height=44,
            fg_color="#3A4558",
            hover_color="#2F394A",
            state="disabled",
            command=self.on_stop,
        )
        self.stop_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.kill_btn = ctk.CTkButton(
            actions,
            text="Убить процессы",
            height=44,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            command=self.on_force_kill,
        )
        self.kill_btn.pack(side="left", fill="x", expand=True)

        log_card = ctk.CTkFrame(shell, fg_color=CARD, corner_radius=16, border_width=1, border_color=STROKE)
        log_card.pack(fill="both", expand=True)
        log_inner = ctk.CTkFrame(log_card, fg_color="transparent")
        log_inner.pack(fill="both", expand=True, padx=14, pady=12)
        ctk.CTkLabel(log_inner, text="Лог", text_color=MUTED).pack(anchor="w")
        self.log_box = ctk.CTkTextbox(
            log_inner,
            fg_color=FIELD,
            border_color=STROKE,
            text_color="#C9D3E2",
            font=ctk.CTkFont(family="Cascadia Mono", size=12),
            activate_scrollbars=True,
        )
        self.log_box.pack(fill="both", expand=True, pady=(6, 0))
        self.log_box.configure(state="disabled")

        self._apply_settings()
        self.ssid_entry.bind("<FocusOut>", lambda _e: self._persist_settings())
        self.pass_entry.bind("<FocusOut>", lambda _e: self._persist_settings())
        self.link_entry.bind("<FocusOut>", lambda _e: self._persist_settings())

    def _apply_settings(self) -> None:
        cfg = load_settings()
        if cfg.get("link"):
            self.link_entry.delete(0, "end")
            self.link_entry.insert(0, str(cfg["link"]))
        ssid = str(cfg.get("ssid") or "VPN-Share")
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

    def _persist_settings(self) -> None:
        adapter = self.adapter_var.get()
        save_settings(
            {
                "link": self.link_entry.get().strip(),
                "ssid": self.ssid_entry.get().strip() or "VPN-Share",
                "password": self.pass_entry.get(),
                "adapter": adapter,
                "show_password": self._show_password,
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

    def _on_ctrl_v(self, _event=None):
        text = self._clipboard_text()
        if text:
            try:
                if self.link_entry.selection_present():
                    self.link_entry.delete("sel.first", "sel.last")
            except Exception:
                pass
            self.link_entry.insert("insert", text)
        return "break"

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
        values = [f"{a.name}  ·  {a.description}  [{a.status}]" for a in adapters]
        if not values:
            values = ["Wi‑Fi адаптер не найден"]
        self.adapter_menu.configure(values=values)
        preferred = getattr(self, "_saved_adapter", "") or ""
        chosen = values[0]
        if preferred:
            for item in values:
                if preferred == item or preferred.split("·", 1)[0].strip() in item:
                    chosen = item
                    break
        self.adapter_var.set(chosen)

    def on_start(self) -> None:
        if self._busy:
            return
        link = self.link_entry.get().strip()
        adapter_label = self.adapter_var.get()
        ssid = self.ssid_entry.get().strip()
        password = self.pass_entry.get().strip()
        if not link:
            messagebox.showwarning("Ссылка", "Вставь VLESS или Hysteria2 ключ")
            return
        if "не найден" in adapter_label.lower():
            messagebox.showwarning("Адаптер", "Подключи USB Wi‑Fi и нажми «Обновить адаптеры»")
            return
        if len(password) < 8:
            messagebox.showwarning("Пароль", "Пароль Wi‑Fi минимум 8 символов")
            return

        wifi_name = adapter_label.split("·", 1)[0].strip()
        self._persist_settings()
        self._last_error_shown = ""
        self._append_log("——— новый запуск ———")
        self._append_log("Запрос прав администратора для сетевого сервиса...")
        self._set_busy(True)
        self._write_job(
            {
                "action": "start",
                "link": link,
                "wifi_adapter": wifi_name,
                "ssid": ssid,
                "password": password,
            }
        )
        if not self._spawn_elevated_job():
            self._set_busy(False)
            return
        self._set_status("запуск", WARN)
        self._start_polling()

    def on_stop(self) -> None:
        if self._busy and not self._running:
            return
        self._append_log("Стоп...")
        self._set_busy(True)
        # Prefer soft signal to supervisor; also escalate job as backup
        try:
            (WORK / "stop.flag").write_text("1", encoding="utf-8")
        except Exception:
            pass
        self._write_job({"action": "stop"})
        self._spawn_elevated_job()
        self._set_status("остановка", WARN)
        self._start_polling()

    def on_force_kill(self) -> None:
        if not messagebox.askyesno(
            "Убить процессы",
            "Принудительно остановить раздачу, закрыть служебные процессы и выключить Wi‑Fi точку?",
        ):
            return
        self._append_log("Принудительная остановка...")
        self._set_busy(True)
        try:
            (WORK / "kill.flag").write_text("1", encoding="utf-8")
        except Exception:
            pass
        self._write_job({"action": "kill"})
        self._spawn_elevated_job()
        self._set_status("kill", DANGER)
        self._start_polling()

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

        # If already admin, run inline in a thread (no second UAC)
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
                self._set_status("активно", ACCENT)
                self.start_btn.configure(state="disabled")
                self.stop_btn.configure(state="normal")
                self._busy = False
            elif state == "stopped":
                self._running = False
                self._set_status("остановлено", "#3D4A5C")
                self._set_busy(False)
                self.start_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
            elif state == "error":
                self._running = False
                self._set_status("ошибка", DANGER)
                self._set_busy(False)
                self.start_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
                if message and message != self._last_error_shown:
                    self._last_error_shown = message
                    self._append_log(f"Ошибка: {message}")
            elif state in ("starting", "stopping", "killing"):
                self._busy = True
                label = {
                    "starting": ("запуск", WARN),
                    "stopping": ("остановка", WARN),
                    "killing": ("kill", DANGER),
                }[state]
                self._set_status(label[0], label[1])

        # Always keep polling so logs never "freeze" after stop/error
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
            # file truncated/rotated
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
        fg = "#0B1A12" if color == ACCENT else TEXT
        if color in (DANGER, WARN):
            fg = "#1A1208"
        self.status_pill.configure(text=f"  {text}  ", fg_color=color, text_color=fg)

    def _append_log_ui(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
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
