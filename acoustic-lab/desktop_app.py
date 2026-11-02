"""desktop_app.py — SilentNetwork Acoustic Lab, Windows GUI (SN-016).

Сценарий: Windows → динамик ноутбука → воздух → микрофон Android → WAV →
анализ на Windows. Приложение не управляет телефоном по сети.

Безопасный режим по умолчанию — FILE-ONLY: WAV создаётся беззвучно, PLAY
отключён, аудиоустройства не открываются. Физическое воспроизведение
разрешается только флагом `--hardware-test` и подтверждается перед каждым
нажатием PLAY. Список устройств ничего не выбирает автоматически.

Запуск:
    python desktop_app.py                  # FILE-ONLY
    python desktop_app.py --hardware-test  # разрешить ручной PLAY
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
    from snlab import desktop, playback
except ImportError as _dependency_exc:  # pragma: no cover - зависит от окружения
    desktop = None  # type: ignore[assignment]
    playback = None  # type: ignore[assignment]
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


class DesktopApp:
    """Тонкая Tkinter-оболочка над `snlab.desktop`."""

    def __init__(
        self,
        root: Any,
        *,
        hardware_test: bool = False,
        results_dir: str | Path | None = None,
        engine: Any | None = None,
        device_lister: Callable[[], list[Any]] | None = None,
        confirm_playback: Callable[[Any, Any], bool] | None = None,
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
            else (playback.SoundDevicePlaybackEngine() if self.hardware_test else None)
        )
        self.device_lister = device_lister or desktop.list_output_devices
        self.confirm_playback = confirm_playback or self._ask_confirm
        self.analyzer = analyzer or desktop.analyze_phone_wav

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

        _configure_style(root)
        self._build_ui()
        self._update_playback_state()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.root.title(WINDOW_TITLE)
        self.root.configure(background=COLOR_BG)
        self.root.geometry("1220x900")
        self.root.minsize(1_020, 720)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._build_header()
        self._build_content()
        self._build_footer()

    # ---------------------------------------------------------------- header

    def _build_header(self) -> None:
        header = ttk.Frame(self.root, padding=(18, 14, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        titles = ttk.Frame(header)
        titles.grid(row=0, column=0, sticky="w")
        ttk.Label(titles, text="SilentNetwork", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            titles,
            text="Акустическая лаборатория · передача данных через звук",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(1, 0))

        if self.hardware_test:
            mode_text = "Тестовый режим"
            mode_bg, mode_fg = "#FFF4E5", COLOR_WARNING
        else:
            mode_text = "Только файлы"
            mode_bg, mode_fg = "#E8F5EC", COLOR_SUCCESS
        tk.Label(
            header,
            text=mode_text,
            background=mode_bg,
            foreground=mode_fg,
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
        ).grid(row=0, column=1, sticky="e")

    # --------------------------------------------------------------- content

    def _build_content(self) -> None:
        shell = ttk.Frame(self.root, padding=(14, 6, 14, 6))
        shell.grid(row=1, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        # Прокручиваемая область: при раскрытии «Подробнее» карточка анализа
        # становится выше окна, и без прокрутки нижние элементы были бы
        # недоступны.
        self.content_canvas = tk.Canvas(
            shell,
            background=COLOR_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        self.content_scroll = ttk.Scrollbar(
            shell, orient="vertical", command=self.content_canvas.yview
        )
        self.content_scroll.grid(row=0, column=1, sticky="ns")
        self.content_scroll.grid_remove()
        self.content_canvas.configure(yscrollcommand=self._on_content_scroll)

        content = ttk.Frame(self.content_canvas)
        self._content_window = self.content_canvas.create_window(
            (0, 0), window=content, anchor="nw"
        )
        content.columnconfigure(0, weight=1, uniform="card")
        content.columnconfigure(1, weight=1, uniform="card")
        content.rowconfigure(0, weight=1)

        content.bind("<Configure>", self._sync_content_scroll)
        self.content_canvas.bind("<Configure>", self._sync_content_scroll)
        self.root.bind_all("<MouseWheel>", self._on_content_mousewheel, add="+")
        self._content_frame = content

        left = ttk.Frame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        right = ttk.Frame(content)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self._build_device_section(left)
        self._build_generator_section(left)
        self._build_phone_section(right)

    def _on_content_scroll(self, first: str, last: str) -> None:
        self.content_scroll.set(first, last)
        fits = float(first) <= 0.0 and float(last) >= 1.0
        if fits:
            if self.content_scroll.winfo_ismapped():
                self.content_scroll.grid_remove()
            self.content_canvas.yview_moveto(0.0)
        elif not self.content_scroll.winfo_ismapped():
            self.content_scroll.grid()

    def _sync_content_scroll(self, _event: Any | None = None) -> None:
        canvas = self.content_canvas
        if not canvas.winfo_exists():
            return
        width = canvas.winfo_width()
        if self.content_scroll.winfo_ismapped():
            width -= self.content_scroll.winfo_reqwidth()
        width = max(1, width)
        needed = self._content_frame.winfo_reqheight()
        height = max(needed, canvas.winfo_height())
        canvas.itemconfigure(self._content_window, width=width, height=height)
        canvas.configure(scrollregion=(0, 0, width, height))
        if needed > canvas.winfo_height():
            if not self.content_scroll.winfo_ismapped():
                self.content_scroll.grid()
        elif self.content_scroll.winfo_ismapped():
            self.content_scroll.grid_remove()
            canvas.yview_moveto(0.0)

    def _request_content_sync(self) -> None:
        """Пересчитать прокрутку после изменения высоты содержимого.

        Сначала нужно дать Tk пересчитать запрошенные размеры после pack/grid,
        а затем применить новую область прокрутки.
        """

        def _run() -> None:
            if not self.root.winfo_exists():
                return
            self.root.update_idletasks()
            self._sync_content_scroll()

        self.root.after_idle(_run)

    def _on_content_mousewheel(self, event: Any) -> None:
        if not self.content_scroll.winfo_ismapped():
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None or not str(widget).startswith(str(self.content_canvas)):
            return
        step = -1 if event.delta > 0 else 1
        self.content_canvas.yview_scroll(step * 2, "units")

    # ---------------------------------------------------------- device card

    def _build_device_section(self, parent: Any) -> None:
        outer, body = self._card(
            parent,
            "Динамик ноутбука",
            "Устройство, через которое прозвучит тестовый сигнал",
        )
        outer.grid(row=0, column=0, sticky="ew", pady=(0, 12))
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
        outer.grid(row=1, column=0, sticky="nsew")
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
        body.pack(fill="both", expand=True, padx=16, pady=14)
        ttk.Label(body, text=title, style="Section.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(
                body, text=subtitle, style="CardMuted.TLabel",
                wraplength=500, justify="left",
            ).pack(anchor="w", pady=(1, 0))
        inner = tk.Frame(body, background=COLOR_CARD)
        inner.pack(fill="both", expand=True, pady=(6, 0))
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
        footer = ttk.Frame(self.root, padding=(18, 2, 18, 10))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        note = (
            "Тестовый режим: воспроизведение запускается только вручную и после подтверждения."
            if self.hardware_test
            else "Безопасный режим: звук не воспроизводится, WAV создаётся беззвучно."
        )
        ttk.Label(footer, text=note, style="Subtitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            footer,
            text=f"Результаты: {self.results_dir}",
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
        self._update_playback_state()
        if self.devices:
            self.device_status.configure(
                text=f"Найдено динамиков: {len(self.devices)}. Выберите нужный в списке."
            )
        else:
            self.device_status.configure(
                text="Выходных устройств не найдено. Проверьте драйверы Windows."
            )

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
                self.device_status.configure(
                    text=(
                        f"Доступен · драйвер {device.hostapi_name} · "
                        f"{device.default_samplerate:g} Гц"
                    )
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
            self.hardware_test
            and tone is not None
            and device is not None
            and not self._playing
        )
        self.play_button.configure(state="normal" if allowed else "disabled")
        self.stop_button.configure(
            state="normal" if self._playing and self.engine is not None else "disabled"
        )
        if not self.hardware_test:
            hint = "Воспроизведение отключено в безопасном режиме."
        elif tone is None:
            hint = "Сначала создайте сигнал."
        elif device is None:
            hint = "Выберите динамик ноутбука."
        elif self._playing:
            hint = "Идёт воспроизведение. Кнопка «Стоп» прерывает его."
        else:
            hint = "Перед стартом приложение попросит подтверждение."
        self.play_hint.configure(text=hint)

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
        if not self.hardware_test:
            self.playback_status.configure(
                text="Воспроизведение недоступно: приложение работает без звука."
            )
            return False
        if self.generated is None:
            self.playback_status.configure(text="Сначала создайте сигнал.")
            return False
        if self.selected_device is None:
            self.playback_status.configure(text="Сначала выберите динамик ноутбука.")
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
        if self._playing and self.engine is not None:
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
            "По умолчанию FILE-ONLY: звук не воспроизводится. "
            "PLAY доступен только с --hardware-test и после подтверждения."
        ),
    )
    parser.add_argument(
        "--hardware-test",
        action="store_true",
        help=(
            "РАЗРЕШИТЬ физический PLAY через явно выбранный динамик. "
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
