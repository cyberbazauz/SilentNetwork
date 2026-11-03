# SilentNetwork

Автономная акустическая система связи: данные передаются от динамика к
микрофону через звуковую волну. Интернет, Wi-Fi, Bluetooth и сотовая связь не
используются как транспорт данных и не являются резервным каналом.

**Текущее состояние: исследовательский этап.** Криптографии, протокола SCNP и
mesh-сети пока нет. Есть BFSK-тракт, Windows Desktop Lab и Android Audio Lab,
включая раздел передачи текста через звук. Первый приём Laptop → Phone
подтверждён на уровне файла: WAV и JSON с телефона независимо декодированы,
CRC-16 и текст совпали. Серия SN-028 ещё не начата; обратное направление ждёт
внешний микрофон. Добавлен Python-прототип файлового слоя v0: файл до 4096 байт
разбивается на пакеты MANIFEST/DATA/END, передаётся кадрами Frame v1 и
собирается обратно с проверкой SHA-256. В Windows Desktop Lab появились
карточки выбора файла, подтверждаемого PLAY и записи с микрофона. В Android
появилась карточка приёма файла с записью 15/30/60 с и экспортом файла и
JSON-отчёта. Это пока файловый round-trip через WAV и локальный UI, не
подтверждённая передача через воздух и не доказательство устойчивости канала.

## Что уже есть

| Область | Состояние |
|---|---|
| Нормативные документы проекта | есть (`AGENTS.md`, `TASK_BOARD.md`) |
| Аудит репозитория и окружения | выполнен: `docs/audit/2026-11-01-repository-audit.md` |
| Исходная архитектура v1.0 | каноническая копия в `docs/reference/` |
| Acoustic Lab: инфраструктура | есть и покрыта тестами (`acoustic-lab/snlab/`) |
| Acoustic Lab: сканер устройств и фона | реализован, реальный захват 10 с проверен (SN-010) |
| Acoustic Lab: BFSK-передатчик (WAV) | реализован, 20/20 одинаковых SHA-256 (SN-011) |
| Acoustic Lab: BFSK-приёмник (WAV) | реализован, файловый round-trip и проверка CRC (SN-021) |
| Acoustic Lab: файловый слой v0 | реализован: файл 0…4096 байт, пакеты MANIFEST/DATA/END, SHA-256, CLI и JSON-отчёты; Windows UI для генерации, PLAY и приёма; Android-карточка приёма файла (SN-034) |
| Acoustic Lab: BER/PER benchmark (AWGN) | реализован, M0-6 100/100 при 15 дБ (SN-013) |
| Windows Desktop Lab | реализован: WAV, PLAY через выбранный динамик, анализ записи телефона (SN-015/SN-016), передача и приём текста через звук (SN-025) |
| Android Audio Lab | реализован: запись, WAV/JSON, анализ и вердикт, передача и приём текста через звук (SN-026) |
| Standalone Windows EXE | собран `dist/SilentNetwork-Desktop.exe` (PyInstaller onefile, 66 МБ), smoke-test и запуск окна проверены |
| Реальная передача через воздух | Laptop → Phone: `FIRST CONTACT VERIFIED AT FILE LEVEL — SERIES NOT STARTED`; Phone → Laptop: `PENDING EXTERNAL MICROPHONE` |
| Rust Core, SCNP, криптография | не реализованы |

## Структура репозитория

```text
SilentNetwork/
├── AGENTS.md                     # правила Agent 1 (единственный агент)
├── PROMPT_AGENT_1.txt            # стартовый промпт Agent 1
├── TASK_BOARD.md                 # единый статус задач
├── .gitignore / .gitattributes
├── docs/
│   ├── audit/                    # аудиты состояния
│   ├── decisions/                # ADR (ADR-001 — кадр и BFSK)
│   ├── current-milestone.md      # текущий milestone и критерии приёмки
│   └── reference/                # канонические исходные документы
└── acoustic-lab/                 # Python-исследования и измерения
    ├── snlab/                    # профили, WAV I/O, метрики, AWGN, BFSK
    ├── tests/                    # pytest
    ├── scanner.py                # SN-010, реализован
    ├── transmitter.py            # SN-011, реализован
    ├── receiver.py               # SN-021, реализован
    ├── file_transfer.py          # SN-034, CLI файлового слоя
    └── benchmark.py              # SN-013, реализован
```

`docs/architecture.md`, `docs/protocol-spec.md`, `docs/security-model.md` и
`docs/android-core-api.md` ещё не созданы: они появятся по мере утверждения
соответствующих решений.

## Быстрый старт

Требуется Python 3.12 (проверено на 3.12.10, Windows).

```powershell
cd acoustic-lab
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest tests -q
```

Все тесты проверяют инфраструктуру (конфигурацию, WAV, метрики, AWGN и
активную область сигнала), анализаторы сканера, BFSK-передатчик, файловый
BFSK-приёмник, файловый слой и BER/PER benchmark; аудиобэкенд в тестах
подменяется фейком, реальный микрофон и динамик не открываются.

## Готовые сборки

Каталог `dist/` не входит в Git и собирается локально.

| Файл | Что это |
|---|---|
| `dist/SilentNetwork-Desktop.exe` | standalone Windows-приложение, Python не нужен |
| `dist/SilentNetwork-Android.apk` | debug-сборка Android-приложения |

**Windows.** Запустить `dist\SilentNetwork-Desktop.exe` двойным кликом. В
разделе «Передача данных» можно сформировать WAV с текстом, проиграть его через
выбранный динамик, записать микрофон и декодировать принятый кадр; WAV и JSON
сохраняются рядом с `.exe` в `results/hardware_calibration/`. Проверка без
открытия окна:

```powershell
dist\SilentNetwork-Desktop.exe --smoke-test
```

EXE собран PyInstaller 6.22.3 (onefile, windowed) из `acoustic-lab/desktop_app.py`.
Сборка воспроизводится командой из `docs/sn028-packaging.md`; `torch`, `pandas`,
`matplotlib` и тестовые пакеты исключены. EXE не подписан сертификатом.

**Android.** Установка на подключённый по USB телефон с отладкой:

```powershell
& "C:\Android\sdk\platform-tools\adb.exe" install -r "dist\SilentNetwork-Android.apk"
```

APK подписан debug-ключом, а не production-ключом. Приложение содержит те же
возможности: передача текста, запись, декодирование, экспорт WAV/JSON.

> Ни EXE, ни APK ещё не участвовали в документально подтверждённой передаче
> через воздух. Успешный smoke-test, запуск окна и файловый round-trip не
> являются доказательством акустической передачи.

**Разбор серии SN-028.** Экспортированные с телефона WAV раскладываются по
именам `sn028-w2p-<дистанция>-<M1..M4>-<номер>.wav` и проверяются пакетно:

```powershell
cd acoustic-lab
python sn028_series.py --dir <каталог> --distance 30cm
```

Отчёт CSV/JSON/Markdown сохраняется в `acoustic-lab/results/sn028/series/`.
Пороги: 30 см — 10/10, 1 м — ≥ 8/10, 3 м — исследовательски, шум — 0 ложных
кадров.

**Файловый слой v0.** Передача файла до 4096 байт и обратная сборка из WAV:

```powershell
cd acoustic-lab
python file_transfer.py send --file note.txt --out results\note.wav
python file_transfer.py receive --wav results\note.wav --out-dir results\received
```

`send` создаёт WAV и отчёт `<имя>.tx.json`, `receive` — файл и отчёт
`<имя>.rx.json`. Файл создаётся только при полном наборе чанков и совпадении
SHA-256. Это файловый канал L0: воспроизведение WAV в динамик и запись
микрофона выполняются отдельно и не считаются доказанными этим инструментом.
Контракт: `docs/acoustic-file-transfer.md`.

В `desktop_app.py` те же операции доступны через карточки «Передать файл» и
«Принять файл»: выбрать файл, создать WAV, подтвердить PLAY, записать
микрофон и увидеть имя, размер, SHA-256 и путь собранного файла. Отчёты
Desktop Lab используют отдельные схемы `file-transfer-tx`,
`file-transfer-playback` и `file-transfer-receive`; исходная схема контракта
сохраняется в `contract_schema`.

На Android в Audio Lab добавлена карточка «Файлы через звук»: профиль,
запись 15/30/60 с, приём Laptop → Phone, показ вердикта, имени, размера,
SHA-256 и экспорт принятого файла и JSON-отчёта. Кодек портирован из Python и
проверен JVM-тестами на точных векторах, round-trip, потере и повторе чанков,
конфликтах и SHA-256. Запись через воздух, 1 КиБ и 4 КиБ на Android ещё не
проверены; мобильная запись ограничена 60 с в этой карточке.

## Документы

- `docs/current-milestone.md` — milestone M0, 11 измеримых критериев и
  фактические результаты SN-013.
- `docs/sn028-physical-test-protocol.md` — протокол физического теста SN-028 и
  быстрый старт Laptop → Phone.
- `docs/sn028-stage-1-runbook.md` — точный порядок серии 30 см, 10 попыток.
- `docs/sn028-next-plan.md` — план дальнейших работ после первого успеха.
- `docs/acoustic-file-transfer.md` — контракт файлового слоя v0, пакеты,
  лимиты, CLI и дорожная карта.
- `acoustic-lab/results/sn028-first-contact.md` — независимая проверка первого
  принятого WAV/JSON.
- `docs/sn028-packaging.md` — сборка standalone Windows EXE.
- `acoustic-lab/results/M0-report.md` — отчёт BER/PER на AWGN и M0-6.
- `docs/decisions/ADR-001-acoustic-frame-v1.md` — формат кадра, CRC-16 и
  BFSK-модуляция Acoustic Lab.
- `docs/audit/2026-11-01-repository-audit.md` — состояние репозитория,
  окружения, риски и блокеры.
- `docs/reference/SilentNetwork_Architecture_v1.0.txt` — утверждённая
  архитектура, SHA-256 `A8849A52A260A07B1DB74438F9AA9B6458551A76065086DFE328E0525EE6E9D5`.
- `docs/reference/README.md` — правила работы с каноническими документами.

## Чего нельзя утверждать

- Что передача через воздух уже подтверждена серией измерений SN-028.
- Что файловый round-trip через WAV, smoke-test или успешный `send`/`receive`
  доказывают передачу через воздух.
- Что подтверждены дальность, скорость или неслышимость сигнала.
- Что работает на любом оборудовании.
- Что CRC или SHA-256 заменяют аутентифицированное шифрование.

Показатели из Architecture v1.0 (10–50 м, 5–15 кбит/с и др.) — целевые
гипотезы до стендовых измерений.

## Роли

- **Agent 1** — архитектура, Acoustic Lab, DSP/PHY, Rust Core, SCNP, mesh,
  Windows Desktop Lab, Android-приложение и интеграция.
- **Product Owner** — решения по требованиям, контрактам и приёмке этапов.

## Лицензия

Не выбрана. До публикации требуется решение Product Owner.
