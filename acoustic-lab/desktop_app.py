"""desktop_app.py — SilentNetwork Acoustic Lab, Windows GUI (SN-015).

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
TASK_ID = "SN-015"
WINDOW_TITLE = "SilentNetwork Acoustic Lab — SN-015"


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

        self._build_ui()
        self._update_playback_state()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1220x900")
        self.root.minsize(1_080, 760)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=3)
        self.root.rowconfigure(3, weight=2)

        header = ttk.Frame(self.root, padding=(10, 8, 10, 2))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="SilentNetwork Acoustic Lab",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        mode_text = (
            "HARDWARE TEST: PLAY разрешён только вручную, с подтверждением"
            if self.hardware_test
            else "FILE-ONLY: WAV и анализ без звука; PLAY отключён"
        )
        ttk.Label(
            header,
            text=mode_text,
            foreground="#8a4b00" if self.hardware_test else "#1f5f2a",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        scenario = ttk.LabelFrame(
            self.root,
            text="Сценарий LAPTOP → PHONE (без сетевого управления телефоном)",
            padding=(8, 4),
        )
        scenario.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 2))
        ttk.Label(
            scenario,
            text=(
                "1) Android: начать запись на 10 с.   "
                "2) Windows: создать WAV и выбрать динамик.   "
                "3) Нажать PLAY и подтвердить.\n"
                "4) Ноутбук воспроизводит 3 с тона.   "
                "5) Android сохраняет запись.   "
                "6) Экспортировать WAV и импортировать его ниже."
            ),
            justify="left",
        ).pack(anchor="w")

        body = ttk.Frame(self.root, padding=(10, 4))
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        for column in range(3):
            body.columnconfigure(column, weight=1, uniform="sections")

        self._build_device_section(body, column=0)
        self._build_generator_section(body, column=1)
        self._build_playback_section(body, column=2)
        self._build_phone_section()

    def _build_device_section(self, parent: Any, column: int) -> None:
        frame = ttk.LabelFrame(parent, text="A. OUTPUT DEVICE", padding=8)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0, 5))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(
            frame,
            text="Только выходные устройства. Ничего не выбирается автоматически.",
            wraplength=300,
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        tree_frame = ttk.Frame(frame)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.device_tree = ttk.Treeview(
            tree_frame,
            columns=("idx", "name", "host", "out", "rate"),
            show="headings",
            height=10,
            selectmode="browse",
        )
        headings = {
            "idx": ("#", 35),
            "name": ("Устройство", 190),
            "host": ("Host API", 85),
            "out": ("Out", 40),
            "rate": ("Rate, Гц", 75),
        }
        for key, (title, width) in headings.items():
            self.device_tree.heading(key, text=title)
            self.device_tree.column(key, width=width, anchor="w", stretch=key == "name")
        self.device_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.device_tree.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.device_tree.configure(yscrollcommand=scrollbar.set)
        self.device_tree.bind("<<TreeviewSelect>>", self._on_device_selected)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="ew", pady=(6, 2))
        ttk.Button(
            buttons,
            text="Обновить устройства",
            command=self.refresh_devices,
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Проверить 48/44.1 кГц",
            command=self.check_device_formats,
        ).pack(side="left", padx=(5, 0))

        self.device_status = ttk.Label(
            frame,
            text="Список не загружен. Нажмите «Обновить устройства».",
            wraplength=330,
            justify="left",
        )
        self.device_status.grid(row=3, column=0, sticky="ew", pady=(4, 0))

    def _build_generator_section(self, parent: Any, column: int) -> None:
        frame = ttk.LabelFrame(parent, text="B. TEST SIGNAL GENERATOR", padding=8)
        frame.grid(row=0, column=column, sticky="nsew", padx=5)
        frame.columnconfigure(1, weight=1)

        self.frequency_var = tk.StringVar(value="1000")
        self.sample_rate_var = tk.StringVar(value="48000")
        self.duration_var = tk.StringVar(value="3.0")
        self.amplitude_var = tk.StringVar(value="0.10")
        self.lead_var = tk.StringVar(value="500")
        self.tail_var = tk.StringVar(value="500")

        rows = (
            ("Частота, Гц", self.frequency_var, "combobox", [f"{v:g}" for v in desktop.FREQUENCY_CHOICES_HZ]),
            ("Sample rate, Гц", self.sample_rate_var, "combobox", [str(v) for v in desktop.SAMPLE_RATE_CHOICES]),
            ("Длительность, с", self.duration_var, "entry", None),
            ("Амплитуда (0..1)", self.amplitude_var, "entry", None),
            ("Тишина до, мс", self.lead_var, "entry", None),
            ("Тишина после, мс", self.tail_var, "entry", None),
        )
        for row, (label, variable, kind, values) in enumerate(rows):
            ttk.Label(frame, text=label).grid(
                row=row, column=0, sticky="w", pady=2, padx=(0, 6)
            )
            if kind == "combobox":
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=values,
                    state="readonly",
                    width=12,
                )
            else:
                widget = ttk.Entry(frame, textvariable=variable, width=14)
            widget.grid(row=row, column=1, sticky="ew", pady=2)

        ttk.Label(
            frame,
            text="PCM16 mono, косинусная рампа 5 мс. Генерация всегда беззвучна.",
            wraplength=300,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 4))

        ttk.Button(
            frame,
            text="Создать WAV (без звука)",
            command=self.generate_wav,
        ).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(2, 6))

        self.generated_info = ttk.Label(
            frame,
            text="WAV ещё не создан.",
            wraplength=320,
            justify="left",
        )
        self.generated_info.grid(row=8, column=0, columnspan=2, sticky="ew")
        ttk.Button(
            frame,
            text="Открыть папку результатов",
            command=self.open_results_dir,
        ).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_playback_section(self, parent: Any, column: int) -> None:
        frame = ttk.LabelFrame(parent, text="C. PLAYBACK", padding=8)
        frame.grid(row=0, column=column, sticky="nsew", padx=(5, 0))
        frame.columnconfigure(0, weight=1)

        self.play_info = ttk.Label(
            frame,
            text="Тон не создан; устройство не выбрано.",
            wraplength=320,
            justify="left",
        )
        self.play_info.grid(row=0, column=0, sticky="ew")

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.play_button = ttk.Button(
            buttons,
            text="PLAY",
            command=self.request_playback,
            state="disabled",
        )
        self.play_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.stop_button = ttk.Button(
            buttons,
            text="STOP",
            command=self.request_stop,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        self.play_hint = ttk.Label(
            frame,
            text="",
            wraplength=320,
            justify="left",
        )
        self.play_hint.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.playback_status = ttk.Label(
            frame,
            text="Воспроизведения не было.",
            wraplength=320,
            justify="left",
        )
        self.playback_status.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.playback_json_label = ttk.Label(
            frame,
            text="",
            wraplength=320,
            justify="left",
        )
        self.playback_json_label.grid(row=4, column=0, sticky="ew", pady=(4, 0))

    def _build_phone_section(self) -> None:
        frame = ttk.LabelFrame(
            self.root,
            text="D. PHONE WAV → ANALYSIS",
            padding=(8, 6),
        )
        frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(4, 10))
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=3)
        frame.rowconfigure(0, weight=1)

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(4, weight=1)

        expected_row = ttk.Frame(left)
        expected_row.grid(row=0, column=0, sticky="ew")
        expected_row.columnconfigure(1, weight=1)
        ttk.Label(expected_row, text="Expected frequency, Гц:").grid(
            row=0, column=0, sticky="w"
        )
        self.expected_var = tk.StringVar(value="1000")
        ttk.Entry(expected_row, textvariable=self.expected_var, width=12).grid(
            row=0, column=1, sticky="w", padx=(6, 0)
        )

        buttons = ttk.Frame(left)
        buttons.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        ttk.Button(
            buttons,
            text="Импорт WAV с телефона",
            command=self.import_phone_wav,
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Повторить анализ",
            command=self.analyze_current_phone,
        ).pack(side="left", padx=(5, 0))

        self.phone_status = ttk.Label(
            left,
            text="WAV телефона не импортирован.",
            wraplength=420,
            justify="left",
        )
        self.phone_status.grid(row=2, column=0, sticky="ew", pady=(2, 4))

        ttk.Label(left, text="PSD (канал 0)").grid(row=3, column=0, sticky="w")
        self.psd_canvas = tk.Canvas(
            left,
            height=170,
            background="white",
            highlightthickness=1,
            highlightbackground="#b0b0b0",
        )
        self.psd_canvas.grid(row=4, column=0, sticky="nsew", pady=(2, 4))

        self.spectrogram_label = ttk.Label(
            left,
            text="Спектрограмма ещё не построена.",
            anchor="center",
        )
        self.spectrogram_label.grid(row=5, column=0, sticky="ew")

        open_buttons = ttk.Frame(left)
        open_buttons.grid(row=6, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(
            open_buttons,
            text="Открыть PSD PNG",
            command=lambda: self.open_png(self.phone_record.psd_path if self.phone_record else None),
        ).pack(side="left")
        ttk.Button(
            open_buttons,
            text="Открыть спектрограмму",
            command=lambda: self.open_png(
                self.phone_record.spectrogram_path if self.phone_record else None
            ),
        ).pack(side="left", padx=(5, 0))

        text_frame = ttk.Frame(frame)
        text_frame.grid(row=0, column=1, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.analysis_text = tk.Text(
            text_frame,
            wrap="word",
            height=18,
            font=("Consolas", 9),
        )
        self.analysis_text.grid(row=0, column=0, sticky="nsew")
        text_scroll = ttk.Scrollbar(
            text_frame, orient="vertical", command=self.analysis_text.yview
        )
        text_scroll.grid(row=0, column=1, sticky="ns")
        self.analysis_text.configure(yscrollcommand=text_scroll.set)
        self.analysis_text.insert(
            "1.0",
            "Импортируйте WAV с телефона: здесь появятся уровни, доминирующая "
            "частота, полоса 18–19 кГц и Expected frequency vs Detected frequency.\n\n"
            "SNR одиночной записи будет показан как N/A.",
        )
        self.analysis_text.configure(state="disabled")

    # ------------------------------------------------------------- devices

    def refresh_devices(self) -> None:
        self.device_status.configure(text="Опрос устройств без открытия потоков...")
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
                    device.max_output_channels,
                    f"{device.default_samplerate:g}",
                ),
            )
        self.selected_device = None
        self._update_playback_state()
        if self.devices:
            self.device_status.configure(
                text=(
                    f"Найдено выходных устройств: {len(self.devices)}. "
                    "Выберите динамик ноутбука вручную; по умолчанию ничего не выбрано."
                )
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
                self.device_status.configure(
                    text=(
                        f"Выбрано: [{device.index}] {device.name} "
                        f"({device.hostapi_name}); out={device.max_output_channels}; "
                        f"driver rate={device.default_samplerate:g} Гц"
                    )
                )
                break
        self._update_playback_state()

    def check_device_formats(self) -> None:
        if self.selected_device is None:
            self.device_status.configure(text="Сначала выберите устройство в списке.")
            return
        self.device_status.configure(
            text="Проверка PCM16 mono 48000/44100 Гц через драйвер (поток не открывается)..."
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
                lines.append(f"{rate} Гц: поддерживается")
            else:
                lines.append(f"{rate} Гц: НЕТ ({entry['error']})")
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
            messagebox.showerror("Параметры тона", str(exc), parent=self.root)
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
                f"WAV: {tone.wav_path}\n"
                f"JSON: {tone.metadata_path}\n"
                f"{tone.frequency_hz:g} Гц, {settings.sample_rate} Гц, "
                f"{settings.duration_s:g} с, амплитуда {settings.amplitude:g}; "
                f"SHA-256 {tone.sha256[:16]}…"
            )
        )
        self.playback_status.configure(
            text="WAV создан беззвучно. PLAY не выполнялся."
        )
        self.playback_json_label.configure(text="")
        self._update_playback_state()

    # ------------------------------------------------------------ playback

    def _update_playback_state(self) -> None:
        tone = self.generated
        device = self.selected_device
        if tone is None:
            self.play_info.configure(text="Тон не создан; устройство не выбрано.")
        else:
            device_text = (
                "не выбрано"
                if device is None
                else f"[{device.index}] {device.name} ({device.hostapi_name})"
            )
            self.play_info.configure(
                text=(
                    f"Тон: {tone.frequency_hz:g} Гц; "
                    f"длительность {tone.settings.duration_s:g} с; "
                    f"амплитуда {tone.settings.amplitude:g}\n"
                    f"Устройство: {device_text}\n"
                    f"WAV: {tone.wav_path.name}"
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
            hint = "PLAY отключён: FILE-ONLY. Перезапустите с --hardware-test."
        elif tone is None:
            hint = "PLAY недоступен: сначала создайте WAV."
        elif device is None:
            hint = "PLAY недоступен: выберите выходное устройство вручную."
        elif self._playing:
            hint = "Идёт воспроизведение. STOP прерывает его и помечает как неполное."
        else:
            hint = "PLAY доступен: перед стартом будет запрошено подтверждение."
        self.play_hint.configure(text=hint)

    def _ask_confirm(self, tone: Any, device: Any) -> bool:
        return bool(
            messagebox.askyesno(
                "Подтверждение PLAY",
                (
                    "Сейчас будет слышимый звук через выбранный динамик.\n\n"
                    f"Частота: {tone.frequency_hz:g} Гц\n"
                    f"Длительность: {tone.settings.duration_s:g} с\n"
                    f"Амплитуда: {tone.settings.amplitude:g}\n"
                    f"Устройство: [{device.index}] {device.name}\n"
                    f"Host API: {device.hostapi_name}\n\n"
                    "Убедитесь, что телефон уже записывает. Продолжить?"
                ),
                parent=self.root,
                icon="warning",
            )
        )

    def request_playback(self) -> bool:
        if not self.hardware_test:
            self.playback_status.configure(
                text="PLAY запрещён: приложение в FILE-ONLY. Нужен --hardware-test."
            )
            return False
        if self.generated is None:
            self.playback_status.configure(text="Сначала создайте WAV.")
            return False
        if self.selected_device is None:
            self.playback_status.configure(text="Сначала выберите выходное устройство.")
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
        self.playback_status.configure(text="Воспроизведение выполняется...")
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
        self.playback_status.configure(
            text="STOP запрошен; ожидаю закрытия потока..."
        )
        self.engine.stop()

    # ------------------------------------------------------------- phone

    def import_phone_wav(self) -> None:
        path = filedialog.askopenfilename(
            title="Импорт WAV с телефона",
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
            return
        expected_text = self.expected_var.get().strip().replace(",", ".")
        expected: float | None = None
        if expected_text:
            try:
                expected = float(expected_text)
            except ValueError:
                messagebox.showerror(
                    "Expected frequency",
                    f"Ожидается число, получено {self.expected_var.get()!r}",
                    parent=self.root,
                )
                return
        self.phone_status.configure(text="Анализ WAV...")
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
            messagebox.showerror(
                "Ошибка анализа WAV",
                f"{type(exc).__name__}: {exc}",
                parent=self.root,
            )
            return

        self.phone_record = record
        self.analysis_text.configure(state="normal")
        self.analysis_text.delete("1.0", "end")
        self.analysis_text.insert("1.0", desktop.format_phone_summary(record))
        self.analysis_text.configure(state="disabled")
        self.phone_status.configure(
            text=(
                f"Импортировано: {record.imported_path}\n"
                f"JSON: {record.json_path}\n"
                f"CSV: {record.csv_path}"
            )
        )
        self._draw_psd(record)
        self._show_spectrogram(record)

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
                10, 10, anchor="nw", text="PSD: недостаточно данных"
            )
            return
        canvas.update_idletasks()
        width = max(320, canvas.winfo_width())
        height = max(120, canvas.winfo_height())
        freqs = np.asarray(frequencies[1:], dtype=np.float64)
        density = np.asarray(psd[1:], dtype=np.float64)
        valid = (freqs > 0) & np.isfinite(density)
        freqs = freqs[valid]
        density = density[valid]
        if freqs.size < 2:
            canvas.create_text(10, 10, anchor="nw", text="PSD: нет данных")
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
            canvas.create_line(left, y, right, y, fill="#dddddd")
            canvas.create_text(
                2, y, anchor="w", text=f"{value:.0f}", font=("Segoe UI", 7)
            )
        for freq in (100, 1_000, 10_000, 20_000):
            if min_freq <= freq <= max_freq:
                x = x_of(float(freq))
                canvas.create_line(x, top, x, bottom, fill="#eeeeee")
                label = f"{freq // 1000}k" if freq >= 1_000 else f"{freq}"
                canvas.create_text(
                    x, height - 10, text=label, font=("Segoe UI", 7)
                )
        points: list[float] = []
        for freq, value in zip(freqs, db):
            points.extend((x_of(float(freq)), y_of(float(value))))
        if len(points) >= 4:
            canvas.create_line(*points, fill="#1f5f9f", width=1)
        canvas.create_text(
            left + 4,
            top + 2,
            anchor="nw",
            text=f"PSD: {record.imported_path.name}",
            font=("Segoe UI", 8, "bold"),
        )

    def _show_spectrogram(self, record: Any) -> None:
        path = record.spectrogram_path
        if path is None or not Path(path).is_file():
            self.spectrogram_label.configure(
                image="", text="Спектрограмма не создана."
            )
            self._spectrogram_image = None
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
            "SilentNetwork Acoustic Lab: тестовый WAV, ручной PLAY через "
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
