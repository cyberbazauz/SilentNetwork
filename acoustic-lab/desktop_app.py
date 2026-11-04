"""desktop_app.py — SilentNetwork Acoustic Lab, Windows GUI (SN-016).

Сценарий: Windows → динамик ноутбука → воздух → микрофон Android → WAV →
анализ на Windows. Приложение не управляет телефоном по сети.

При запуске автоматически выбирается системный динамик. PLAY доступен после
создания сигнала и подтверждается перед каждым воспроизведением; системная
громкость не меняется. Флаг `--hardware-test` оставлен для совместимости с
прежними сценариями и больше не блокирует PLAY.

Запуск:
    python desktop_app.py                  # обычный запуск, PLAY после подтверждения
    python desktop_app.py --hardware-test  # совместимый режим (то же поведение)
    python desktop_app.py --smoke-test     # проверить сборку GUI без звука
"""

from __future__ import annotations

import argparse
import math
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Callable

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as _tk_exc:  # pragma: no cover - Windows включает Tk
    tk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    _TK_IMPORT_ERROR: Exception | None = _tk_exc
else:
    _TK_IMPORT_ERROR = None

try:
    from snlab import audio
    from snlab import datalink_desktop as data_desktop
    from snlab import desktop, playback
    from snlab import file_transfer_desktop as file_desktop
except ImportError as _dependency_exc:  # pragma: no cover - зависит от окружения
    audio = None  # type: ignore[assignment]
    data_desktop = None  # type: ignore[assignment]
    desktop = None  # type: ignore[assignment]
    playback = None  # type: ignore[assignment]
    file_desktop = None  # type: ignore[assignment]
    _DEPENDENCY_ERROR: Exception | None = _dependency_exc
else:
    _DEPENDENCY_ERROR = None

TOOL_NAME = "desktop_app.py"
TASK_ID = "SN-016"
WINDOW_TITLE = "SilentNetwork — Акустическая лаборатория"

# Единая светлая палитра SilentNetwork (общая с Android-приложением).
COLOR_BG = "#F4F6F9"
COLOR_CARD = "#FFFFFF"
COLOR_BORDER = "#DCE2EA"
COLOR_TEXT = "#1F2937"
COLOR_MUTED = "#6B7280"
COLOR_ACCENT = "#2563EB"
COLOR_ACCENT_HOVER = "#1D4ED8"
COLOR_SUCCESS = "#15803D"
COLOR_WARNING = "#B45309"
COLOR_ERROR = "#B91C1C"
COLOR_NEUTRAL_BG = "#F1F4F9"


def prepare_tcl_environment() -> list[str]:
    """Подсказать Tk пути к Tcl/Tk, если Python установлен из Microsoft Store.

    Store-сборка Python 3.12 не находит `tcl8.6`/`tk8.6` автоматически, из-за
    чего `tk.Tk()` падает на `ttk/fonts.tcl`. Переменные задаются только если
    они не заданы пользователем и каталоги реально существуют. Возвращает
    список выполненных подстановок для диагностики.
    """
    base = Path(sys.base_prefix) / "tcl"
    applied: list[str] = []
    for variable, directory in (
        ("TCL_LIBRARY", base / "tcl8.6"),
        ("TK_LIBRARY", base / "tk8.6"),
    ):
        if os.environ.get(variable) or not directory.is_dir():
            continue
        os.environ[variable] = str(directory)
        applied.append(f"{variable}={directory}")
    return applied


def enable_high_dpi() -> None:
    """Включить резкую отрисовку Tkinter на Windows.

    Без этого Windows растягивает окно как растровую картинку: шрифты и
    линии выглядят размытыми. Вызывать до создания ``tk.Tk()``.
    """

    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            # PROCESS_PER_MONITOR_DPI_AWARE_V2 (Windows 10 1703+).
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:  # noqa: BLE001 - более старые версии Windows
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:  # noqa: BLE001
                ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - DPI не критичен для работы
        pass


def configure_dpi_scaling(root: Any) -> float:
    """Согласовать размеры Tk со Scaling Windows. Вернуть масштаб к 96 DPI."""

    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:  # noqa: BLE001
        return 1.0
    if dpi <= 0:
        return 1.0
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:  # noqa: BLE001
        pass
    return max(1.0, dpi / 96.0)


def _configure_style(root: Any) -> None:
    """Спокойная светлая тема вместо стандартного серого Tkinter."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:  # pragma: no cover - зависит от платформы
        pass

    style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT,
                    font=("Segoe UI", 10))
    style.configure("TFrame", background=COLOR_BG)
    style.configure("Card.TFrame", background=COLOR_CARD)

    style.configure("Title.TLabel", background=COLOR_BG, foreground=COLOR_TEXT,
                    font=("Segoe UI", 20, "bold"))
    style.configure("Subtitle.TLabel", background=COLOR_BG, foreground=COLOR_MUTED,
                    font=("Segoe UI", 10))
    style.configure("Section.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT,
                    font=("Segoe UI", 13, "bold"))
    style.configure("Card.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT)
    style.configure("CardMuted.TLabel", background=COLOR_CARD, foreground=COLOR_MUTED)
    style.configure("Value.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT,
                    font=("Segoe UI", 11, "bold"))
    style.configure("Field.TLabel", background=COLOR_CARD, foreground=COLOR_MUTED,
                    font=("Segoe UI", 9))

    style.configure("TButton", padding=(10, 6), font=("Segoe UI", 10))
    style.configure("Accent.TButton", background=COLOR_ACCENT, foreground="#FFFFFF",
                    padding=(12, 8), font=("Segoe UI", 10, "bold"), borderwidth=0)
    style.map(
        "Accent.TButton",
        background=[("active", COLOR_ACCENT_HOVER), ("disabled", "#C3CEDF")],
        foreground=[("disabled", "#F1F4F9")],
    )
    style.configure("Danger.TButton", background=COLOR_ERROR, foreground="#FFFFFF",
                    padding=(12, 8), font=("Segoe UI", 10, "bold"), borderwidth=0)
    style.map(
        "Danger.TButton",
        background=[("active", "#991B1B"), ("disabled", "#E3B7B7")],
        foreground=[("disabled", "#FDF2F2")],
    )
    style.configure("Secondary.TButton", background="#EEF2F7", foreground=COLOR_TEXT,
                    padding=(10, 6), borderwidth=0)
    style.map(
        "Secondary.TButton",
        background=[("active", "#E2E8F0"), ("disabled", "#F3F5F8")],
        foreground=[("disabled", "#A0AAB8")],
    )
    style.configure("Link.TButton", background=COLOR_CARD, foreground=COLOR_ACCENT,
                    padding=(0, 4), borderwidth=0, font=("Segoe UI", 10, "bold"))
    style.map("Link.TButton", background=[("active", COLOR_CARD)],
              foreground=[("active", COLOR_ACCENT_HOVER)])

    style.configure("TEntry", fieldbackground="#FFFFFF", foreground=COLOR_TEXT,
                    bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER,
                    darkcolor=COLOR_BORDER, padding=4)
    style.configure("TCombobox", fieldbackground="#FFFFFF", background="#FFFFFF",
                    foreground=COLOR_TEXT, bordercolor=COLOR_BORDER,
                    arrowcolor=COLOR_MUTED, padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")],
              foreground=[("readonly", COLOR_TEXT)])

    style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
                    foreground=COLOR_TEXT, rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background="#F0F3F8", foreground=COLOR_MUTED,
                    font=("Segoe UI", 9, "bold"), borderwidth=0, padding=4)
    style.map("Treeview", background=[("selected", "#DBEAFE")],
              foreground=[("selected", COLOR_TEXT)])

    style.configure(
        "Vertical.TScrollbar",
        background="#C7D2E0",
        troughcolor=COLOR_BG,
        bordercolor=COLOR_BG,
        arrowcolor=COLOR_MUTED,
        relief="flat",
        arrowsize=12,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", "#AEBCCD"), ("pressed", "#9AAABF")],
    )

    style.configure(
        "TNotebook",
        background=COLOR_BG,
        borderwidth=0,
        tabmargins=(0, 0, 0, 10),
    )
    style.configure(
        "TNotebook.Tab",
        background="#E7ECF3",
        foreground=COLOR_MUTED,
        padding=(20, 9),
        borderwidth=0,
        font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLOR_CARD), ("active", "#EEF2F8")],
        foreground=[("selected", COLOR_ACCENT), ("active", COLOR_TEXT)],
        lightcolor=[("selected", COLOR_CARD)],
        darkcolor=[("selected", COLOR_CARD)],
        bordercolor=[("selected", COLOR_CARD)],
    )


class DesktopApp:
    """Тонкая Tkinter-оболочка над `snlab.desktop`."""

    def __init__(
        self,
        root: Any,
        *,
        hardware_test: bool = True,
        results_dir: str | Path | None = None,
        engine: Any | None = None,
        device_lister: Callable[[], list[Any]] | None = None,
        input_device_lister: Callable[[], list[Any]] | None = None,
        recorder: Callable[..., Any] | None = None,
        confirm_playback: Callable[[Any, Any], bool] | None = None,
        confirm_data_playback: Callable[[Any, Any], bool] | None = None,
        confirm_file_playback: Callable[[Any, Any], bool] | None = None,
        analyzer: Callable[..., Any] | None = None,
    ) -> None:
        if desktop is None or playback is None:
            raise RuntimeError(
                "модули snlab.desktop/playback недоступны: "
                f"{_DEPENDENCY_ERROR}. Установите зависимости из requirements.txt"
            )
        self.root = root
        self.hardware_test = bool(hardware_test)
        self.results_dir = (
            Path(results_dir) if results_dir is not None else desktop.default_results_dir()
        )
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.engine = (
            engine
            if engine is not None
            else (playback.SoundDevicePlaybackEngine() if playback is not None else None)
        )
        self.device_lister = device_lister or desktop.list_output_devices
        self.confirm_playback = confirm_playback or self._ask_confirm
        self.analyzer = analyzer or desktop.analyze_phone_wav
        self.input_device_lister = (
            input_device_lister
            if input_device_lister is not None
            else (data_desktop.list_input_devices if data_desktop is not None else (lambda: []))
        )
        self.recorder = recorder or audio.record_ambient
        self.confirm_data_playback = confirm_data_playback or self._ask_confirm_data
        self.confirm_file_playback = confirm_file_playback or self._ask_confirm_file

        self.devices: list[Any] = []
        self.selected_device: Any | None = None
        self.generated: Any | None = None
        self.phone_source: Path | None = None
        self.phone_record: Any | None = None
        self._playing = False
        self._playback_thread: threading.Thread | None = None
        self._playback_queue: queue.Queue[tuple[str, Any]] | None = None
        self._spectrogram_image: Any | None = None
        self._device_panel_open = False
        self._scroll_syncs: list[Callable[[], None]] = []
        self._scroll_canvases: list[tuple[Any, Any]] = []
        self._device_tab: Any | None = None
        self.header_device_label: Any | None = None

        self.data_input_devices: list[Any] = []
        self.data_selected_input: Any | None = None
        self.data_profile_var = tk.StringVar(value="data-audible")
        self.data_text_var = tk.StringVar(value="SILENTNETWORK")
        self.data_duration_var = tk.StringVar(value="12")
        self.data_generated = None
        self.data_received = None
        self.data_playing = False
        self.data_recording = False
        self._data_play_queue: queue.Queue[tuple[str, Any]] | None = None
        self._data_record_queue: queue.Queue[tuple[str, Any]] | None = None

        self.file_input_devices: list[Any] = []
        self.file_selected_input: Any | None = None
        self.file_source_var = tk.StringVar(value="")
        self.file_profile_var = tk.StringVar(value="data-audible")
        self.file_duration_var = tk.StringVar(value="30")
        self.file_generated = None
        self.file_received = None
        self.file_playing = False
        self.file_recording = False
        self._file_play_queue: queue.Queue[tuple[str, Any]] | None = None
        self._file_record_queue: queue.Queue[tuple[str, Any]] | None = None

        self.ui_scale = configure_dpi_scaling(root)
        _configure_style(root)
        self._build_ui()
        self._update_playback_state()
        self.refresh_devices()
        self.refresh_data_inputs()
        self.refresh_file_inputs()
        self._update_data_play_state()
        self._update_file_play_state()
        if not self._device_panel_open:
            self._toggle_device_panel()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI

    def _px(self, value: float) -> int:
        """Логические пиксели → физические для текущего масштаба Windows."""

        return max(1, int(round(value * self.ui_scale)))

    def _build_ui(self) -> None:
        self.root.title(WINDOW_TITLE)
        self.root.configure(background=COLOR_BG)
        scale = self.ui_scale
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = int(min(1220 * scale, screen_w * 0.94))
        height = int(min(900 * scale, screen_h * 0.90))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(
            int(min(940 * scale, screen_w * 0.80)),
            int(min(620 * scale, screen_h * 0.70)),
        )
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._build_header()
        self._build_content()
        self._build_footer()

    # ---------------------------------------------------------------- header

    def _build_header(self) -> None:
        header = tk.Frame(self.root, background=COLOR_CARD)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        titles = tk.Frame(header, background=COLOR_CARD)
        titles.grid(
            row=0, column=0, sticky="w",
            padx=(self._px(22), self._px(16)), pady=(self._px(13), self._px(11)),
        )
        tk.Label(
            titles,
            text="SilentNetwork",
            background=COLOR_CARD,
            foreground=COLOR_TEXT,
            font=("Segoe UI", 18, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            titles,
            text="Акустическая лаборатория · звук вместо сети",
            background=COLOR_CARD,
            foreground=COLOR_MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(anchor="w", pady=(1, 0))

        tools = tk.Frame(header, background=COLOR_CARD)
        tools.grid(
            row=0, column=1, sticky="e",
            padx=(0, self._px(20)), pady=(self._px(13), self._px(11)),
        )
        self.header_device_label = tk.Label(
            tools,
            text="Динамик: поиск…",
            background=COLOR_NEUTRAL_BG,
            foreground=COLOR_TEXT,
            font=("Segoe UI", 9),
            padx=12,
            pady=5,
            anchor="w",
        )
        self.header_device_label.pack(side="left", padx=(0, 8))
        ttk.Button(
            tools,
            text="Настроить звук",
            style="Secondary.TButton",
            command=self._open_device_tab,
        ).pack(side="left")

    def _open_device_tab(self) -> None:
        try:
            if self._device_tab is not None:
                self.tabview.select(self._device_tab)
        except Exception:  # noqa: BLE001 - вкладка может быть ещё не создана
            pass

    # --------------------------------------------------------------- content

    def _build_content(self) -> None:
        shell = ttk.Frame(
            self.root,
            padding=(self._px(16), self._px(12), self._px(16), self._px(6)),
        )
        shell.grid(row=1, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        self.tabview = ttk.Notebook(shell)
        self.tabview.grid(row=0, column=0, sticky="nsew")
        self.tabview.bind(
            "<<NotebookTabChanged>>", lambda _event: self._request_content_sync()
        )

        pad = self._px(14)
        file_tab = ttk.Frame(self.tabview, padding=(pad, pad, pad, pad))
        file_body = self._scrollable(file_tab)
        file_body.columnconfigure(0, weight=1, uniform="file")
        file_body.columnconfigure(1, weight=1, uniform="file")
        file_body.rowconfigure(0, weight=1)
        self._build_file_send_section(file_body)
        self._build_file_receive_section(file_body)
        self.tabview.add(file_tab, text="  Файл  ")

        data_tab = ttk.Frame(self.tabview, padding=(pad, pad, pad, pad))
        data_body = self._scrollable(data_tab)
        data_body.columnconfigure(0, weight=1, uniform="data")
        data_body.columnconfigure(1, weight=1, uniform="data")
        data_body.rowconfigure(0, weight=1)
        self._build_data_send_section(data_body)
        self._build_data_receive_section(data_body)
        self.tabview.add(data_tab, text="  Сообщение  ")

        tone_tab = ttk.Frame(self.tabview, padding=(pad, pad, pad, pad))
        tone_body = self._scrollable(tone_tab)
        tone_body.columnconfigure(0, weight=1, uniform="tone")
        tone_body.columnconfigure(1, weight=1, uniform="tone")
        tone_body.rowconfigure(0, weight=1)
        tone_left = ttk.Frame(tone_body)
        tone_left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        tone_left.columnconfigure(0, weight=1)
        tone_left.rowconfigure(0, weight=1)
        tone_right = ttk.Frame(tone_body)
        tone_right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        tone_right.columnconfigure(0, weight=1)
        tone_right.rowconfigure(0, weight=1)
        self._build_generator_section(tone_left)
        self._build_phone_section(tone_right)
        self.tabview.add(tone_tab, text="  Тон и анализ  ")

        device_tab = ttk.Frame(self.tabview, padding=(pad, pad, pad, pad))
        device_body = self._scrollable(device_tab)
        device_body.columnconfigure(0, weight=1)
        device_body.rowconfigure(0, weight=1)
        self._build_device_section(device_body)
        self.tabview.add(device_tab, text="  Устройства  ")
        self._device_tab = device_tab

        self.root.bind_all("<MouseWheel>", self._on_content_mousewheel, add="+")

    def _scrollable(self, parent: Any) -> Any:
        """Вернуть прокручиваемую область внутри вкладки."""

        holder = ttk.Frame(parent)
        holder.grid(row=0, column=0, sticky="nsew")
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            holder,
            background=COLOR_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        scroll.grid_remove()
        canvas.configure(yscrollcommand=scroll.set)

        inner = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def sync() -> None:
            if not canvas.winfo_exists():
                return
            width = canvas.winfo_width()
            if scroll.winfo_ismapped():
                width -= scroll.winfo_reqwidth()
            width = max(1, width)
            needed = inner.winfo_reqheight()
            canvas.itemconfigure(window, width=width)
            canvas.configure(scrollregion=(0, 0, width, needed))
            if needed > canvas.winfo_height():
                if not scroll.winfo_ismapped():
                    scroll.grid()
            elif scroll.winfo_ismapped():
                scroll.grid_remove()
                canvas.yview_moveto(0.0)

        inner.bind("<Configure>", lambda _event: sync())
        canvas.bind("<Configure>", lambda _event: sync())
        self._scroll_syncs.append(sync)
        self._scroll_canvases.append((canvas, scroll))
        return inner

    def _request_content_sync(self) -> None:
        """Пересчитать прокрутку после изменения высоты содержимого."""

        def _run() -> None:
            if not self.root.winfo_exists():
                return
            self.root.update_idletasks()
            for sync in self._scroll_syncs:
                sync()

        self.root.after_idle(_run)

    def _on_content_mousewheel(self, event: Any) -> None:
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            for canvas, scroll in self._scroll_canvases:
                if widget is canvas and scroll.winfo_ismapped():
                    step = -1 if event.delta > 0 else 1
                    canvas.yview_scroll(step * 3, "units")
                    return
            widget = getattr(widget, "master", None)

    # ---------------------------------------------------------- device card

    def _build_device_section(self, parent: Any) -> None:
        outer, body = self._card(
            parent,
            "Динамик ноутбука",
            "Через это устройство прозвучит сигнал",
        )
        outer.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        body.columnconfigure(0, weight=1)

        self.device_name_label = ttk.Label(
            body, text="Устройство не выбрано", style="Value.TLabel"
        )
        self.device_name_label.grid(row=0, column=0, sticky="w", pady=(10, 0))

        self.device_status = ttk.Label(
            body,
            text="Нажмите «Выбрать устройство», чтобы увидеть список динамиков.",
            style="CardMuted.TLabel",
            wraplength=460,
            justify="left",
        )
        self.device_status.grid(row=1, column=0, sticky="ew", pady=(2, 8))

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=2, column=0, sticky="w")
        self.device_toggle_button = ttk.Button(
            buttons,
            text="Выбрать устройство",
            style="Secondary.TButton",
            command=self._toggle_device_panel,
        )
        self.device_toggle_button.pack(side="left")
        ttk.Button(
            buttons,
            text="Обновить список",
            style="Secondary.TButton",
            command=self.refresh_devices,
        ).pack(side="left", padx=(6, 0))

        self.device_panel = tk.Frame(body, bg=COLOR_CARD)
        self.device_panel.columnconfigure(0, weight=1)

        tree_frame = tk.Frame(self.device_panel, bg=COLOR_CARD)
        tree_frame.grid(row=0, column=0, sticky="nsew", pady=(10, 4))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.device_tree = ttk.Treeview(
            tree_frame,
            columns=("idx", "name", "host", "rate"),
            show="headings",
            height=6,
            selectmode="browse",
        )
        headings = {
            "idx": ("#", 32),
            "name": ("Устройство", 260),
            "host": ("Драйвер", 90),
            "rate": ("Гц", 60),
        }
        for key, (title, width) in headings.items():
            self.device_tree.heading(key, text=title)
            self.device_tree.column(
                key, width=width, anchor="w", stretch=key == "name"
            )
        self.device_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.device_tree.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.device_tree.configure(yscrollcommand=scrollbar.set)
        self.device_tree.bind("<<TreeviewSelect>>", self._on_device_selected)

        info_buttons = ttk.Frame(self.device_panel, style="Card.TFrame")
        info_buttons.grid(row=1, column=0, sticky="ew")
        ttk.Button(
            info_buttons,
            text="Проверить 44.1 / 48 кГц",
            style="Secondary.TButton",
            command=self.check_device_formats,
        ).pack(side="left")
        ttk.Label(
            info_buttons,
            text="Драйвер и ID показаны только здесь.",
            style="CardMuted.TLabel",
        ).pack(side="left", padx=(8, 0))

    def _toggle_device_panel(self) -> None:
        if self._device_panel_open:
            self.device_panel.grid_forget()
            self.device_toggle_button.configure(text="Выбрать устройство")
            self._device_panel_open = False
            self._request_content_sync()
            return
        self.device_panel.grid(row=3, column=0, sticky="nsew")
        self.device_toggle_button.configure(text="Скрыть список")
        self._device_panel_open = True
        if not self.devices:
            self.refresh_devices()
        self._request_content_sync()

    # ------------------------------------------------------- generator card

    def _build_generator_section(self, parent: Any) -> None:
        outer, body = self._card(
            parent,
            "Отправить тестовый сигнал",
            "Создайте WAV, затем воспроизведите его вручную",
        )
        outer.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        self.frequency_var = tk.StringVar(value="1000")
        self.sample_rate_var = tk.StringVar(value="48000")
        self.duration_var = tk.StringVar(value="3.0")
        self.amplitude_var = tk.StringVar(value="0.10")
        self.lead_var = tk.StringVar(value="500")
        self.tail_var = tk.StringVar(value="500")

        ttk.Label(body, text="Частота", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(12, 2), padx=(0, 10)
        )
        ttk.Combobox(
            body,
            textvariable=self.frequency_var,
            values=[f"{value:g}" for value in desktop.FREQUENCY_CHOICES_HZ],
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", pady=(12, 2))

        ttk.Label(body, text="Длительность, с", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        ttk.Entry(body, textvariable=self.duration_var).grid(
            row=1, column=1, sticky="ew", pady=2
        )

        self.level_label = ttk.Label(
            body, text="Уровень: 0.10 · безопасная амплитуда", style="CardMuted.TLabel"
        )
        self.level_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.amplitude_var.trace_add("write", self._update_level_label)

        ttk.Button(
            body,
            text="Создать сигнал",
            style="Accent.TButton",
            command=self.generate_wav,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 6))

        self.generated_info = ttk.Label(
            body,
            text="Сигнал ещё не создан.",
            style="CardMuted.TLabel",
            wraplength=460,
            justify="left",
        )
        self.generated_info.grid(row=4, column=0, columnspan=2, sticky="ew")

        self.play_info = ttk.Label(
            body, text="", style="Card.TLabel", wraplength=460, justify="left"
        )
        self.play_info.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        play_row = ttk.Frame(body, style="Card.TFrame")
        play_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        play_row.columnconfigure(0, weight=1)
        play_row.columnconfigure(1, weight=1)
        self.play_button = ttk.Button(
            play_row,
            text="Воспроизвести",
            style="Accent.TButton",
            command=self.request_playback,
            state="disabled",
        )
        self.play_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_button = ttk.Button(
            play_row,
            text="Стоп",
            style="Danger.TButton",
            command=self.request_stop,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.play_hint = ttk.Label(
            body, text="", style="CardMuted.TLabel", wraplength=460, justify="left"
        )
        self.play_hint.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        self.playback_status = ttk.Label(
            body, text="Воспроизведения не было.", style="Card.TLabel",
            wraplength=460, justify="left",
        )
        self.playback_status.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self.playback_json_label = ttk.Label(
            body, text="", style="CardMuted.TLabel", wraplength=460, justify="left"
        )
        self.playback_json_label.grid(row=9, column=0, columnspan=2, sticky="ew")

        advanced_holder, advanced = self._collapsible(
            body, "Дополнительные настройки"
        )
        advanced_holder.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        advanced.columnconfigure(1, weight=1)

        ttk.Label(advanced, text="Sample rate, Гц", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        ttk.Combobox(
            advanced,
            textvariable=self.sample_rate_var,
            values=[str(value) for value in desktop.SAMPLE_RATE_CHOICES],
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", pady=2)

        ttk.Label(advanced, text="Амплитуда (0…1)", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        ttk.Entry(advanced, textvariable=self.amplitude_var).grid(
            row=1, column=1, sticky="ew", pady=2
        )

        ttk.Label(advanced, text="Тишина до, мс", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        ttk.Entry(advanced, textvariable=self.lead_var).grid(
            row=2, column=1, sticky="ew", pady=2
        )

        ttk.Label(advanced, text="Тишина после, мс", style="Field.TLabel").grid(
            row=3, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        ttk.Entry(advanced, textvariable=self.tail_var).grid(
            row=3, column=1, sticky="ew", pady=2
        )

        ttk.Label(
            advanced,
            text="PCM16 mono. Плавное начало и конец сигнала (косинусная рампа 5 мс).",
            style="CardMuted.TLabel",
            wraplength=440,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 2))

        ttk.Button(
            advanced,
            text="Открыть папку результатов",
            style="Secondary.TButton",
            command=self.open_results_dir,
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _update_level_label(self, *_args: Any) -> None:
        text = self.amplitude_var.get().strip().replace(",", ".")
        try:
            amplitude = float(text)
        except ValueError:
            self.level_label.configure(text="Уровень: укажите число от 0 до 1")
            return
        if amplitude <= 0.10:
            note = "безопасная амплитуда"
        elif amplitude <= 0.30:
            note = "умеренная амплитуда"
        else:
            note = "громко — начните с малой амплитуды"
        self.level_label.configure(text=f"Уровень: {amplitude:g} · {note}")

    # ----------------------------------------------------------- phone card

    def _build_phone_section(self, parent: Any) -> None:
        outer, body = self._card(
            parent,
            "Проверить запись телефона",
            "Импортируйте WAV, записанный телефоном во время теста",
        )
        outer.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        expected_row = ttk.Frame(body, style="Card.TFrame")
        expected_row.grid(row=0, column=0, sticky="ew", pady=(12, 0))
        expected_row.columnconfigure(1, weight=1)
        ttk.Label(
            expected_row, text="Ожидаемая частота, Гц", style="Field.TLabel"
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.expected_var = tk.StringVar(value="1000")
        ttk.Entry(expected_row, textvariable=self.expected_var, width=10).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(
            expected_row,
            text="заполняется после создания сигнала",
            style="CardMuted.TLabel",
        ).grid(row=0, column=2, sticky="e")

        ttk.Button(
            body,
            text="Открыть WAV-запись",
            style="Accent.TButton",
            command=self.import_phone_wav,
        ).grid(row=1, column=0, sticky="ew", pady=(10, 6))

        self.phone_status = ttk.Label(
            body,
            text="WAV телефона не импортирован.",
            style="CardMuted.TLabel",
            wraplength=500,
            justify="left",
        )
        self.phone_status.grid(row=2, column=0, sticky="ew")

        self.verdict_panel = tk.Frame(body, bg=COLOR_NEUTRAL_BG)
        self.verdict_panel.grid(row=3, column=0, sticky="ew", pady=(10, 8))
        self.verdict_panel.columnconfigure(0, weight=1)
        self.verdict_label = tk.Label(
            self.verdict_panel,
            text="Результат появится после импорта",
            background=COLOR_NEUTRAL_BG,
            foreground=COLOR_MUTED,
            font=("Segoe UI", 15, "bold"),
            anchor="w",
            padx=14,
            pady=10,
        )
        self.verdict_label.grid(row=0, column=0, sticky="ew")
        self.verdict_hint = tk.Label(
            self.verdict_panel,
            text="Здесь будет короткое пояснение к записи.",
            background=COLOR_NEUTRAL_BG,
            foreground=COLOR_MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=470,
            padx=14,
            pady=0,
        )
        self.verdict_hint.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        metrics = tk.Frame(body, bg=COLOR_CARD)
        metrics.grid(row=4, column=0, sticky="ew")
        for column in range(2):
            metrics.columnconfigure(column, weight=1, uniform="metric")

        self.metric_expected = self._metric_tile(
            metrics, 0, 0, "Ожидаемая частота", "—"
        )
        self.metric_detected = self._metric_tile(
            metrics, 0, 1, "Обнаруженная частота", "—"
        )
        self.metric_level = self._metric_tile(
            metrics, 1, 0, "Уровень сигнала", "—"
        )
        self.metric_clipping = self._metric_tile(
            metrics, 1, 1, "Перегрузка", "—"
        )

        spectrum_header = ttk.Frame(body, style="Card.TFrame")
        spectrum_header.grid(row=5, column=0, sticky="ew", pady=(10, 2))
        spectrum_header.columnconfigure(0, weight=1)
        ttk.Label(spectrum_header, text="Спектр", style="Field.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            spectrum_header,
            text="канал 0 · уровень в дБ",
            style="CardMuted.TLabel",
        ).grid(row=0, column=1, sticky="e")

        self.psd_canvas = tk.Canvas(
            body,
            height=150,
            background="#FFFFFF",
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
        )
        self.psd_canvas.grid(row=6, column=0, sticky="ew")

        details_holder, details = self._collapsible(body, "Подробнее")
        details_holder.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        details.columnconfigure(0, weight=1)

        text_frame = tk.Frame(details, bg=COLOR_CARD)
        text_frame.grid(row=0, column=0, sticky="ew")
        text_frame.columnconfigure(0, weight=1)
        self.analysis_text = tk.Text(
            text_frame,
            wrap="word",
            height=10,
            font=("Consolas", 9),
            background="#FFFFFF",
            foreground=COLOR_TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            padx=8,
            pady=6,
        )
        self.analysis_text.grid(row=0, column=0, sticky="ew")
        text_scroll = ttk.Scrollbar(
            text_frame, orient="vertical", command=self.analysis_text.yview
        )
        text_scroll.grid(row=0, column=1, sticky="ns")
        self.analysis_text.configure(yscrollcommand=text_scroll.set)
        self.analysis_text.insert(
            "1.0",
            "Импортируйте WAV с телефона: здесь появятся уровни, доминирующая "
            "частота, полоса 18–19 кГц и сравнение ожидаемой и обнаруженной "
            "частоты.\n\nSNR одиночной записи показан как N/A.",
        )
        self.analysis_text.configure(state="disabled")

        detail_buttons = ttk.Frame(details, style="Card.TFrame")
        detail_buttons.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(
            detail_buttons,
            text="Повторить анализ",
            style="Secondary.TButton",
            command=self.analyze_current_phone,
        ).pack(side="left")
        ttk.Button(
            detail_buttons,
            text="Открыть PSD",
            style="Secondary.TButton",
            command=lambda: self.open_png(
                self.phone_record.psd_path if self.phone_record else None
            ),
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            detail_buttons,
            text="Открыть спектрограмму",
            style="Secondary.TButton",
            command=lambda: self.open_png(
                self.phone_record.spectrogram_path if self.phone_record else None
            ),
        ).pack(side="left", padx=(6, 0))

        self.spectrogram_label = ttk.Label(
            details,
            text="Спектрограмма ещё не построена.",
            style="CardMuted.TLabel",
            anchor="center",
        )
        self.spectrogram_label.grid(row=2, column=0, sticky="ew", pady=(6, 0))

    # ------------------------------------------------------------- data link

    def _build_data_send_section(self, parent: Any) -> None:
        outer, body = self._card(
            parent,
            "Передать сообщение",
            "Текст до 64 байт UTF-8 в кадре Frame v1 через звук",
        )
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Профиль", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(12, 2), padx=(0, 10)
        )
        self.data_profile_box = ttk.Combobox(
            body,
            textvariable=self.data_profile_var,
            values=sorted(data_desktop.DATA_PROFILE_NAMES)
            if data_desktop is not None
            else ["data-audible", "data-robust"],
            state="readonly",
        )
        self.data_profile_box.grid(row=0, column=1, sticky="ew", pady=(12, 2))

        ttk.Label(body, text="Сообщение", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        ttk.Entry(body, textvariable=self.data_text_var).grid(
            row=1, column=1, sticky="ew", pady=2
        )

        ttk.Label(
            body,
            text=(
                "Слышимый диапазон: 1200/2200 Гц, 300 бод. "
                "CRC-16 обнаруживает ошибки, но не аутентифицирует отправителя."
            ),
            style="CardMuted.TLabel",
            wraplength=460,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        ttk.Button(
            body,
            text="Создать сообщение",
            style="Accent.TButton",
            command=self.generate_data,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 6))

        self.data_generated_info = ttk.Label(
            body,
            text="Сообщение ещё не создано.",
            style="CardMuted.TLabel",
            wraplength=460,
            justify="left",
        )
        self.data_generated_info.grid(row=4, column=0, columnspan=2, sticky="ew")

        play_row = ttk.Frame(body, style="Card.TFrame")
        play_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        play_row.columnconfigure(0, weight=1)
        play_row.columnconfigure(1, weight=1)
        self.data_play_button = ttk.Button(
            play_row,
            text="Воспроизвести",
            style="Accent.TButton",
            command=self.request_data_playback,
            state="disabled",
        )
        self.data_play_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.data_stop_button = ttk.Button(
            play_row,
            text="Стоп",
            style="Danger.TButton",
            command=self.request_data_stop,
            state="disabled",
        )
        self.data_stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.data_play_hint = ttk.Label(
            body, text="", style="CardMuted.TLabel", wraplength=460, justify="left"
        )
        self.data_play_hint.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        self.data_play_status = ttk.Label(
            body,
            text="Воспроизведения не было.",
            style="Card.TLabel",
            wraplength=460,
            justify="left",
        )
        self.data_play_status.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self.data_play_json = ttk.Label(
            body, text="", style="CardMuted.TLabel", wraplength=460, justify="left"
        )
        self.data_play_json.grid(row=8, column=0, columnspan=2, sticky="ew")

    def _build_data_receive_section(self, parent: Any) -> None:
        outer, body = self._card(
            parent,
            "Принять сообщение",
            "Запись с микрофона и декодирование кадра",
        )
        outer.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Профиль", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(12, 2), padx=(0, 10)
        )
        ttk.Combobox(
            body,
            textvariable=self.data_profile_var,
            values=sorted(data_desktop.DATA_PROFILE_NAMES)
            if data_desktop is not None
            else ["data-audible", "data-robust"],
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", pady=(12, 2))

        ttk.Label(body, text="Микрофон", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        self.data_input_box = ttk.Combobox(body, state="readonly")
        self.data_input_box.grid(row=1, column=1, sticky="ew", pady=2)
        self.data_input_box.bind(
            "<<ComboboxSelected>>", self._on_data_input_selected
        )

        ttk.Label(body, text="Запись, с", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        ttk.Entry(body, textvariable=self.data_duration_var).grid(
            row=2, column=1, sticky="ew", pady=2
        )

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(
            buttons,
            text="Обновить микрофоны",
            style="Secondary.TButton",
            command=self.refresh_data_inputs,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.data_record_button = ttk.Button(
            buttons,
            text="Записать и декодировать",
            style="Accent.TButton",
            command=self.record_and_decode,
        )
        self.data_record_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.data_record_status = ttk.Label(
            body,
            text="Запись ещё не выполнялась.",
            style="CardMuted.TLabel",
            wraplength=460,
            justify="left",
        )
        self.data_record_status.grid(row=4, column=0, columnspan=2, sticky="ew")

        self.data_verdict_panel = tk.Frame(body, bg=COLOR_NEUTRAL_BG)
        self.data_verdict_panel.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 8))
        self.data_verdict_panel.columnconfigure(0, weight=1)
        self.data_verdict_label = tk.Label(
            self.data_verdict_panel,
            text="Сообщение ещё не принималось",
            background=COLOR_NEUTRAL_BG,
            foreground=COLOR_MUTED,
            font=("Segoe UI", 15, "bold"),
            anchor="w",
            padx=14,
            pady=10,
        )
        self.data_verdict_label.grid(row=0, column=0, sticky="ew")
        self.data_verdict_hint = tk.Label(
            self.data_verdict_panel,
            text="Нажмите «Записать и декодировать», когда прозвучит сообщение.",
            background=COLOR_NEUTRAL_BG,
            foreground=COLOR_MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=440,
            padx=14,
            pady=0,
        )
        self.data_verdict_hint.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self.data_received_text = ttk.Label(
            body,
            text="",
            style="Value.TLabel",
            wraplength=460,
            justify="left",
        )
        self.data_received_text.grid(row=6, column=0, columnspan=2, sticky="ew")

        self.data_record_json = ttk.Label(
            body, text="", style="CardMuted.TLabel", wraplength=460, justify="left"
        )
        self.data_record_json.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    def _build_file_send_section(self, parent: Any) -> None:
        outer, body = self._card(
            parent,
            "Передать файл",
            "Файл до 8192 байт: MANIFEST / DATA / END внутри кадров Frame v1",
        )
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Профиль", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(12, 2), padx=(0, 10)
        )
        ttk.Combobox(
            body,
            textvariable=self.file_profile_var,
            values=sorted(file_desktop.DATA_PROFILE_NAMES)
            if file_desktop is not None
            else ["data-audible", "data-robust"],
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", pady=(12, 2))

        ttk.Label(body, text="Файл", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        ttk.Entry(body, textvariable=self.file_source_var).grid(
            row=1, column=1, sticky="ew", pady=2
        )

        ttk.Label(
            body,
            text=(
                "Один файл 0…8192 байт, имя до 40 байт UTF-8. "
                "SHA-256 проверяет целостность, но не аутентифицирует отправителя."
            ),
            style="CardMuted.TLabel",
            wraplength=460,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(
            buttons,
            text="Выбрать файл…",
            style="Secondary.TButton",
            command=self.choose_file_source,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            buttons,
            text="Создать WAV",
            style="Accent.TButton",
            command=self.generate_file_message,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.file_generated_info = ttk.Label(
            body,
            text="WAV с файлом ещё не создан.",
            style="CardMuted.TLabel",
            wraplength=460,
            justify="left",
        )
        self.file_generated_info.grid(row=4, column=0, columnspan=2, sticky="ew")

        play_row = ttk.Frame(body, style="Card.TFrame")
        play_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        play_row.columnconfigure(0, weight=1)
        play_row.columnconfigure(1, weight=1)
        self.file_play_button = ttk.Button(
            play_row,
            text="Воспроизвести",
            style="Accent.TButton",
            command=self.request_file_playback,
            state="disabled",
        )
        self.file_play_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.file_stop_button = ttk.Button(
            play_row,
            text="Стоп",
            style="Danger.TButton",
            command=self.request_file_stop,
            state="disabled",
        )
        self.file_stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.file_play_hint = ttk.Label(
            body, text="", style="CardMuted.TLabel", wraplength=460, justify="left"
        )
        self.file_play_hint.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        self.file_play_status = ttk.Label(
            body,
            text="Воспроизведения не было.",
            style="Card.TLabel",
            wraplength=460,
            justify="left",
        )
        self.file_play_status.grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        self.file_play_json = ttk.Label(
            body, text="", style="CardMuted.TLabel", wraplength=460, justify="left"
        )
        self.file_play_json.grid(row=8, column=0, columnspan=2, sticky="ew")

    def _build_file_receive_section(self, parent: Any) -> None:
        outer, body = self._card(
            parent,
            "Принять файл",
            "Запись с микрофона, сборка файла и проверка SHA-256",
        )
        outer.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Профиль", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(12, 2), padx=(0, 10)
        )
        ttk.Combobox(
            body,
            textvariable=self.file_profile_var,
            values=sorted(file_desktop.DATA_PROFILE_NAMES)
            if file_desktop is not None
            else ["data-audible", "data-robust"],
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", pady=(12, 2))

        ttk.Label(body, text="Микрофон", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        self.file_input_box = ttk.Combobox(body, state="readonly")
        self.file_input_box.grid(row=1, column=1, sticky="ew", pady=2)
        self.file_input_box.bind(
            "<<ComboboxSelected>>", self._on_file_input_selected
        )

        ttk.Label(body, text="Запись, с", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=2, padx=(0, 10)
        )
        ttk.Entry(body, textvariable=self.file_duration_var).grid(
            row=2, column=1, sticky="ew", pady=2
        )

        ttk.Label(
            body,
            text=(
                "Файл 100 байт звучит около 11 с, 4096 байт — около 182 с, "
                "8192 байт — около 364 с. Запись должна покрыть весь сигнал "
                "и паузы; для 8192 байт выберите не меньше 420 с."
            ),
            style="CardMuted.TLabel",
            wraplength=460,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(
            buttons,
            text="Обновить микрофоны",
            style="Secondary.TButton",
            command=self.refresh_file_inputs,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.file_record_button = ttk.Button(
            buttons,
            text="Записать и собрать",
            style="Accent.TButton",
            command=self.record_and_decode_file,
        )
        self.file_record_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.file_record_status = ttk.Label(
            body,
            text="Запись ещё не выполнялась.",
            style="CardMuted.TLabel",
            wraplength=460,
            justify="left",
        )
        self.file_record_status.grid(row=5, column=0, columnspan=2, sticky="ew")

        self.file_verdict_panel = tk.Frame(body, bg=COLOR_NEUTRAL_BG)
        self.file_verdict_panel.grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(10, 8)
        )
        self.file_verdict_panel.columnconfigure(0, weight=1)
        self.file_verdict_label = tk.Label(
            self.file_verdict_panel,
            text="Файл ещё не принимался",
            background=COLOR_NEUTRAL_BG,
            foreground=COLOR_MUTED,
            font=("Segoe UI", 15, "bold"),
            anchor="w",
            padx=14,
            pady=10,
        )
        self.file_verdict_label.grid(row=0, column=0, sticky="ew")
        self.file_verdict_hint = tk.Label(
            self.file_verdict_panel,
            text="Нажмите «Записать и собрать», когда прозвучит файл.",
            background=COLOR_NEUTRAL_BG,
            foreground=COLOR_MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=440,
            padx=14,
            pady=0,
        )
        self.file_verdict_hint.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self.file_received_info = ttk.Label(
            body,
            text="",
            style="Value.TLabel",
            wraplength=460,
            justify="left",
        )
        self.file_received_info.grid(row=7, column=0, columnspan=2, sticky="ew")

        self.file_record_json = ttk.Label(
            body, text="", style="CardMuted.TLabel", wraplength=460, justify="left"
        )
        self.file_record_json.grid(
            row=8, column=0, columnspan=2, sticky="ew", pady=(4, 0)
        )

    # --------------------------------------------------------- data actions

    def refresh_data_inputs(self) -> None:
        if data_desktop is None:
            self.data_record_status.configure(
                text="Модуль передачи данных недоступен: проверьте зависимости."
            )
            return
        try:
            devices = self.input_device_lister()
        except Exception as exc:  # noqa: BLE001 - отказ аудиобэкенда показываем
            self.data_record_status.configure(
                text=f"Не удалось получить микрофоны: {type(exc).__name__}: {exc}"
            )
            return
        self.data_input_devices = [device for device in devices if device.is_input]
        labels = [
            f"[{device.index}] {device.name}" for device in self.data_input_devices
        ]
        self.data_input_box.configure(values=labels)
        if not labels:
            self.data_selected_input = None
            self.data_input_box.set("")
            self.data_record_status.configure(
                text="Входных устройств не найдено. Проверьте микрофон Windows."
            )
            return
        preferred = audio.select_preferred_device(self.data_input_devices, "input")
        if preferred is None:
            self.data_selected_input = None
            self.data_input_box.set("")
            self.data_record_status.configure(
                text=(
                    f"Найдено микрофонов: {len(labels)}. Автовыбор отключён: "
                    "доступны только Bluetooth или виртуальные устройства. "
                    "Подключите встроенный или USB-микрофон."
                )
            )
            return
        self.data_selected_input = preferred
        position = self.data_input_devices.index(preferred)
        self.data_input_box.current(position)
        warning = audio.device_warning(preferred)
        suffix = f" Внимание: {warning}" if warning else ""
        self.data_record_status.configure(
            text=f"Найдено микрофонов: {len(labels)}. Выбран: {preferred.name}.{suffix}"
        )

    def _on_data_input_selected(self, _event: Any | None = None) -> None:
        position = self.data_input_box.current()
        if 0 <= position < len(self.data_input_devices):
            device = self.data_input_devices[position]
            self.data_selected_input = device
            warning = audio.device_warning(device)
            if warning:
                self.data_record_status.configure(
                    text=f"[{device.index}] {device.name}. Внимание: {warning}"
                )
            else:
                self.data_record_status.configure(
                    text=f"[{device.index}] {device.name} выбран для записи."
                )

    def generate_data(self) -> None:
        if data_desktop is None:
            messagebox.showerror(
                "Передача данных", "Модуль передачи данных недоступен.", parent=self.root
            )
            return
        text = self.data_text_var.get()
        try:
            generated = data_desktop.generate_data_message(
                text,
                self.data_profile_var.get(),
                self.results_dir,
            )
        except Exception as exc:  # noqa: BLE001 - ошибку показываем в UI
            self.data_generated_info.configure(
                text=f"Сообщение не создано: {exc}"
            )
            messagebox.showerror("Сообщение не создано", str(exc), parent=self.root)
            return
        self.data_generated = generated
        payload_bytes = generated.report.get("payload_bytes", 0)
        self.data_generated_info.configure(
            text=(
                f"Готово: {payload_bytes} байт UTF-8 · "
                f"{generated.profile.name} · {generated.file_duration_s:.2f} с\n"
                f"Файл: {generated.wav_path.name}"
            )
        )
        self.data_play_status.configure(
            text="Сообщение создано беззвучно. Воспроизведение не выполнялось."
        )
        self.data_play_json.configure(text="")
        self._update_data_play_state()

    def _update_data_play_state(self) -> None:
        generated = self.data_generated
        device = self.selected_device
        if generated is None:
            self.data_play_hint.configure(text="Сначала создайте сообщение.")
        elif device is None:
            self.data_play_hint.configure(
                text="Динамик не найден. Нажмите «Обновить список» выше."
            )
        elif self.data_playing:
            self.data_play_hint.configure(
                text="Идёт воспроизведение сообщения. «Стоп» прерывает его."
            )
        else:
            self.data_play_hint.configure(
                text="Перед стартом приложение попросит подтверждение."
            )
        allowed = (
            generated is not None
            and device is not None
            and self.engine is not None
            and not self.data_playing
            and not self.file_playing
            and not self._playing
        )
        self.data_play_button.configure(state="normal" if allowed else "disabled")
        self.data_stop_button.configure(
            state="normal"
            if self.data_playing and self.engine is not None
            else "disabled"
        )

    def _ask_confirm_data(self, generated: Any, device: Any) -> bool:
        duration = generated.file_duration_s
        warning = audio.device_warning(device)
        warning_line = f"\n\nВнимание: {warning}" if warning else ""
        return bool(
            messagebox.askyesno(
                "Подтверждение передачи",
                (
                    "Сейчас через выбранный динамик прозвучит сообщение.\n\n"
                    f"Текст: {generated.text}\n"
                    f"Профиль: {generated.profile.name}\n"
                    f"Длительность: {duration:.2f} с\n"
                    f"Устройство: {device.name}\n\n"
                    f"Сообщение не зашифровано. Продолжить?{warning_line}"
                ),
                parent=self.root,
                icon="warning",
            )
        )

    def request_data_playback(self) -> bool:
        if data_desktop is None:
            return False
        if self.data_generated is None:
            self.data_play_status.configure(text="Сначала создайте сообщение.")
            return False
        if self.selected_device is None:
            self.data_play_status.configure(text="Сначала выберите динамик ноутбука.")
            return False
        if self.engine is None:
            self.data_play_status.configure(
                text="Воспроизведение недоступно: аудиодвижок не загружен."
            )
            return False
        if self.data_playing or self.file_playing or self._playing:
            self.data_play_status.configure(text="Воспроизведение уже идёт.")
            return False
        if not self.confirm_data_playback(self.data_generated, self.selected_device):
            self.data_play_status.configure(
                text="Воспроизведение отменено пользователем; JSON не создавался."
            )
            return False

        generated = self.data_generated
        device = self.selected_device
        self.data_playing = True
        self._data_play_queue = queue.Queue()
        self.data_play_status.configure(text="Воспроизведение сообщения выполняется…")
        self.data_play_json.configure(text="")
        self._update_data_play_state()
        self._update_file_play_state()

        def worker() -> None:
            try:
                record = data_desktop.play_data_message(
                    generated,
                    device,
                    engine=self.engine,
                    hardware_test=True,
                )
            except Exception as exc:  # noqa: BLE001 - ошибку показываем в UI
                assert self._data_play_queue is not None
                self._data_play_queue.put(("error", exc))
            else:
                assert self._data_play_queue is not None
                self._data_play_queue.put(("record", record))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self._poll_data_playback)
        return True

    def request_data_stop(self) -> None:
        if self.engine is None or not self.data_playing:
            return
        self.data_play_status.configure(text="Останавливаю воспроизведение…")
        self.engine.stop()

    def _poll_data_playback(self) -> None:
        if self._data_play_queue is None:
            return
        try:
            kind, payload = self._data_play_queue.get_nowait()
        except queue.Empty:
            if self.data_playing:
                self.root.after(100, self._poll_data_playback)
            return
        self.data_playing = False
        if kind == "record":
            self._on_data_playback_record(payload)
        else:
            self.data_play_status.configure(
                text=f"PLAY не выполнен: {type(payload).__name__}: {payload}"
            )
        self._update_data_play_state()
        self._update_file_play_state()

    def _on_data_playback_record(self, record: Any) -> None:
        if record.status == "completed" and record.performed:
            text = (
                f"PLAY завершён: воспроизведено {record.frames_written} "
                f"из {record.frames_total} отсчётов за {record.elapsed_s:.2f} с. "
                "Приём этим не подтверждён."
            )
        elif record.status == "stopped":
            text = (
                "PLAY остановлен: сообщение прозвучало не полностью "
                f"({record.frames_written} из {record.frames_total} отсчётов)."
            )
        else:
            text = (
                "PLAY завершился ошибкой: "
                f"{record.error or 'причина не сообщена драйвером'}"
            )
        self.data_play_status.configure(text=text)
        self.data_play_json.configure(text=f"JSON: {record.json_path}")

    def record_and_decode(self) -> None:
        if data_desktop is None:
            return
        if self.data_selected_input is None:
            self.data_record_status.configure(
                text="Сначала выберите микрофон и нажмите «Обновить микрофоны»."
            )
            return
        if self.data_recording:
            return
        try:
            duration = float(self.data_duration_var.get().strip().replace(",", "."))
        except ValueError:
            messagebox.showerror(
                "Длительность записи",
                f"Ожидается число, получено {self.data_duration_var.get()!r}",
                parent=self.root,
            )
            return

        device = self.data_selected_input
        profile = self.data_profile_var.get()
        self.data_recording = True
        self._data_record_queue = queue.Queue()
        self.data_record_button.configure(state="disabled")
        self.data_record_status.configure(
            text=f"Идёт запись {duration:g} с. Сейчас должно прозвучать сообщение…"
        )
        self._set_data_verdict(
            "Идёт запись", "Не закрывайте окно до завершения записи.", COLOR_MUTED
        )
        self.root.update_idletasks()

        def worker() -> None:
            try:
                received = data_desktop.receive_data(
                    profile,
                    device,
                    duration,
                    self.results_dir,
                    recorder=self.recorder,
                )
            except Exception as exc:  # noqa: BLE001 - ошибку драйвера показываем
                assert self._data_record_queue is not None
                self._data_record_queue.put(("error", exc))
            else:
                assert self._data_record_queue is not None
                self._data_record_queue.put(("received", received))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(150, self._poll_data_record)

    def _poll_data_record(self) -> None:
        if self._data_record_queue is None:
            return
        try:
            kind, payload = self._data_record_queue.get_nowait()
        except queue.Empty:
            if self.data_recording:
                self.root.after(150, self._poll_data_record)
            return
        self.data_recording = False
        self.data_record_button.configure(state="normal")
        if kind == "received":
            self._show_received_data(payload)
        else:
            self.data_record_status.configure(
                text=f"Запись не выполнена: {type(payload).__name__}: {payload}"
            )
            self._set_data_verdict(
                "Запись не выполнена",
                "Проверьте микрофон Windows и права доступа к нему.",
                COLOR_ERROR,
            )

    def _show_received_data(self, received: Any) -> None:
        self.data_received = received
        report = received.report
        if received.ok:
            self._set_data_verdict(
                "Сообщение принято",
                "CRC-16 кадра совпал. Это проверка целостности, но не подлинности.",
                COLOR_SUCCESS,
            )
            self.data_received_text.configure(text=received.text or "")
        else:
            title, hint = self._describe_data_error(report)
            self._set_data_verdict(title, hint, COLOR_ERROR)
            self.data_received_text.configure(text="")
        rms = report.get("recording", {}).get("rms_dbfs")
        level = f" · RMS {rms:.1f} dBFS" if isinstance(rms, (int, float)) else ""
        self.data_record_status.configure(
            text=(
                f"Записано {received.recording.duration_s:.2f} с"
                f"{level}. Файл: {received.wav_path.name}"
            )
        )
        self.data_record_json.configure(text=f"JSON: {received.json_path}")
        self._request_content_sync()

    @staticmethod
    def _describe_data_error(report: Any) -> tuple[str, str]:
        error_type = report.get("error_type")
        message = str(report.get("message") or "Причина не сообщена.")
        mapping = {
            "signal_not_found": (
                "Сигнал не найден",
                "В записи нет уверенной преамбулы. Поднесите телефон или "
                "динамик ближе и повторите передачу.",
            ),
            "sync_mismatch": (
                "Кадр повреждён",
                "Преамбула найдена, но синхрослово не совпало. "
                "Помехи или неверный профиль.",
            ),
            "version_unsupported": (
                "Неподдерживаемая версия",
                "Кадр найден, но версия формата не поддерживается.",
            ),
            "length_invalid": (
                "Неверная длина кадра",
                "Длина payload вне допустимого диапазона.",
            ),
            "incomplete_frame": (
                "Запись обрезана",
                "Кадр не успел прозвучать целиком. Увеличьте длительность записи.",
            ),
            "crc_mismatch": (
                "Ошибка CRC",
                "Кадр принят с искажениями. Уменьшите расстояние или "
                "используйте профиль data-robust.",
            ),
            "wav_format_error": (
                "Неверный формат записи",
                "Ожидается PCM16 mono на частоте профиля.",
            ),
            "wav_not_found": (
                "Файл записи не найден",
                "Запись не была сохранена.",
            ),
            "payload_utf8_error": (
                "Текст не декодируется",
                "Кадр цел, но payload не является корректным UTF-8.",
            ),
            "receiver_error": (
                "Ошибка приёма",
                "Приёмник не смог обработать запись.",
            ),
        }
        title, hint = mapping.get(
            error_type,
            (
                "Сообщение не принято",
                "Проверьте расстояние, громкость и выбранный профиль.",
            ),
        )
        return title, f"{hint} ({message})"

    def _set_data_verdict(self, title: str, hint: str, color: str) -> None:
        self.data_verdict_label.configure(text=title, foreground=color)
        self.data_verdict_hint.configure(text=hint)
        self._request_content_sync()

    # --------------------------------------------------------- file actions

    def refresh_file_inputs(self) -> None:
        if file_desktop is None:
            self.file_record_status.configure(
                text="Модуль передачи файлов недоступен: проверьте зависимости."
            )
            return
        try:
            devices = self.input_device_lister()
        except Exception as exc:  # noqa: BLE001 - отказ аудиобэкенда показываем
            self.file_record_status.configure(
                text=f"Не удалось получить микрофоны: {type(exc).__name__}: {exc}"
            )
            return
        self.file_input_devices = [device for device in devices if device.is_input]
        labels = [
            f"[{device.index}] {device.name}" for device in self.file_input_devices
        ]
        self.file_input_box.configure(values=labels)
        if not labels:
            self.file_selected_input = None
            self.file_input_box.set("")
            self.file_record_status.configure(
                text="Входных устройств не найдено. Проверьте микрофон Windows."
            )
            return
        preferred = audio.select_preferred_device(self.file_input_devices, "input")
        if preferred is None:
            self.file_selected_input = None
            self.file_input_box.set("")
            self.file_record_status.configure(
                text=(
                    f"Найдено микрофонов: {len(labels)}. Автовыбор отключён: "
                    "доступны только Bluetooth или виртуальные устройства. "
                    "Подключите встроенный или USB-микрофон."
                )
            )
            return
        self.file_selected_input = preferred
        position = self.file_input_devices.index(preferred)
        self.file_input_box.current(position)
        warning = audio.device_warning(preferred)
        suffix = f" Внимание: {warning}" if warning else ""
        self.file_record_status.configure(
            text=f"Найдено микрофонов: {len(labels)}. Выбран: {preferred.name}.{suffix}"
        )

    def _on_file_input_selected(self, _event: Any | None = None) -> None:
        position = self.file_input_box.current()
        if 0 <= position < len(self.file_input_devices):
            device = self.file_input_devices[position]
            self.file_selected_input = device
            warning = audio.device_warning(device)
            if warning:
                self.file_record_status.configure(
                    text=f"[{device.index}] {device.name}. Внимание: {warning}"
                )
            else:
                self.file_record_status.configure(
                    text=f"[{device.index}] {device.name} выбран для записи."
                )

    def choose_file_source(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбрать файл для передачи",
            filetypes=(("Все файлы", "*.*"),),
            parent=self.root,
        )
        if not path:
            return
        self.file_source_var.set(path)
        source = Path(path)
        try:
            size = source.stat().st_size
        except OSError as exc:
            self.file_generated_info.configure(text=f"Файл недоступен: {exc}")
            return
        limit = (
            file_desktop.file_transfer.MAX_FILE_BYTES
            if file_desktop is not None
            else 8192
        )
        if size > limit:
            self.file_generated_info.configure(
                text=(
                    f"Файл {source.name} занимает {size} байт и не входит "
                    f"в лимит {limit} байт. Выберите файл меньше."
                )
            )
            return
        self.file_generated_info.configure(
            text=f"Выбран файл: {source.name} · {size} байт. Нажмите «Создать WAV»."
        )

    def generate_file_message(self) -> None:
        if file_desktop is None:
            messagebox.showerror(
                "Передача файла", "Модуль передачи файлов недоступен.", parent=self.root
            )
            return
        source = self.file_source_var.get().strip()
        if not source:
            messagebox.showerror(
                "Передача файла",
                "Сначала выберите файл.",
                parent=self.root,
            )
            return
        try:
            generated = file_desktop.generate_file_message(
                source,
                self.file_profile_var.get(),
                self.results_dir,
            )
        except Exception as exc:  # noqa: BLE001 - ошибку показываем в UI
            self.file_generated_info.configure(text=f"WAV не создан: {exc}")
            messagebox.showerror("WAV не создан", str(exc), parent=self.root)
            return
        self.file_generated = generated
        self.file_generated_info.configure(
            text=(
                f"Готово: {generated.filename} · {generated.file_size} байт · "
                f"{generated.packet_count} пакетов (DATA {generated.total_chunks}) · "
                f"{generated.file_duration_s:.2f} с\n"
                f"Запись для приёма: не меньше "
                f"{file_desktop.recommended_record_seconds(generated.file_duration_s):.0f} с\n"
                f"Файл SHA-256: {generated.sha256}\n"
                f"WAV: {generated.wav_path.name}"
            )
        )
        self.file_play_status.configure(
            text="WAV с файлом создан беззвучно. Воспроизведение не выполнялось."
        )
        self.file_play_json.configure(text="")
        self._update_file_play_state()

    def _update_file_play_state(self) -> None:
        generated = self.file_generated
        device = self.selected_device
        if generated is None:
            self.file_play_hint.configure(text="Сначала создайте WAV с файлом.")
        elif device is None:
            self.file_play_hint.configure(
                text="Динамик не найден. Нажмите «Обновить список» выше."
            )
        elif self.file_playing:
            self.file_play_hint.configure(
                text="Идёт воспроизведение файла. «Стоп» прерывает его."
            )
        else:
            self.file_play_hint.configure(
                text="Перед стартом приложение попросит подтверждение."
            )
        allowed = (
            generated is not None
            and device is not None
            and self.engine is not None
            and not self.file_playing
            and not self.data_playing
            and not self._playing
        )
        self.file_play_button.configure(state="normal" if allowed else "disabled")
        self.file_stop_button.configure(
            state="normal"
            if self.file_playing and self.engine is not None
            else "disabled"
        )

    def _ask_confirm_file(self, generated: Any, device: Any) -> bool:
        duration = generated.file_duration_s
        warning = audio.device_warning(device)
        warning_line = f"\n\nВнимание: {warning}" if warning else ""
        return bool(
            messagebox.askyesno(
                "Подтверждение передачи файла",
                (
                    "Сейчас через выбранный динамик прозвучит файл.\n\n"
                    f"Файл: {generated.filename} ({generated.file_size} байт)\n"
                    f"Пакетов: {generated.packet_count} "
                    f"(DATA {generated.total_chunks})\n"
                    f"Профиль: {generated.profile.name}\n"
                    f"Длительность: {duration:.2f} с\n"
                    f"Устройство: {device.name}\n\n"
                    "Файл не зашифрован и не аутентифицирован. "
                    f"Продолжить?{warning_line}"
                ),
                parent=self.root,
                icon="warning",
            )
        )

    def request_file_playback(self) -> bool:
        if file_desktop is None:
            return False
        if self.file_generated is None:
            self.file_play_status.configure(text="Сначала создайте WAV с файлом.")
            return False
        if self.selected_device is None:
            self.file_play_status.configure(
                text="Сначала выберите динамик ноутбука."
            )
            return False
        if self.engine is None:
            self.file_play_status.configure(
                text="Воспроизведение недоступно: аудиодвижок не загружен."
            )
            return False
        if self.file_playing or self.data_playing or self._playing:
            self.file_play_status.configure(text="Воспроизведение уже идёт.")
            return False
        if not self.confirm_file_playback(self.file_generated, self.selected_device):
            self.file_play_status.configure(
                text="Воспроизведение отменено пользователем; JSON не создавался."
            )
            return False

        generated = self.file_generated
        device = self.selected_device
        self.file_playing = True
        self._file_play_queue = queue.Queue()
        self.file_play_status.configure(
            text="Воспроизведение файла выполняется…"
        )
        self.file_play_json.configure(text="")
        self._update_file_play_state()
        self._update_data_play_state()

        def worker() -> None:
            try:
                record = file_desktop.play_file_message(
                    generated,
                    device,
                    engine=self.engine,
                    hardware_test=True,
                )
            except Exception as exc:  # noqa: BLE001 - ошибку показываем в UI
                assert self._file_play_queue is not None
                self._file_play_queue.put(("error", exc))
            else:
                assert self._file_play_queue is not None
                self._file_play_queue.put(("record", record))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self._poll_file_playback)
        return True

    def request_file_stop(self) -> None:
        if self.engine is None or not self.file_playing:
            return
        self.file_play_status.configure(text="Останавливаю воспроизведение…")
        self.engine.stop()

    def _poll_file_playback(self) -> None:
        if self._file_play_queue is None:
            return
        try:
            kind, payload = self._file_play_queue.get_nowait()
        except queue.Empty:
            if self.file_playing:
                self.root.after(100, self._poll_file_playback)
            return
        self.file_playing = False
        if kind == "record":
            self._on_file_playback_record(payload)
        else:
            self.file_play_status.configure(
                text=f"PLAY не выполнен: {type(payload).__name__}: {payload}"
            )
        self._update_file_play_state()
        self._update_data_play_state()

    def _on_file_playback_record(self, record: Any) -> None:
        if record.status == "completed" and record.performed:
            text = (
                f"PLAY завершён: воспроизведено {record.frames_written} "
                f"из {record.frames_total} отсчётов за {record.elapsed_s:.2f} с. "
                "Приём этим не подтверждён."
            )
        elif record.status == "stopped":
            text = (
                "PLAY остановлен: файл прозвучал не полностью "
                f"({record.frames_written} из {record.frames_total} отсчётов)."
            )
        else:
            text = (
                "PLAY завершился ошибкой: "
                f"{record.error or 'причина не сообщена драйвером'}"
            )
        self.file_play_status.configure(text=text)
        self.file_play_json.configure(text=f"JSON: {record.json_path}")

    def record_and_decode_file(self) -> None:
        if file_desktop is None:
            return
        if self.file_selected_input is None:
            self.file_record_status.configure(
                text="Сначала выберите микрофон и нажмите «Обновить микрофоны»."
            )
            return
        if self.file_recording:
            return
        try:
            duration = float(
                self.file_duration_var.get().strip().replace(",", ".")
            )
        except ValueError:
            messagebox.showerror(
                "Длительность записи",
                f"Ожидается число, получено {self.file_duration_var.get()!r}",
                parent=self.root,
            )
            return

        device = self.file_selected_input
        profile = self.file_profile_var.get()
        self.file_recording = True
        self._file_record_queue = queue.Queue()
        self.file_record_button.configure(state="disabled")
        self.file_record_status.configure(
            text=f"Идёт запись {duration:g} с. Сейчас должен прозвучать файл…"
        )
        self._set_file_verdict(
            "Идёт запись", "Не закрывайте окно до завершения записи.", COLOR_MUTED
        )
        self.root.update_idletasks()

        def worker() -> None:
            try:
                received = file_desktop.receive_file(
                    profile,
                    device,
                    duration,
                    self.results_dir,
                    recorder=self.recorder,
                )
            except Exception as exc:  # noqa: BLE001 - ошибку драйвера показываем
                assert self._file_record_queue is not None
                self._file_record_queue.put(("error", exc))
            else:
                assert self._file_record_queue is not None
                self._file_record_queue.put(("received", received))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(150, self._poll_file_record)

    def _poll_file_record(self) -> None:
        if self._file_record_queue is None:
            return
        try:
            kind, payload = self._file_record_queue.get_nowait()
        except queue.Empty:
            if self.file_recording:
                self.root.after(150, self._poll_file_record)
            return
        self.file_recording = False
        self.file_record_button.configure(state="normal")
        if kind == "received":
            self._show_received_file(payload)
        else:
            self.file_record_status.configure(
                text=f"Запись не выполнена: {type(payload).__name__}: {payload}"
            )
            self._set_file_verdict(
                "Запись не выполнена",
                "Проверьте микрофон Windows и права доступа к нему.",
                COLOR_ERROR,
            )

    def _show_received_file(self, received: Any) -> None:
        self.file_received = received
        report = received.report
        if received.ok:
            self._set_file_verdict(
                "Файл принят",
                "Размер и SHA-256 совпали. Это проверка целостности, "
                "но не подлинности отправителя.",
                COLOR_SUCCESS,
            )
            file_info = report.get("file", {})
            self.file_received_info.configure(
                text=(
                    f"{file_info.get('filename')} · "
                    f"{file_info.get('size')} байт\n"
                    f"SHA-256: {file_info.get('sha256')}\n"
                    f"Сохранён: {received.output_path}"
                )
            )
        else:
            title, hint = self._describe_file_error(report)
            self._set_file_verdict(title, hint, COLOR_ERROR)
            transfer = report.get("transfer") or {}
            if transfer:
                self.file_received_info.configure(
                    text=(
                        f"Чанков: {transfer.get('chunks_received')} "
                        f"из {transfer.get('total_chunks')}; "
                        f"пропущено: {len(transfer.get('missing_chunks') or [])}"
                    )
                )
            else:
                self.file_received_info.configure(text="")
        rms = report.get("recording", {}).get("rms_dbfs")
        level = f" · RMS {rms:.1f} dBFS" if isinstance(rms, (int, float)) else ""
        self.file_record_status.configure(
            text=(
                f"Записано {received.recording.duration_s:.2f} с"
                f"{level}. Файл: {received.wav_path.name}"
            )
        )
        self.file_record_json.configure(text=f"JSON: {received.json_path}")
        self._request_content_sync()

    @staticmethod
    def _describe_file_error(report: Any) -> tuple[str, str]:
        error_type = report.get("error_type")
        message = str(report.get("message") or "Причина не сообщена.")
        mapping = {
            "signal_not_found": (
                "Сигнал не найден",
                "В записи нет уверенной преамбулы. Поднесите телефон или "
                "динамик ближе и повторите передачу.",
            ),
            "not_file_transfer": (
                "Это не файл",
                "Кадры найдены, но они не являются файловыми пакетами "
                "MANIFEST/DATA/END.",
            ),
            "incomplete_transfer": (
                "Файл не собран",
                "Не все чанки приняты или запись короче сигнала. Увеличьте "
                "длительность записи и повторите передачу.",
            ),
            "transfer_conflict": (
                "Конфликт передачи",
                "Пакеты относятся к разным передачам или содержат разные "
                "данные. Файл не сохранён.",
            ),
            "hash_mismatch": (
                "SHA-256 не совпал",
                "Файл собран, но его содержимое отличается от отправленного. "
                "Файл не сохранён.",
            ),
            "manifest_invalid": (
                "Повреждён MANIFEST",
                "Имя или размер файла в манифесте некорректны.",
            ),
            "packet_invalid": (
                "Повреждён пакет",
                "Пакет не соответствует контракту файлового слоя.",
            ),
            "filename_invalid": (
                "Недопустимое имя",
                "Имя файла пустое, слишком длинное или содержит запрещённые "
                "символы.",
            ),
            "file_too_large": (
                "Файл слишком велик",
                "Файловый слой v0 принимает не более 8192 байт.",
            ),
            "output_error": (
                "Не удалось сохранить файл",
                "Проверьте права на каталог результатов.",
            ),
            "wav_format_error": (
                "Неверный формат записи",
                "Ожидается PCM16 mono на частоте профиля.",
            ),
            "wav_not_found": (
                "Файл записи не найден",
                "Запись не была сохранена.",
            ),
            "sync_mismatch": (
                "Кадр повреждён",
                "Преамбула найдена, но синхрослово не совпало.",
            ),
            "crc_mismatch": (
                "Ошибка CRC",
                "Кадр принят с искажениями. Уменьшите расстояние или "
                "используйте профиль data-robust.",
            ),
            "incomplete_frame": (
                "Запись обрезана",
                "Кадр не успел прозвучать целиком. Увеличьте длительность "
                "записи.",
            ),
        }
        title, hint = mapping.get(
            error_type,
            (
                "Файл не принят",
                "Проверьте расстояние, громкость, длительность записи и "
                "выбранный профиль.",
            ),
        )
        return title, f"{hint} ({message})"

    def _set_file_verdict(self, title: str, hint: str, color: str) -> None:
        self.file_verdict_label.configure(text=title, foreground=color)
        self.file_verdict_hint.configure(text=hint)
        self._request_content_sync()

    def _metric_tile(
        self, parent: Any, row: int, column: int, title: str, value: str
    ) -> Any:
        tile = tk.Frame(parent, bg=COLOR_NEUTRAL_BG)
        tile.grid(
            row=row,
            column=column,
            sticky="nsew",
            padx=(0, 4) if column == 0 else (4, 0),
            pady=(0, 8),
        )
        tk.Label(
            tile,
            text=title,
            background=COLOR_NEUTRAL_BG,
            foreground=COLOR_MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(anchor="w", padx=10, pady=(7, 0))
        value_label = tk.Label(
            tile,
            text=value,
            background=COLOR_NEUTRAL_BG,
            foreground=COLOR_TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        )
        value_label.pack(anchor="w", padx=10, pady=(0, 7))
        return value_label

    def _card(
        self, parent: Any, title: str, subtitle: str | None = None
    ) -> tuple[Any, Any]:
        outer = tk.Frame(
            parent,
            background=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1,
        )
        body = tk.Frame(outer, background=COLOR_CARD)
        body.pack(
            fill="both", expand=True,
            padx=self._px(18), pady=self._px(16),
        )
        ttk.Label(body, text=title, style="Section.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(
                body, text=subtitle, style="CardMuted.TLabel",
                wraplength=self._px(540), justify="left",
            ).pack(anchor="w", pady=(2, 0))
        inner = tk.Frame(body, background=COLOR_CARD)
        inner.pack(fill="both", expand=True, pady=(10, 0))
        return outer, inner

    def _collapsible(self, parent: Any, title: str) -> tuple[Any, Any]:
        holder = tk.Frame(parent, background=COLOR_CARD)
        content = tk.Frame(holder, background=COLOR_CARD)
        button = ttk.Button(
            holder,
            text=f"▸ {title}",
            style="Link.TButton",
            command=lambda: _toggle(),
        )
        button.pack(anchor="w")

        def _toggle() -> None:
            if content.winfo_manager():
                content.pack_forget()
                button.configure(text=f"▸ {title}")
            else:
                content.pack(fill="both", expand=True, pady=(6, 0))
                button.configure(text=f"▾ {title}")
            self._request_content_sync()

        holder._toggle = _toggle  # type: ignore[attr-defined]
        return holder, content

    # ---------------------------------------------------------------- footer

    def _build_footer(self) -> None:
        footer = ttk.Frame(
            self.root,
            padding=(self._px(20), self._px(4), self._px(20), self._px(8)),
        )
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        note = "Воспроизведение только вручную · после подтверждения"
        ttk.Label(footer, text=note, style="Subtitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            footer,
            text=f"Результаты: {self.results_dir.name}",
            style="Subtitle.TLabel",
        ).grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------- devices

    def refresh_devices(self) -> None:
        self.device_status.configure(text="Опрос устройств без открытия потоков…")
        self.root.update_idletasks()
        try:
            devices = self.device_lister()
        except Exception as exc:  # noqa: BLE001 - отказ аудиобэкенда показываем
            self.device_status.configure(
                text=f"Не удалось получить устройства: {type(exc).__name__}: {exc}"
            )
            return
        self.devices = [device for device in devices if device.is_output]
        for item in self.device_tree.get_children():
            self.device_tree.delete(item)
        for device in self.devices:
            self.device_tree.insert(
                "",
                "end",
                values=(
                    device.index,
                    device.name,
                    device.hostapi_name,
                    f"{device.default_samplerate:g}",
                ),
            )
        self.selected_device = None
        self.device_name_label.configure(text="Устройство не выбрано")
        if self.header_device_label is not None:
            self.header_device_label.configure(text="Динамик: не выбран")
        self._update_playback_state()
        if self.devices:
            preferred = audio.select_preferred_device(self.devices, "output")
            if preferred is None:
                self.device_status.configure(
                    text=(
                        f"Найдено динамиков: {len(self.devices)}. "
                        "Автовыбор отключён: доступны только Bluetooth или "
                        "виртуальные устройства. Подключите встроенный динамик "
                        "и выберите его вручную."
                    )
                )
                return
            for item in self.device_tree.get_children():
                values = self.device_tree.item(item, "values")
                if values and str(values[0]) == str(preferred.index):
                    self.device_tree.selection_set(item)
                    self.device_tree.see(item)
                    break
            self._on_device_selected()
            warning = audio.device_warning(preferred)
            suffix = f" Внимание: {warning}" if warning else ""
            self.device_status.configure(
                text=(
                    f"Найдено динамиков: {len(self.devices)}. "
                    f"Автовыбор: {preferred.name}.{suffix}"
                )
            )
        else:
            self.device_status.configure(
                text="Выходных устройств не найдено. Проверьте драйверы Windows."
            )
            if self.header_device_label is not None:
                self.header_device_label.configure(text="Динамик не найден")

    def _on_device_selected(self, _event: Any | None = None) -> None:
        selection = self.device_tree.selection()
        if not selection:
            return
        values = self.device_tree.item(selection[0], "values")
        if not values:
            return
        try:
            index = int(values[0])
        except (TypeError, ValueError):
            return
        for device in self.devices:
            if device.index == index:
                self.selected_device = device
                self.device_name_label.configure(text=device.name)
                if self.header_device_label is not None:
                    short = device.name if len(device.name) <= 40 else device.name[:39] + "…"
                    self.header_device_label.configure(text=f"Динамик: {short}")
                warning = audio.device_warning(device)
                text = (
                    f"Доступен · драйвер {device.hostapi_name} · "
                    f"{device.default_samplerate:g} Гц"
                )
                self.device_status.configure(
                    text=f"{text}. Внимание: {warning}" if warning else text
                )
                break
        self._update_playback_state()

    def check_device_formats(self) -> None:
        if self.selected_device is None:
            self.device_status.configure(text="Сначала выберите устройство в списке.")
            return
        self.device_status.configure(
            text="Проверка PCM16 mono 48 000 / 44 100 Гц через драйвер…"
        )
        self.root.update_idletasks()
        try:
            result = desktop.probe_output_formats(self.selected_device)
        except Exception as exc:  # noqa: BLE001 - отказ бэкенда показываем
            self.device_status.configure(
                text=f"Проверка форматов не удалась: {type(exc).__name__}: {exc}"
            )
            return
        lines = []
        for rate, entry in result.items():
            if entry["supported"]:
                lines.append(f"{rate} Гц: да")
            else:
                lines.append(f"{rate} Гц: нет")
        self.device_status.configure(text="; ".join(lines))

    # ----------------------------------------------------------- generator

    def _read_settings(self) -> Any:
        def number(value: str, field: str) -> float:
            try:
                return float(value.strip().replace(",", "."))
            except ValueError as exc:
                raise desktop.ToneSettingsError(
                    f"{field}: ожидается число, получено {value!r}"
                ) from exc

        return desktop.ToneSettings(
            frequency_hz=number(self.frequency_var.get(), "Частота"),
            sample_rate=int(self.sample_rate_var.get()),
            duration_s=number(self.duration_var.get(), "Длительность"),
            amplitude=number(self.amplitude_var.get(), "Амплитуда"),
            ramp_ms=desktop.DEFAULT_RAMP_MS,
            lead_ms=number(self.lead_var.get() or "0", "Тишина до"),
            tail_ms=number(self.tail_var.get() or "0", "Тишина после"),
        )

    def generate_wav(self) -> None:
        try:
            settings = self._read_settings()
        except (desktop.ToneSettingsError, ValueError) as exc:
            messagebox.showerror("Параметры сигнала", str(exc), parent=self.root)
            return
        try:
            tone = desktop.generate_tone(settings, self.results_dir)
        except Exception as exc:  # noqa: BLE001 - ошибку файла показываем
            messagebox.showerror(
                "Не удалось создать WAV",
                f"{type(exc).__name__}: {exc}",
                parent=self.root,
            )
            return
        self.generated = tone
        self.expected_var.set(f"{tone.frequency_hz:g}")
        self.generated_info.configure(
            text=(
                f"Готово: {tone.frequency_hz:g} Гц · "
                f"{tone.settings.duration_s:g} с · "
                f"{tone.settings.sample_rate} Гц\n"
                f"Файл: {tone.wav_path.name}"
            )
        )
        self.playback_status.configure(
            text="Сигнал создан беззвучно. Воспроизведение не выполнялось."
        )
        self.playback_json_label.configure(text="")
        self._update_playback_state()

    # ------------------------------------------------------------ playback

    def _update_playback_state(self) -> None:
        tone = self.generated
        device = self.selected_device
        if tone is None:
            self.play_info.configure(text="")
        else:
            self.play_info.configure(
                text=(
                    f"{tone.frequency_hz:g} Гц · "
                    f"{tone.settings.duration_s:g} с · "
                    f"амплитуда {tone.settings.amplitude:g}"
                )
            )
        allowed = (
            tone is not None
            and device is not None
            and not self._playing
        )
        self.play_button.configure(state="normal" if allowed else "disabled")
        self.stop_button.configure(
            state="normal" if self._playing and self.engine is not None else "disabled"
        )
        if tone is None:
            hint = "Сначала создайте сигнал."
        elif device is None:
            hint = "Динамик не найден. Нажмите «Обновить список»."
        elif self._playing:
            hint = "Идёт воспроизведение. Кнопка «Стоп» прерывает его."
        else:
            hint = "Перед стартом приложение попросит подтверждение."
        self.play_hint.configure(text=hint)
        self._update_data_play_state()
        self._update_file_play_state()

    def _ask_confirm(self, tone: Any, device: Any) -> bool:
        return bool(
            messagebox.askyesno(
                "Подтверждение воспроизведения",
                (
                    "Сейчас через выбранный динамик прозвучит тестовый сигнал.\n\n"
                    f"Частота: {tone.frequency_hz:g} Гц\n"
                    f"Длительность: {tone.settings.duration_s:g} с\n"
                    f"Амплитуда: {tone.settings.amplitude:g}\n"
                    f"Устройство: {device.name}\n\n"
                    "Убедитесь, что телефон уже записывает. Продолжить?"
                ),
                parent=self.root,
                icon="warning",
            )
        )

    def request_playback(self) -> bool:
        if self.generated is None:
            self.playback_status.configure(text="Сначала создайте сигнал.")
            return False
        if self.selected_device is None:
            self.playback_status.configure(text="Сначала выберите динамик ноутбука.")
            return False
        if self.engine is None:
            self.playback_status.configure(
                text="Воспроизведение недоступно: аудиодвижок не загружен."
            )
            return False
        if self._playing:
            self.playback_status.configure(text="Воспроизведение уже идёт.")
            return False
        if not self.confirm_playback(self.generated, self.selected_device):
            self.playback_status.configure(
                text="Воспроизведение отменено пользователем; JSON не создавался."
            )
            return False

        tone = self.generated
        device = self.selected_device
        self._playing = True
        self._playback_queue = queue.Queue()
        self.playback_status.configure(text="Воспроизведение выполняется…")
        self.playback_json_label.configure(text="")
        self._update_playback_state()

        def worker() -> None:
            try:
                record = desktop.play_generated(
                    tone,
                    device,
                    engine=self.engine,
                    hardware_test=True,
                )
            except Exception as exc:  # noqa: BLE001 - ошибку показываем в UI
                assert self._playback_queue is not None
                self._playback_queue.put(("error", exc))
            else:
                assert self._playback_queue is not None
                self._playback_queue.put(("record", record))

        self._playback_thread = threading.Thread(target=worker, daemon=True)
        self._playback_thread.start()
        self.root.after(100, self._poll_playback)
        return True

    def _poll_playback(self) -> None:
        if self._playback_queue is None:
            return
        try:
            kind, payload = self._playback_queue.get_nowait()
        except queue.Empty:
            if self._playing:
                self.root.after(100, self._poll_playback)
            return
        self._playing = False
        if kind == "record":
            self._on_playback_record(payload)
        else:
            self.playback_status.configure(
                text=f"PLAY не выполнен: {type(payload).__name__}: {payload}"
            )
        self._update_playback_state()

    def _on_playback_record(self, record: Any) -> None:
        if record.status == "completed" and record.performed:
            text = (
                f"PLAY завершён: воспроизведено {record.frames_written} "
                f"из {record.frames_total} отсчётов за {record.elapsed_s:.2f} с."
            )
        elif record.status == "stopped":
            text = (
                "PLAY остановлен: воспроизведение неполное "
                f"({record.frames_written} из {record.frames_total} отсчётов)."
            )
        else:
            text = (
                "PLAY завершился ошибкой: "
                f"{record.error or 'причина не сообщена драйвером'}"
            )
        self.playback_status.configure(text=text)
        self.playback_json_label.configure(text=f"JSON: {record.json_path}")

    def request_stop(self) -> None:
        if self.engine is None or not self._playing:
            return
        self.playback_status.configure(text="Останавливаю воспроизведение…")
        self.engine.stop()

    # --------------------------------------------------------------- phone

    def import_phone_wav(self) -> None:
        path = filedialog.askopenfilename(
            title="Открыть WAV-запись телефона",
            filetypes=(("WAV PCM16", "*.wav"), ("Все файлы", "*.*")),
            parent=self.root,
        )
        if not path:
            return
        self.phone_source = Path(path)
        self.analyze_current_phone()

    def analyze_current_phone(self) -> None:
        if self.phone_source is None:
            self.phone_status.configure(text="Сначала импортируйте WAV с телефона.")
            self._set_verdict(
                "Файл не выбран", "Нажмите «Открыть WAV-запись».", COLOR_MUTED
            )
            return
        expected_text = self.expected_var.get().strip().replace(",", ".")
        expected: float | None = None
        if expected_text:
            try:
                expected = float(expected_text)
            except ValueError:
                messagebox.showerror(
                    "Ожидаемая частота",
                    f"Ожидается число, получено {self.expected_var.get()!r}",
                    parent=self.root,
                )
                return
        self.phone_status.configure(text="Анализ записи…")
        self.root.update_idletasks()
        try:
            record = self.analyzer(
                self.phone_source,
                expected_frequency_hz=expected,
                results_dir=self.results_dir,
            )
        except Exception as exc:  # noqa: BLE001 - ошибку файла показываем
            self.phone_status.configure(
                text=f"Анализ не выполнен: {type(exc).__name__}: {exc}"
            )
            self._set_verdict(
                "Не удалось прочитать запись",
                "Проверьте, что файл является WAV PCM16 и не повреждён.",
                COLOR_ERROR,
            )
            messagebox.showerror(
                "Ошибка анализа WAV",
                f"{type(exc).__name__}: {exc}",
                parent=self.root,
            )
            return

        self.phone_record = record
        self._update_result_view(record)
        self.analysis_text.configure(state="normal")
        self.analysis_text.delete("1.0", "end")
        self.analysis_text.insert("1.0", desktop.format_phone_summary(record))
        self.analysis_text.configure(state="disabled")
        self.phone_status.configure(
            text=f"Проанализировано: {record.imported_path.name}"
        )
        self._draw_psd(record)
        self._show_spectrogram(record)

    def _update_result_view(self, record: Any) -> None:
        title, hint, color = self._classify_result(record)
        self._set_verdict(title, hint, color)

        expected = record.expected_frequency_hz
        detected = record.detected_frequency_hz
        level = record.analysis.channel_analyses[0].summary["level"]
        clipping = record.analysis.channel_analyses[0].summary["clipping"]

        self.metric_expected.configure(
            text="не задана" if expected is None else f"{expected:g} Гц"
        )
        if detected is None:
            self.metric_detected.configure(text="не найден")
        else:
            self.metric_detected.configure(text=f"{detected:.1f} Гц")
        self.metric_level.configure(text=f"{level['rms_dbfs']:.1f} dBFS")
        if clipping["detected"]:
            self.metric_clipping.configure(
                text=f"да · {clipping['clipped_samples']} отсч."
            )
        else:
            self.metric_clipping.configure(text="нет")

    def _classify_result(self, record: Any) -> tuple[str, str, str]:
        """Короткий результат простым языком на основе фактического анализа."""
        channel = record.analysis.channel_analyses[0]
        level = channel.summary["level"]
        clipping = channel.summary["clipping"]
        rms = float(level["rms_dbfs"])
        expected = record.expected_frequency_hz
        detected = record.detected_frequency_hz
        dominant = record.global_dominant_frequency_hz
        margin = record.detection_margin_db

        if clipping["detected"]:
            return (
                "Запись перегружена",
                "Сигнал слишком громкий: часть отсчётов обрезана. "
                "Уменьшите громкость и повторите тест.",
                COLOR_WARNING,
            )
        if expected is not None:
            if detected is None:
                if dominant is not None and abs(dominant - expected) > 300.0:
                    return (
                        "Обнаружена другая частота",
                        f"В записи есть сигнал около {dominant:.0f} Гц, "
                        f"но ожидалась частота {expected:.0f} Гц.",
                        COLOR_WARNING,
                    )
                return (
                    "Сигнал не обнаружен",
                    f"В записи нет уверенного пика около {expected:.0f} Гц.",
                    COLOR_ERROR,
                )
            if rms < -45.0:
                return (
                    "Слабый сигнал",
                    f"Частота совпадает, но уровень очень низкий "
                    f"({rms:.1f} dBFS). Поднесите телефон ближе к динамику.",
                    COLOR_WARNING,
                )
            if margin is not None and margin < 10.0:
                return (
                    "Слабый сигнал",
                    "Пик едва выделяется над шумом. Проверьте громкость "
                    "и расстояние.",
                    COLOR_WARNING,
                )
            return (
                "Сигнал обнаружен",
                f"Пик около {detected:.1f} Гц уверенно выделяется над шумом.",
                COLOR_SUCCESS,
            )
        if detected is None:
            return (
                "Сигнал не обнаружен",
                "В записи нет выраженного спектрального пика.",
                COLOR_ERROR,
            )
        if rms < -45.0:
            return (
                "Слабый сигнал",
                f"Найден пик {detected:.1f} Гц, но уровень низкий "
                f"({rms:.1f} dBFS).",
                COLOR_WARNING,
            )
        return (
            "Сигнал обнаружен",
            f"Найден спектральный пик {detected:.1f} Гц.",
            COLOR_SUCCESS,
        )

    def _set_verdict(self, title: str, hint: str, color: str) -> None:
        self.verdict_label.configure(text=title, foreground=color)
        self.verdict_hint.configure(text=hint)
        self._request_content_sync()

    def _draw_psd(self, record: Any) -> None:
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - numpy нужен всему анализу
            self.psd_canvas.create_text(
                10, 10, anchor="nw", text="PSD: numpy не установлен"
            )
            return
        canvas = self.psd_canvas
        canvas.delete("all")
        channel = record.analysis.channel_analyses[0]
        frequencies = channel.frequencies
        psd = channel.psd
        if frequencies is None or psd is None or frequencies.size < 2:
            canvas.create_text(
                10, 10, anchor="nw", text="Недостаточно данных для спектра"
            )
            return
        canvas.update_idletasks()
        width = max(320, canvas.winfo_width())
        height = max(110, canvas.winfo_height())
        freqs = np.asarray(frequencies[1:], dtype=np.float64)
        density = np.asarray(psd[1:], dtype=np.float64)
        valid = (freqs > 0) & np.isfinite(density)
        freqs = freqs[valid]
        density = density[valid]
        if freqs.size < 2:
            canvas.create_text(10, 10, anchor="nw", text="Нет данных для спектра")
            return
        db = 10.0 * np.log10(np.maximum(density, 1e-30))
        min_freq = max(20.0, float(freqs[0]))
        max_freq = float(freqs[-1])
        if max_freq <= min_freq:
            return
        min_db = float(np.percentile(db, 1.0))
        max_db = float(np.percentile(db, 99.5))
        if max_db - min_db < 1.0:
            max_db = min_db + 1.0
        step = max(1, freqs.size // max(1, width - 50))
        freqs = freqs[::step]
        db = db[::step]
        left, right, top, bottom = 42, width - 10, 10, height - 22

        def x_of(freq: float) -> float:
            fraction = (math.log10(freq) - math.log10(min_freq)) / (
                math.log10(max_freq) - math.log10(min_freq)
            )
            return left + fraction * max(1, right - left)

        def y_of(value: float) -> float:
            fraction = (value - min_db) / (max_db - min_db)
            return bottom - fraction * max(1, bottom - top)

        for value in (min_db, (min_db + max_db) / 2.0, max_db):
            y = y_of(value)
            canvas.create_line(left, y, right, y, fill="#E5E9F0")
            canvas.create_text(
                2, y, anchor="w", text=f"{value:.0f}", font=("Segoe UI", 7),
                fill=COLOR_MUTED,
            )
        for freq in (100, 1_000, 10_000, 20_000):
            if min_freq <= freq <= max_freq:
                x = x_of(float(freq))
                canvas.create_line(x, top, x, bottom, fill="#F0F3F8")
                label = f"{freq // 1000}k" if freq >= 1_000 else f"{freq}"
                canvas.create_text(
                    x, height - 10, text=label, font=("Segoe UI", 7),
                    fill=COLOR_MUTED,
                )
        points: list[float] = []
        for freq, value in zip(freqs, db):
            points.extend((x_of(float(freq)), y_of(float(value))))
        if len(points) >= 4:
            canvas.create_line(*points, fill=COLOR_ACCENT, width=2)
        canvas.create_text(
            left + 4,
            top + 2,
            anchor="nw",
            text=f"{record.imported_path.name}",
            font=("Segoe UI", 8, "bold"),
            fill=COLOR_TEXT,
        )

    def _show_spectrogram(self, record: Any) -> None:
        path = record.spectrogram_path
        if path is None or not Path(path).is_file():
            self.spectrogram_label.configure(
                image="", text="Спектрограмма не создана."
            )
            self._spectrogram_image = None
            self._request_content_sync()
            return
        try:
            image = tk.PhotoImage(file=str(path))
            factor = max(1, image.width() // 620, image.height() // 180)
            if factor > 1:
                image = image.subsample(factor, factor)
            self._spectrogram_image = image
            self.spectrogram_label.configure(image=image, text="")
        except Exception:  # noqa: BLE001 - предпросмотр не обязателен
            self._spectrogram_image = None
            self.spectrogram_label.configure(
                image="",
                text=f"PNG сохранён: {path}",
            )
        self._request_content_sync()

    def open_results_dir(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._open_path(self.results_dir)

    def open_png(self, path: Any | None) -> None:
        if path is None:
            self.phone_status.configure(text="PNG ещё не создан.")
            return
        self._open_path(Path(path))

    @staticmethod
    def _open_path(path: Path) -> None:
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:  # pragma: no cover - не Windows
                import webbrowser

                webbrowser.open(path.resolve().as_uri())
        except Exception:  # noqa: BLE001 - открытие проводника не критично
            pass

    def _on_close(self) -> None:
        if (self._playing or self.file_playing) and self.engine is not None:
            try:
                self.engine.stop()
            except Exception:  # noqa: BLE001
                pass
        self.root.destroy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "SilentNetwork Acoustic Lab: тестовый WAV, ручное PLAY через "
            "выбранный динамик и анализ WAV с Android."
        ),
        epilog=(
            "При запуске автоматически выбирается системный динамик. "
            "PLAY доступен после создания сигнала и требует подтверждения."
        ),
    )
    parser.add_argument(
        "--hardware-test",
        action="store_true",
        help=(
            "совместимый режим; PLAY уже доступен по умолчанию. "
            "Перед каждым PLAY запрашивается подтверждение."
        ),
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="каталог результатов (по умолчанию acoustic-lab/results/hardware_calibration)",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="собрать GUI, обновить окно и выйти без звука и без mainloop",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if _TK_IMPORT_ERROR is not None:
        print(
            f"Tkinter недоступен: {_TK_IMPORT_ERROR}. "
            "Установите Python с поддержкой Tk или используйте другой интерпретатор.",
            file=sys.stderr,
        )
        return 3
    if desktop is None or playback is None:
        print(
            "Не установлены зависимости Acoustic Lab: "
            f"{_DEPENDENCY_ERROR}. Выполните: "
            "python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 4
    prepare_tcl_environment()
    enable_high_dpi()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Не удалось открыть окно Tkinter: {exc}", file=sys.stderr)
        return 3
    if args.smoke_test:
        root.withdraw()
    try:
        app = DesktopApp(
            root,
            hardware_test=args.hardware_test,
            results_dir=args.results_dir,
        )
        if args.smoke_test:
            root.update_idletasks()
            root.update()
            print(
                "GUI smoke test OK: "
                f"hardware_test={app.hardware_test}, "
                f"play_enabled={app.play_button.instate(['!disabled'])}, "
                f"results_dir={app.results_dir}"
            )
            return 0
        root.mainloop()
        return 0
    except Exception as exc:  # noqa: BLE001 - верхний уровень GUI
        print(f"Не удалось запустить GUI: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
