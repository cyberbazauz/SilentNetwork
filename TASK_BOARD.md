# SilentNetwork — общая доска задач

**Версия:** 4.3
**Дата создания:** 2026-11-01
**Последнее обновление:** 2026-11-07 — Agent 1: исправлены две Android-ошибки (SN-037). Чат не отправлял звук: общий валидатор `DataLinkCodec`/`FileTransferCodec` принимал только `data-audible`/`data-robust`, тогда как Messenger использует `chat-normal`/`chat-excellent`/`chat-highfreq`; добавлен `BfskProfile.supportedProfiles()`, текст и файлы кодируются любым чат-профилем. В Audio Lab кнопка «Воспроизвести» в режиме «Сообщение» была отключена до ручного «Создать сообщение» и молча не реагировала; теперь она всегда активна и при необходимости кодирует текущий текст перед воспроизведением. Android unit-тесты **216** (108 debug + 108 release, failures 0, errors 0, skipped 2), включая 6 новых `ChatProfileCodecTest`; APK 3 734 143 байта, SHA-256 `277342375B014DEBF1A9D76C405FC4A0FBA6BD6E24F662D474E54DD6A0B68251`. Проверка на реальном телефоне не выполнялась. Ранее в этот день: SN-036, ART/1 встроен в Python-чат Windows Desktop Lab; Python **777 passed**; EXE 66 581 462 байта, SHA-256 `FD951AC3…9BEC1B2`, smoke-test exit 0. Передача через воздух, Android-порт ART/1 и кросс-проверка Python ↔ Kotlin не выполнялись; телефон не подключён (`adb devices` пуст)
**Владелец:** Product Owner

Этот файл — единый источник текущего статуса работ для Agent 1. Агент обновляет статус задачи и добавляет краткий отчёт. Не ставь `DONE`, пока критерии приёмки не проверены.

> **2026-11-02:** Gemini больше не участвует в проекте. Все задачи, ранее закреплённые за Gemini, переходят к Agent 1. Исторические записи сохранены без изменений.

## 1. Легенда статусов

- `BACKLOG` — задача запланирована, но не начата.
- `READY` — требования и входные данные определены, можно брать в работу.
- `IN_PROGRESS` — выполняется.
- `BLOCKED` — заблокирована; обязательно указать причину и владельца следующего действия.
- `REVIEW` — реализация готова, требуется проверка.
- `DONE` — критерии приёмки выполнены и подтверждены тестами.
- `DEFERRED` — сознательно отложена Product Owner.

## 2. Правила обновления

1. У каждой задачи есть ID и один ответственный агент.
2. Не назначай двум агентам одну и ту же задачу с пересекающимися файлами.
3. Перед началом задачи укажи ветку и файлы, которые будут изменяться.
4. Если меняется Core API, SCNP или формат аудиообмена, сначала обнови соответствующую спецификацию и согласуй её с Product Owner.
5. В комментарии указывай фактические тесты и результаты, а не ожидаемые.
6. Если задача заблокирована, не обходи проблему скрытой заглушкой; создай запрос другому агенту.

## 3. Текущая цель MVP

Первый подтверждённый MVP должен обеспечить:

- два устройства обмениваются текстом посредством звуковой волны;
- данные защищены аутентифицированным шифрованием;
- получатель проверяет целостность и подлинность;
- доставка подтверждается на транспортном уровне;
- для передачи данных не требуется интернет, Wi-Fi, Bluetooth или сотовая связь;
- реальная работа подтверждена повторяемыми тестами.

Начальная контрольная конфигурация — Windows ↔ Windows. Android подключается после появления стабильного акустического модема и документированного Core API. Mesh, передача файлов и дополнительные платформы не должны задерживать доказательство базового канала.

## 4. Backlog по этапам

### Этап 0 — Аудит и подготовка репозитория

| ID | Задача | Ответственный | Статус | Критерий приёмки / результат |
|---|---|---|---|---|
| SN-001 | Изучить фактическую структуру репозитория и сравнить с Architecture v1.0 | Agent 1 | REVIEW | Отчёт `docs/audit/2026-11-01-repository-audit.md`: кода нет, окружение проверено, риски и блокеры зафиксированы; ждёт проверки Product Owner |
| SN-002 | Проверить Android-окружение, Gradle и наличие приложения | Agent 1 | DONE | Аудит проведён (`docs/audit/android-environment-audit.md`): JDK 17 (`C:\jdk-17.0.2`), Android SDK 34/35/NDK 27 в наличии; тестовая сборка Debug APK успешно подтверждена |
| SN-003 | Зафиксировать текущую архитектуру, открытые вопросы и первый milestone | Agent 1 | IN_PROGRESS | M0 утверждён с условиями (Product Owner, 2026-11-01), 11 критериев ожидают рассмотрения; `docs/architecture.md` ещё не создан — нужно отдельное согласование структуры |
| SN-004 | Подготовить начальную Android app skeleton, если её ещё нет | Agent 1 | READY | Приложение собирается; экраны/заглушки явно помечены и не имитируют связь |
| SN-005 | Инициализировать git-репозиторий, ветку `agent1/...` и `.gitignore` | Agent 1 | DONE | Репозиторий создан: `main` + `agent1/m0-acoustic-lab`; `git check-ignore` подтверждает исключение результатов и аудио; `.gitattributes` сохраняет байты `docs/reference/` |
| SN-006 | Добавить `README.md` и скелет `docs/` | Agent 1 | DONE | `README.md` и структура `docs/` созданы; описано фактическое состояние, отсутствующие модули прямо названы нереализованными |
| SN-007 | Внести Architecture v1.0 в репозиторий как каноническую копию | Agent 1 + PO | DONE | `docs/reference/SilentNetwork_Architecture_v1.0.txt`; SHA-256 `A8849A52…E9D5` совпал с оригиналом до и после копирования |
| SN-008 | Каркас `acoustic-lab/`: `snlab/`, CLI-скрипты, pytest | Agent 1 | DONE | `snlab/{config,wavio,metrics,channel,cli}.py`, 4 CLI-заглушки с кодом 2, 52 теста проходят; DSP ещё не реализован (SN-020/SN-021) |
| SN-009 | `requirements.txt` и инструкция запуска окружения | Agent 1 | DONE | `acoustic-lab/requirements.txt` с точными версиями (numpy 1.26.4, scipy 1.13.1, sounddevice 0.5.6, matplotlib 3.11.2, pytest 9.1.1); инструкция в README |

### Этап 1 — Acoustic Lab и измерения

| ID | Задача | Ответственный | Статус | Критерий приёмки / результат |
|---|---|---|---|---|
| SN-010 | Проверка доступных аудиоустройств и параметров захвата | Agent 1 | DONE | `snlab/audio.py` + `scanner.py`: 26 endpoint'ов с host API, проверка частот/типов через драйвер, выбор устройства по индексу/имени, захват 10 с, RMS/peak/шумовой пол/PSD/полосы/клиппинг, WAV+JSON+CSV+PNG; 50 новых тестов (100 всего; 2 теста-заглушки scanner заменены); реальный захват 48 кГц/10 с проверен; SNR из фона не вычисляется (M0-4) |
| SN-011 | Генератор тестовых акустических сигналов (BFSK transmitter) | Agent 1 | DONE | `snlab/frame.py`, `snlab/bfsk.py`, `transmitter.py`: сообщение/файл → кадр (преамбула, sync `0x1ACF`, версия, длина, CRC-16) → CPFSK → PCM16 WAV + JSON; 20/20 запусков дали одинаковые PCM и WAV SHA-256; 53 новых теста (153 всего); реальное воспроизведение через динамик не выполнялось и не заявляется |
| SN-012 | Захват и анализ сигнала | Agent 1 | DONE | `snlab/calibration.py` + `calibrate.py`: тон-план, генерация, захват, уровень/SNR/смещение частоты/клиппинг, WAV+JSON+PNG; физический режим только с `--hardware-test`, безопасны `--list-devices` и `--analyze`; тесты на синтетике и фейковом аудиобэкенде. Реальный акустический PASS не заявляется: физическая часть — PENDING |
| SN-013 | Benchmark: BER, PER, goodput, задержка и потери | Agent 1 | DONE | `snlab/benchmark.py` + `benchmark.py`: 100 испытаний на каждую точку 0/5/10/15/20 дБ, AWGN по мощности активной области, диагностический BER до CRC, PER со всеми отказами, CSV/JSON/PNG/`M0-report.md`; 45 новых тестов (240 всего); M0-6 — 100/100 при 15 дБ. Goodput и задержка не измерялись: файловый канал не передаёт в реальном времени |
| SN-014 | Hardware Calibration: генератор тонов, анализатор WAV, частотная характеристика | Agent 1 | IN_PROGRESS | Код готов: `snlab/tones.py`, `snlab/analysis.py`, `snlab/response.py`, CLI `tonegen.py`, `analyze.py`, `response.py`; всё работает только с файлами, устройства не открываются без `--hardware-test`. План на 11 шагов — `docs/hardware-calibration/SN-014-TEST-PLAN.md`. Полный прогон: 400 passed. **Физические измерения — PENDING**: не выполнялись, PASS не заявляется |
| SN-015 | Windows Desktop Lab + Android Receiver: LAPTOP → PHONE, ручной PLAY и анализ WAV телефона | Agent 1 | REVIEW | Код готов и объединён: `desktop_app.py` (Tkinter), `snlab/desktop.py`, `snlab/playback.py`, `run_desktop.bat`; Android Receiver с PCM16 WAV, JSON, Test ID и фиксацией маршрута. PLAY после создания сигнала и подтверждения; с SN-016 доступен без `--hardware-test`, системный динамик выбирается автоматически. JSON только после фактического PLAY. Merge commit `aba35cc`. Прежние независимые прогоны: Python 439 passed, Android 56+56 = 112. После merge повторно: Python 439 passed, Android 112 (failures 0), APK `3 392 577` байт. Инструкция: `docs/hardware-calibration/SN-015-LAPTOP-TO-PHONE.md`; доставка: `docs/hardware-calibration/SN-015-FINAL-DELIVERY.md`. **Физическое воспроизведение и LAPTOP → PHONE — PENDING**: не выполнялись, PASS не заявляется |
| SN-016 | Редизайн Windows Desktop Lab и Android UI в едином светлом стиле | Agent 1 | REVIEW | Выполнен без изменения DSP, кадра, CRC, WAV/JSON, Test ID и lifecycle. Windows: две колонки, «Динамик ноутбука», «Отправить тестовый сигнал», «Проверить запись телефона», человеческий вердикт, компактные метрики, прокрутка при раскрытии «Подробнее». PLAY доступен без `--hardware-test` после создания сигнала; системный динамик выбирается автоматически; перед каждым PLAY — подтверждение. Android: светлая тема, Status без фейковых состояний, Audio Lab с крупной записью и авто-анализом, передатчик вынесен во второстепенный раздел. Python `441 passed`; GUI smoke-test FILE-ONLY и `--hardware-test` — `play_enabled=False` (сигнал не создан); Android `118` запусков (59 debug + 59 release), failures/errors 0; `assembleDebug` успешен. Описание: `docs/UI-REDESIGN-SN-016.md`. Windows-скриншоты сняты локально и не коммитятся; **Android-скриншоты — BLOCKED**: нет эмулятора/устройства. Физический аудиотест — `PENDING`. **2026-11-04, второй проход:** Windows получил DPI-aware отрисовку (Per-Monitor V2, `tk scaling`, масштабируемые отступы) и вкладки «Файл», «Сообщение», «Тон и анализ», «Устройства» вместо одной страницы с шестью карточками; прокрутка включается только при нехватке высоты. Android: шапка сжата до 52 dp, в Audio Lab добавлен переключатель «Файл / Сообщение / Тон» с одним активным сценарием; файл остаётся основным, тексты про отсутствие ACK/шифрования и SHA-256 сохранены; лимит файла позже поднят до 8 КиБ в SN-034. Исправлены ложные тексты настроек про 18–19 кГц и XChaCha20-Poly1305/Ed25519. Проверки: Python 602 passed, desktop 88 passed, smoke-test исходников и dist — OK; Android 91 + 91, failures/errors 0, skipped 1; `assembleDebug` успешен; APK 3 604 912 байт, SHA-256 `F5238AEC…C9D6FB`. Детали — раздел 10 `docs/UI-REDESIGN-SN-016.md`. Визуальная проверка Android и физическая передача — `PENDING` |

### Этап 2 — Первый акустический модем

| ID | Задача | Ответственный | Статус | Критерий приёмки / результат |
|---|---|---|---|---|
| SN-020 | Реализовать BFSK transmitter | Agent 1 | DONE | Выполнено в рамках SN-011 по указанию Product Owner: детерминированные тесты кодирования и генерации сигнала проходят; отдельного дублирующего кода нет |
| SN-021 | Реализовать BFSK receiver и синхронизацию | Agent 1 | DONE | `snlab/receiver.py` + `receiver.py`: поиск преамбулы 32/32, уточнение начала, некогерентная демодуляция CPFSK, проверка sync word/версии/длины/CRC-16, JSON-отчёт и коды 0/2/3/4; 44 новых теста, 2 заглушки receiver удалены (195 всего); round-trip проверен на тексте, UTF-8, бинарных данных, длинах 1…512, начальной тишине и втором кадре |
| SN-022 | Реальные испытания Windows ↔ Windows | Agent 1 | BACKLOG | Сообщения передаются по акустике; сохранены настройки, BER/PER и условия теста |
| SN-023 | Исследовать диапазоны и профили на нескольких устройствах | Agent 1 | BACKLOG | Измерительный отчёт; слышимость и ограничения документированы |
| SN-024 | Контракт Acoustic Data Link v0 и кросс-языковые тестовые векторы | Agent 1 | DONE | `docs/acoustic-data-link.md`, профили `data-audible`/`data-robust` в `config.py` (DEFAULT_PROFILE без изменения), `snlab/datalink.py`, `receiver.py` с `min_preamble_matches` (по умолчанию 32/32), векторы `tests/fixtures/data-link-vectors.json` и копия в `apps/android/app/src/test/resources/`; тесты `tests/test_datalink.py` (24 теста) + обновлённые `test_config.py`; `471 passed`. Frame v1 и CRC не менялись. Кросс-языковая совместимость — по декодированию. |
| SN-025 | Windows Desktop Lab: передача и приём текста через звук | Agent 1 | DONE | `snlab/datalink_desktop.py` + карточки в `desktop_app.py`: подтверждение PLAY, запись микрофона по кнопке, декодирование, человекочитаемый вердикт, WAV/JSON (playback/receive схемы), фейковые тесты; `tests/test_sn025_datalink_desktop.py` (17 тестов); `488 passed`. Физический воздух-тест — SN-028. |
| SN-026 | Android: BFSK-модем, Frame v1 и раздел «Передача данных» | Agent 1 | DONE | Kotlin-модулятор/демодулятор, CRC-16, кодек текста, запись и воспроизведение, результат с CRC; unit-тесты `DataLinkVectorsTest`/`DataLinkStateTest`; `test --rerun-tasks` — 73 debug + 73 release, failures 0; `assembleDebug` — BUILD SUCCESSFUL; APK `dist/silentnetwork-android-debug.apk`. Физический воздух — SN-028. |
| SN-027 | Автоматическая кросс-проверка Python ↔ Kotlin | Agent 1 | DONE | `acoustic-lab/cross_check_android.py` + `CrossLanguageTest` (запуск по `-PsnCrosscheckDir`): 4 кейса (ASCII, кириллица, mixed, 64 байта) в обе стороны, преамбула 32/32, CRC совпал. Отчёт `acoustic-lab/results/sn027-cross-check.md`. |
| SN-028 | Физический тест передачи данных и отчёт | Agent 1 | IN_PROGRESS | **Laptop → Phone:** `FIRST CONTACT VERIFIED AT FILE LEVEL — SERIES NOT STARTED`. Product Owner сохранил `reports/datalink_rx_data-audible.wav` и JSON; Agent 1 независимо проверил: SHA-256 `aed02e7b…a00a42` совпал, кадр Frame v1, CRC-16 `0xD8B5`, текст `SILENTNETWORK`, преамбула 32/32, 5.0 с, 48 кГц PCM16 mono, RMS −45.37 dBFS, пик −26.84 dBFS, клиппинг 0, старт 2.393 с; контраст «кадр/фон» ≈24–26 дБ. Это не серия 30 см: нет дистанции, громкости, модели телефона и помещения, текст не M1–M3, запись 5 с. **Phone → Laptop:** `PENDING EXTERNAL MICROPHONE` — встроенный микрофон ноутбука не работает, в статистику не входит. Добавлены `acoustic-lab/sn028_series.py` (пакетный разбор, пороги 30 см 10/10, 1 м ≥ 8/10, 3 м исследовательски, шум 0 ложных кадров), `docs/sn028-next-plan.md` и `docs/sn028-stage-1-runbook.md`. Исправлен JSON Android: `min_preamble_matches` пишет порог 28, а не фактические 32; Android 74 debug + 74 release, failures 0; APK `dist/SilentNetwork-Android.apk` 3 520 640 байт, SHA-256 `c4384015…f18034`. Python 512 passed. |
| SN-029 | Standalone Windows EXE: отправка, приём и декодирование | Agent 1 | DONE | `dist/SilentNetwork-Desktop.exe` собран PyInstaller 6.22.3 (onefile, windowed) из `acoustic-lab/desktop_app.py`; исключены `torch`, `pandas`, `matplotlib`, тестовые пакеты — размер 66 498 521 байт вместо 234 873 087, SHA-256 `E925CD0AC6857F57E01C3C15611091E4436F059B05112AA192CF8658CB456990`. Результаты в frozen-режиме пишутся рядом с `.exe` (`snlab/desktop.py`, `snlab/datalink_desktop.py`), тесты `tests/test_sn028_packaging.py`. Фактически: smoke-test exit 0, окно «SilentNetwork — Акустическая лаборатория» открывается, `608 passed`. EXE включает файловый слой v0 до 8192 байт. Инструкция: `docs/sn028-packaging.md`. Передача через воздух не выполнялась. |
| SN-034 | Файловый слой v0 над Frame v1: файл 0…8192 байт | Agent 1 | REVIEW | Контракт `docs/acoustic-file-transfer.md` (PROPOSED): пакет 10 байт заголовка + до 54 байт payload, MANIFEST/DATA/END, повтор MANIFEST после 16 чанков, END ×2, `transfer_id` 0…255, SHA-256 всего файла. Реализация `acoustic-lab/snlab/file_transfer.py` и CLI `acoustic-lab/file_transfer.py` (`send`/`receive`, JSON `<имя>.tx.json`/`.rx.json`, коды 0/2/3/4). Векторы `tests/fixtures/file-transfer-vectors.json`; 72 теста. Этап 1: `snlab/file_transfer_desktop.py` и карточки «Передать файл»/«Принять файл» в `desktop_app.py`; 19 тестов backend и GUI. Всего Python 608 passed. Этап 3: Kotlin-порт `audiolab/datalink/FileTransferCodec.kt` (packet codec, assembler, plan, encode/decode WAV, multi-frame ресинхронизация), offset-aware `BfskDemodulator.findFrameStart(from)`, карточка «Файлы через звук» в `AudioLabFragment` (профиль, запись 15/30/60/120/240/420 с, подсказка о длинной записи, вердикт, метрики, экспорт файла и JSON), 17 JVM-тестов на точных векторах, round-trip, потерю/повтор чанков, конфликты, SHA-256 и отчёты. Фактически: 100 байт → 5 пакетов, 10.42 с WAV; 4096 байт → 83 пакета, 181.9 с; 8192 байта → 164 пакета, ≈364 с, SHA-256 совпал на L0. Android 94 debug + 94 release, failures 0, errors 0, skipped 1; `assembleDebug` успешен; APK 3 567 860 байт, SHA-256 `90633FFE28D72F76423651FE87B2411A2C8C1F45C32973D957945B5ED1090C0C`. Кросс-проверка Python ↔ Kotlin расширена файловыми кейсами (текст 40 Б, кириллическое имя 23 Б, 120 Б / 3 чанка, пустой файл): 8/8 PASS в обе стороны, отчёт `acoustic-lab/results/sn027-cross-check.md`. **Не сделано:** передача файла через воздух, Android UI-проверка на устройстве, ACK и шифрование. Ожидает одобрения Product Owner. |
| SN-035 | Непрерывный акустический чат поверх Frame v1 (Windows и Android) | Agent 1 | REVIEW | Python `snlab/chat.py`: `StreamingFrameDecoder`, `ChatSession`, события текста/файла, `encode_text_message`/`encode_file_message`; `snlab/audio.py`: `ContinuousCapture` с bounded queue, pause/resume и `on_gap`. Windows: вкладка «Чат» — авто-прослушивание при входе, отправка текста/файла сразу в PLAY, полудуплекс, лог событий. Android: `audiolab/chat/ChatSession.kt`, `StreamFrameDecoder.kt`, `ContinuousAudioCapture.kt`, `MessengerFragment` — авто-старт после разрешения `RECORD_AUDIO`, отправка текста/файла, сохранение принятых файлов в `received/rx-<millis>-<transferId>/`. Payload с префиксом `SF` зарезервирован под файловый пакет; текст с таким префиксом отклоняется. При разрыве потока сбрасывается только PCM-буфер, принятые файловые чанки сохраняются. Тесты: `test_chat.py` 32, `test_sn035_chat_desktop.py` 11; Python 651 passed; Android 204 запуска (102 debug + 102 release, failures 0, errors 0, skipped 2); кросс-проверка Python ↔ Kotlin 8/8; `assembleDebug` успешен; APK 3 624 071 байт, SHA-256 `C9C6684E…DACFF7`; EXE 66 530 850 байт, SHA-256 `5B1C3ED6…DCB6A0`, smoke-test exit 0. **Исправлено 2026-11-05:** старый выбор «лучшего» кандидата преамбулы с окном ±4 символа мог исключить настоящий старт кадра, а `SyncWordError` от более раннего ложного кандидата побеждал настоящий неполный кадр — потоковый декодер пересинхронизировался через кадр и терял текст/файловые пакеты на границе блоков микрофона. Теперь кандидаты перебираются по возрастанию позиции, а `IncompleteFrame` сохраняется до прихода остатка; исправление применено в Python (`receiver.py`, `chat.py`, `file_transfer.py`) и Kotlin (`BfskDemodulator.kt`, `StreamFrameDecoder.kt`, `FileTransferCodec.kt`). Кодирование Android переведено с `sin()` на фазорную рекурсию: текст 64 байта 4.2 → 1.2 мс, файл 8192 байта 290–325 → 109–111 мс (≈2.7–3.4×). Добавлены регрессии: периодический префикс, шумный разрезанный кадр, файлы 0/1/40/200/1000 байт блоками микрофона. **Не проверено:** работа на реальном телефоне, чат через воздух, Phone → Laptop (`PENDING EXTERNAL MICROPHONE`), память при файле 8192 байта. |
| SN-036 | ART/1 в Python-чате: подтверждение доставки и выборочный повтор | Agent 1 | REVIEW | Интеграция `snlab/reliable.py` в непрерывный чат Windows Desktop Lab. `snlab/reliable_chat.py`: распознавание ART/1 по версии 2 в payload (`SF 02`), `config_for_profile` (тайминги от символьной скорости), кодирование раунда/ответа в PCM, `ReliableSendController` с `start`/`tick`/`handle_packet`/`stop`. `snlab/chat.py`: `ChatSession(enable_transport=True, transport_config=...)`, события `TransportStarted/Progress/Completed/Aborted/PacketSeen/ReplyDue`, `poll_transport()`, сохранение файла в `results/chat-received/art-<millis>-<transferId>/`, выдача текста UTF-8. `desktop_app.py`: чекбокс «Подтверждать доставку (ART/1)» (по умолчанию выключен), пауза микрофона на время раунда, автоматический ответ `STATUS`/`ABORT`, прогресс по чанкам, отмена, блокировка смены режима/чекбокса во время передачи. Обычный текст и File Transfer v0 не изменены; Frame v1, CRC-16, BFSK и лимит payload 64 байта не тронуты. Фактически: `test_reliable.py` 107, `test_reliable_chat.py` 12, `test_sn036_reliable_desktop.py` 7, `test_sn035_chat_desktop.py` 11; полный Python-набор **777 passed**. Артефакты пересобраны 2026-11-07 из текущих исходников: EXE 66 581 462 байта, SHA-256 `FD951AC34599C1959F9511844575B5FE03C6B07CD57ADF5E5129FB6AE9BEC1B2`, `--smoke-test` exit 0, в архив входят `snlab.reliable`/`snlab.reliable_chat`; APK 3 734 143 байта, SHA-256 `5E7FF916741CC047C2ABA249AAAF04DB85701A170A1729B85DCF8E74956B052C`, Android unit-тесты 204 (failures 0, errors 0, skipped 2); позже в SN-037 APK пересобран с исправлением Android-чата — SHA-256 `277342375B014DEBF1A9D76C405FC4A0FBA6BD6E24F662D474E54DD6A0B68251`, Android 216. **Не проверено:** передача ART/1 через воздух, реальные потери `STATUS`, эхо/AGC, Android-порт ART/1, кросс-проверка Python ↔ Kotlin, AEAD/аутентификация. Документы: `docs/acoustic-reliable-transport.md` §13, `docs/acoustic-chat.md` §2.4, `docs/sn028-packaging.md`. |
| SN-037 | Android: отправка чата профилями `chat-*` и кнопка «Воспроизвести» в Audio Lab | Agent 1 | REVIEW | Причина ошибки чата: `DataLinkCodec.validateProfile` и `FileTransferCodec.validateDataProfile` принимали только `data-audible`/`data-robust`, а Messenger использует `chat-normal`/`chat-excellent`/`chat-highfreq`; каждый текст и файл отклонялся с `profile_invalid` до генерации PCM. Добавлен `BfskProfile.supportedProfiles()` (data + chat), оба валидатора переведены на него, текст ошибки перечисляет допустимые профили. Причина Audio Lab: `btn_datalink_play` был `android:enabled="false"` до ручного «Создать сообщение» и не давал обратной связи. Теперь кнопка всегда активна; `showDataLinkPlayDialog()` при отсутствии сообщения или после изменения текста/профиля кодирует текущий текст и только затем показывает диалог, пустой текст даёт явную ошибку. Frame v1, CRC-16, BFSK, лимит 64 байта и data-профили Audio Lab не изменены. Тесты: `ChatProfileCodecTest` — 6 (текст всеми тремя `chat-*`, файл на `chat-normal`, отказ неизвестному профилю, состав `supportedProfiles`); Android unit-тесты **216** (108 debug + 108 release, failures 0, errors 0, skipped 2); `assembleDebug` успешен; APK 3 734 143 байта, SHA-256 `277342375B014DEBF1A9D76C405FC4A0FBA6BD6E24F662D474E54DD6A0B68251`. **Не проверено:** работа на реальном телефоне, фактическая слышимость/приём `chat-*`, ручной сценарий кнопки на устройстве. |

### Этап 3 — Security, Identity и SCNP

| ID | Задача | Ответственный | Статус | Критерий приёмки / результат |
|---|---|---|---|---|
| SN-030 | Модель угроз и схема идентичности устройств | Agent 1 | BACKLOG | `docs/security-model.md` и тестируемые требования |
| SN-031 | Реализовать сессионные ключи и AEAD на проверенной библиотеке | Agent 1 | BACKLOG | Положительные и отрицательные crypto-тесты; секреты не попадают в логи |
| SN-032 | Зафиксировать SCNP wire format v1 | Agent 1 | BACKLOG | `docs/protocol-spec.md`, ограничения длин, endian, nonce, versioning и test vectors |
| SN-033 | ACK/NACK, sequence, replay protection и retransmission | Agent 1 | REVIEW | Контракт `docs/acoustic-reliable-transport.md` (PROPOSED): ART/1 поверх Frame v1 без изменения кадра, CRC-16 и BFSK. Заголовок 12 байт, чанк 52 байта, сообщение 0…8192 байт (0…158 чанков), `MANIFEST`/`DATA`/`STATUS`/`META`/`ABORT`, полудуплексные раунды, ACK/NACK-карта всех чанков, выборочный повтор только отсутствующих, `session_id`/`transfer_id`, защита от устаревших статусов, дубликатов и некорректных полей, SHA-256 всего блока, лимиты раундов и таймауты. Реализация: `acoustic-lab/snlab/reliable.py`, `acoustic-lab/snlab/reliable_sim.py`, CLI `acoustic-lab/reliable_sim.py`, векторы `tests/fixtures/reliable-transport-vectors.json`, тесты `tests/test_reliable.py` — 107 тестов. Фактически: полный Python-набор **758 passed**; CLI 500 байт при потере 20 % DATA и 20 % STATUS, seed 7 — 5 раундов, 19 DATA-кадров (9 повторов), 3 STATUS, SHA-256 совпал, модельное время 63.0 с. Проверен только модельный канал: DSP, аудиоустройства, воздух, Kotlin-порт и UI не задействованы. Реплей-защита ограничена `session_id`/`transfer_id` и номером раунда; AEAD/аутентификации нет. Нужно одобрение Product Owner до интеграции в чат/файловый режим. |

### Этап 4 — Android Core Integration

| ID | Задача | Ответственный | Статус | Критерий приёмки / результат |
|---|---|---|---|---|
| SN-040 | Определить способ Rust ↔ Android интеграции и Core API | Agent 1 | BACKLOG | `docs/android-core-api.md` с точными типами, аудиоформатом, потоками, ошибками и версией |
| SN-041 | Реализовать Android AudioRecord/AudioTrack adapter по контракту | Agent 1 | BACKLOG | Работа с PCM и lifecycle; тесты разрешений, запуска, остановки и ошибок |
| SN-042 | Подключить Core API в Kotlin | Agent 1 | BACKLOG | Реальные вызовы Core без заглушек; обрабатываются события и ошибки |
| SN-043 | Проверить Android ↔ Windows акустическую передачу | Agent 1 | BACKLOG | Реальный end-to-end тест с подтверждением доставки и измерениями |

### Этап 5 — Mesh Network

| ID | Задача | Ответственный | Статус | Критерий приёмки / результат |
|---|---|---|---|---|
| SN-050 | Discovery и таблица соседей | Agent 1 | BACKLOG | Повторяемое обнаружение и timeout/expiry тесты |
| SN-051 | Direct routing и поиск маршрута | Agent 1 | BACKLOG | Маршрут строится и восстанавливается после разрыва связи |
| SN-052 | Relay и TTL | Agent 1 | BACKLOG | Корректная ретрансляция зашифрованных пакетов и защита от циклов |
| SN-053 | Store-and-Forward с ограничениями ресурсов | Agent 1 | BACKLOG | TTL хранения, лимиты, удаление и защита от дублей протестированы |
| SN-054 | Android UI для соседей и состояния маршрута | Agent 1 | BACKLOG | UI отображает фактические события Core, без придуманного статуса |

### Этап 6 — Messenger и выпуск

| ID | Задача | Ответственный | Статус | Критерий приёмки / результат |
|---|---|---|---|---|
| SN-060 | Android Messenger UI и история | Agent 1 | BACKLOG | UI работает с реальными Core events; состояния отправки различаются |
| SN-061 | Передача файлов через утверждённый transport API | Agent 1 | BACKLOG | Фрагментация, выборочные повторы и UI прогресса проверены |
| SN-062 | Итоговые security и interoperability tests | Agent 1 | BACKLOG | Протокол, crypto, replay, malformed packets и cross-platform сценарии проверены |
| SN-063 | Release APK и инструкции установки | Agent 1 | BACKLOG | Release build создан; подпись и секреты обрабатываются безопасно; инструкция проверена |

## 5. Текущая активная работа

**Обновлено 2026-11-07 (Agent 1, SN-037 исправление Android-чата и Audio Lab):**

- Причина №1: `DataLinkCodec.validateProfile` и `FileTransferCodec.validateDataProfile` проверяли только `BfskProfile.dataProfiles()`. Messenger отправляет `CHAT_NORMAL`/`CHAT_EXCELLENT`/`CHAT_HIGH_FREQ`, поэтому `DataLinkCodec.encodeToPcm` и `FileTransferCodec.encodeFileToPcm` бросали `profile_invalid` («профиль должен быть data-audible или data-robust») ещё до звука. Добавлен общий `BfskProfile.supportedProfiles()` (data + chat); оба валидатора используют его, текст ошибки перечисляет известные профили.
- Причина №2: в `fragment_audio_lab.xml` у `btn_datalink_play` стоял `android:enabled="false"`, а включался он только после ручного «Создать сообщение». Если сообщение не создано, нажатие не давало никакой реакции. Кнопка теперь активна всегда: `showDataLinkPlayDialog()` кодирует текущий текст, если сообщения ещё нет или текст/профиль изменились, и только затем показывает диалог; пустой текст даёт явную ошибку, а не молчание.
- Тесты: новый `ChatProfileCodecTest` — 6 passed (текст всеми тремя `chat-*` через `DataLinkCodec`, файл 40 байт на `chat-normal` через `FileTransferCodec`, отказ неизвестному профилю в обоих кодеках, состав `supportedProfiles`). Android unit-тесты `testDebugUnitTest testReleaseUnitTest` — **216** запусков (108 debug + 108 release), failures 0, errors 0, skipped 2.
- Сборка: `assembleDebug` — BUILD SUCCESSFUL; `dist/SilentNetwork-Android.apk` пересобран, 3 734 143 байта, SHA-256 `277342375B014DEBF1A9D76C405FC4A0FBA6BD6E24F662D474E54DD6A0B68251`.
- Не изменены: Frame v1, CRC-16/CCITT-FALSE, BFSK/CPFSK, лимит payload 64 байта, data-профили Audio Lab. Windows/Python-код не менялся, Python-тесты в этот раз не перезапускались.
- **Не проверено:** работа на реальном телефоне (`adb devices` пуст), фактическая передача через воздух, слышимость/приём `chat-highfreq`, ручное нажатие кнопки на устройстве. Автотесты используют JVM-кодеки и не доказывают акустическую доставку.

**Обновлено 2026-11-07 (Agent 1, SN-036 ART/1 в Python-чате):**

- `snlab/reliable_chat.py` — слой между ART/1 и чатом: `is_transport_payload`/`parse_transport_payload` (ART/1 отличается от File Transfer v0 байтом версии `SF 02` против `SF 01`), `config_for_profile` (тайминги раунда и ответа от символьной скорости), `encode_transport_packets/round/reply`, `ReliableSendController` с `start`/`tick`/`handle_packet`/`stop`, `SendAction`, `TransportAudio`.
- `snlab/chat.py` — `ChatSession(enable_transport=False, transport_config=None)`, события `TransportStarted/Progress/Completed/Aborted/PacketSeen/ReplyDue`, `poll_transport()`. Принятый ART/1-текст выдаётся как UTF-8, файл сохраняется в `results/chat-received/art-<millis>-<transferId>/`; при выключенном режиме пакет отклоняется с `transport_disabled`, обычный приём продолжается.
- `desktop_app.py` — чекбокс «Подтверждать доставку (ART/1)» (по умолчанию выключен), стиль `Card.TCheckbutton`. Отправка текста/файла идёт раундами; микрофон ставится на паузу на время звука; `STATUS`/`ABORT` принимаются из общего потока и автоматически воспроизводятся; прогресс показывается в статусе; «Стоп» отменяет передачу; смена режима и чекбокса во время передачи запрещена; остановка прослушивания прерывает ART/1.
- Ограничения: ART/1 включается только при активном прослушивании микрофона (иначе нет канала для `STATUS`); лимит — 8192 байта; текст, начинающийся с `SF`, отклоняется; SHA-256/CRC-16 не аутентифицируют отправителя.
- Тесты: `tests/test_reliable_chat.py` — 12, `tests/test_sn036_reliable_desktop.py` — 7 (включая полный обмен «раунд → `STATUS` → подтверждение» на синтетическом PCM и отсутствие ложного успеха без `STATUS`), `tests/test_reliable.py` — 107, `tests/test_sn035_chat_desktop.py` — 11.
- Фактические проверки: `python -m pytest -q` — **777 passed** (111.53 с). GUI-тесты используют `FakeCapture`/`FakePlaybackEngine`, физический звук не задействован.
- Артефакты пересобраны 2026-11-07: `dist/SilentNetwork-Desktop.exe` — 66 581 462 байта, SHA-256 `FD951AC34599C1959F9511844575B5FE03C6B07CD57ADF5E5129FB6AE9BEC1B2`, `--smoke-test` exit 0, в архив входят `snlab.reliable` и `snlab.reliable_chat`. `dist/SilentNetwork-Android.apk` — 3 734 143 байта, SHA-256 `5E7FF916741CC047C2ABA249AAAF04DB85701A170A1729B85DCF8E74956B052C`; Android unit-тесты `testDebugUnitTest testReleaseUnitTest` — **204** запуска (102 debug + 102 release), failures 0, errors 0, skipped 2. Позже в SN-037 APK пересобран: SHA-256 `277342375B014DEBF1A9D76C405FC4A0FBA6BD6E24F662D474E54DD6A0B68251`, Android 216. Предыдущий APK в `dist/` был сборкой 2026-11-04 (`F5189067…`) и не включал последние Kotlin-изменения; теперь `dist/` соответствует текущим исходникам.
- **Не проверено:** передача ART/1 через воздух и реальные потери `STATUS`, число раундов и фактическое время, эхо/AGC/шумоподавление, Android-порт ART/1 и кросс-проверка Python ↔ Kotlin, AEAD/аутентификация, длительная работа и утечки. Документы: `docs/acoustic-reliable-transport.md` §13, `docs/acoustic-chat.md` §2.4.

**Обновлено 2026-11-06 (Agent 1, SN-033 ART/1 — эталонная логика и модельные тесты):**

- Добавлен контракт `docs/acoustic-reliable-transport.md`: `MANIFEST` + до 15 `DATA` + пауза + `STATUS` (ACK/NACK-битовая карта всех чанков) + выборочный повтор только отсутствующих. Frame v1, CRC-16, BFSK/CPFSK, профили и лимит payload 64 байта не изменены.
- Реализация: `acoustic-lab/snlab/reliable.py` (`TransportPacket`, `Manifest`, `StatusInfo`, `AbortInfo`, битовая карта, `ReliableSender`, `ReliableReceiver`), `acoustic-lab/snlab/reliable_sim.py` (детерминированные потери DATA/STATUS и дубликаты), CLI `acoustic-lab/reliable_sim.py` с JSON-отчётом и кодами 0/2/4.
- Тесты: `tests/test_reliable.py` — 107 тестов, `tests/fixtures/reliable-transport-vectors.json` — 5 векторов пакетов для будущей кросс-проверки. Покрыты round-trip формата, MSB-first карта, чистая передача, повтор только потерянных чанков, потеря `STATUS` и `MANIFEST`, дубликаты, `META`, SHA-256 mismatch, конфликты манифеста/имени, ненулевое дополнение, устаревшие и противоречивые статусы, `ack_timeout`, `confirmation_timeout`, `round_limit`, превышение размера и CLI.
- Дефекты, найденные тестами и исправленные: внутренние тексты `ABORT` длиннее 40 байт UTF-8 вызывали исключение вместо отправки `ABORT` (заменены на короткие `"hash mismatch"`, `"padding invalid"`, `"manifest conflict"`, `"meta conflict"`); симулятор падал после первого таймаута `STATUS` из-за ожидания готового раунда (теперь корректно пережидает `turnaround`).
- Фактические проверки: `python -m pytest tests/test_reliable.py -q` — **107 passed**; полный `python -m pytest -q` — **758 passed**; CLI 120 байт без потерь — 1 раунд, 9.7 с; CLI 500 байт, потери 20 % DATA и 20 % STATUS, seed 7 — 5 раундов, 19 DATA-кадров (9 повторов), 3 полученных `STATUS`, 5 потерянных DATA и 1 потерянный `STATUS`, SHA-256 совпал, 63.0 с модельного времени.
- **Не проверено:** реальная передача через воздух, DSP-интеграция, аудиоустройства, полудуплекс с эхом, Android/Kotlin-порт, UI-интеграция в чат/файловый режим, replay-защита с аутентификацией и AEAD. Успешный прогон симулятора не является доказательством акустической доставки.

**Обновлено 2026-11-05 (Agent 1, SN-035 исправление декодирования и скорости):**

- Найдены две причины потери сообщений в реальном времени по коду, без воздушных измерений. Первая: `find_frame_candidates` возвращает и ложный кандидат периодической преамбулы `1010…` на два символа раньше настоящего старта. Старый код выбирал кандидата с лучшим совпадением и оставлял только окно ±4 символа вокруг него; настоящий старт мог оказаться вне окна, и кадр не декодировался. Вторая: `decode_candidates` возвращал первую ошибку, поэтому `SyncWordError` от ложного кандидата побеждал `IncompleteFrame` настоящего кадра, разрезанного границей блока; сканер перепрыгивал через кадр.
- Исправление: `receiver.py::decode_candidates` запоминает `IncompleteFrame` и отдаёт его приоритет; `chat.py::StreamingFrameDecoder`, `file_transfer.py::decode_packets_from_samples`, Kotlin `StreamFrameDecoder`, `FileTransferCodec` и `BfskDemodulator.decodeCandidates` перебирают до 32 кандидатов в порядке возрастания позиции, а CRC-16 остаётся арбитром. Frame v1, CRC-16, профили, payload 64 байта и File Transfer v0 не изменены.
- Ускорено кодирование Android: `BfskModulator.bitsToSamples` больше не вызывает `sin()` на каждый отсчёт, а продвигает кэшированную единичную фазору умножением с перенормировкой раз в символ; добавлен `applyRampInPlace(FloatArray, Int)`. Разовый замер на desktop JVM: текст 64 байта 4.2 → 1.2 мс; файл 100 Б 30 → 4–6 мс; 1000 Б 57–68 → 16–17 мс; 4096 Б 145–227 → 62–66 мс; 8192 Б 290–325 → 109–111 мс.
- Регрессии: `test_chat.py` дополнен потоком с периодическим префиксом, шумным разрезанным кадром, файлом 1000 байт блоками по 512 и файлами 0/1/40/200 байт блоками по 4096. Kotlin `ChatSessionTest` дополнен теми же сценариями блочного потока и файла. Временный JVM-бенчмарк после замера удалён.
- Фактические проверки: `tests/test_chat.py` — 32 passed, `test_sn035_chat_desktop.py` — 11 passed, полный Python-набор — **651 passed**. Android — **204** запуска (102 debug + 102 release), failures 0, errors 0, skipped 2. Кросс-проверка Python ↔ Kotlin — 8/8 PASS. `assembleDebug` — BUILD SUCCESSFUL, APK 3 624 071 байт, SHA-256 `C9C6684E07AEFF7E165123B49CB1CBF751C4BEE15983E9B9D1B9D9DFC7DACFF7`. EXE пересобран: 66 530 850 байт, SHA-256 `5B1C3ED6C8110A08469ABCB81F394A5F99261C1ED358623A01973C199BDCB6A0`, `--smoke-test` exit 0.
- Пересмотрены пути воспроизведения: Python `SoundDevicePlaybackEngine` пишет блоки и закрывает поток только после `Pa_StopStream`, Android `AndroidAudioPlayerEngine` ждёт `playbackHeadPosition >= totalSamples`. Кодового дефекта, обрезающего хвост сообщения, не найдено; изменения в эти файлы не вносились.
- **Не проверено:** работа на реальном телефоне и передача через воздух; Phone → Laptop ждёт внешний микрофон; приём файла 8192 байта и пик памяти; длительная работа без утечек; реальный эхотракт. Эти проверки нельзя заменить синтетикой. Документ: `docs/acoustic-chat.md`.

**Обновлено 2026-11-04 (Agent 1, SN-035 непрерывный чат):**

- Python-ядро: `snlab/chat.py` — `StreamingFrameDecoder` (скользящий PCM-буфер, порог преамбулы 28/32, абсолютные смещения, `flush`, `reset`), `ChatSession` (текст UTF-8 и файловые пакеты, защита от повторного `FileCompleted`, сохранение в `rx-<millis>-<transferId>/`), `encode_text_message`/`encode_file_message` без промежуточного WAV. `snlab/audio.py` получил `ContinuousCapture`: bounded queue, `pause`/`resume`, отбрасывание блоков в паузе, очистка очереди при переполнении, `on_gap("pause"|"overflow")`, `on_error` и остановка потока.
- Windows Desktop Lab: вкладка «Чат» с профилем, микрофоном, логом и отправкой текста/файла сразу в PLAY. При входе на вкладку прослушивание запускается автоматически; после ручного «Стоп» повторный вход снова разрешает авто-старт. На время звука захват приостанавливается, после — возобновляется со сбросом PCM-буфера. Кнопки PLAY других вкладок блокируются, пока звучит чат.
- Android Messenger: `MessengerFragment` просит `RECORD_AUDIO` и сразу начинает постоянный захват; текст и файл отправляются немедленно, файл кодируется через `FileTransferCodec.encodeFileToPcm` без второй копии WAV. Принятые файлы сохраняются в `getExternalFilesDir(null)/received/rx-<millis>-<transferId>/`. На время PLAY композер блокируется, захват ставится на паузу; после PLAY или ошибки приём возобновляется.
- Общий контракт: Frame v1, CRC-16, профили и лимит 64 байта не изменены. Payload, начинающийся с `SF`, считается файловым пакетом; текст с таким префиксом отклоняется. Полудуплекс без эхоподавления. ACK, шифрования и аутентификации нет.
- Фактические проверки: `tests/test_chat.py` — 25 passed, `tests/test_sn035_chat_desktop.py` — 11 passed, полный Python-набор — **644 passed**. Android — **200** запусков (debug+release), failures 0, errors 0, skipped 2; `assembleDebug` — BUILD SUCCESSFUL, APK 3 624 071 байт. EXE пересобран: 66 531 101 байт, SHA-256 `1733F5E5D3DA3DBDBBAC97726D14DB33C4CACC0BBE996C8D35BF31A7A923DE7A`; `--smoke-test` exit 0.
- **Не проверено:** установка APK и работа чата на телефоне (`adb devices` пуст), передача через воздух, Phone → Laptop (`PENDING EXTERNAL MICROPHONE`), приём файла 8192 байта и пик памяти, длительная работа без утечек, реальный эхотракт. Документ: `docs/acoustic-chat.md`.

**Обновлено 2026-11-01 (Agent 1).**

- `SN-001` — аудит выполнен: `docs/audit/2026-11-01-repository-audit.md`. Статус `REVIEW`, ожидает проверки Product Owner.
- `SN-003` — milestone M0 утверждён Product Owner 2026-11-01, включая изменённую методику M0-4 (`docs/current-milestone.md` §5.1). `docs/architecture.md` пока не создан: требуется отдельное согласование структуры.
- `SN-005…SN-009` — выполнены: git-репозиторий, каноническая копия архитектуры, `README.md`, `requirements.txt`, каркас Acoustic Lab.
- `SN-010` — выполнен: сканер устройств/форматов и захват фона с PSD, полосами и клиппингом. Реальный захват 10 с выполнен, но микрофон отдаёт ≈ −90 dBFS RMS — уровень нужно поднимать до акустических экспериментов.
- `SN-011` — выполнен: кадр с преамбулой/CRC-16, CPFSK-модулятор, рампы и PCM16 WAV с JSON-отчётом. 20 запусков `--message "SILENTNETWORK"` дали одинаковые PCM SHA-256 `0f5dac99…92df94` и WAV SHA-256 `42bd7602…e6206`. Звук через динамик не воспроизводился.
- `SN-021` — файловая часть выполнена: `snlab/receiver.py` сам находит преамбулу 32/32, уточняет начало кадра, некогерентно демодулирует CPFSK и проверяет sync word, версию, длину и CRC-16. `receiver.py` получил CLI с кодами 0/2/3/4 и JSON-отчётом. 44 новых теста, 2 заглушки receiver удалены, всего 195 тестов проходят. Round-trip подтверждён на тексте, UTF-8, бинарных данных, длинах 1…512, начальной тишине и данных после кадра. Реальный захват с микрофона не выполнялся.
- `SN-013` — benchmark выполнен на файловом канале с AWGN: 100 испытаний на каждую обязательную точку 0/5/10/15/20 дБ плюс характеризация −10/−5 дБ. Фактически: −10 дБ — 0/100 (PER 1.000), −5 дБ — 76/100 (PER 0.240), 0 дБ — 97/100 (PER 0.030; 2 CRC и 1 неполный кадр), 5/10/15/20 дБ — 100/100 (PER 0.000). Отдельная M0-6: 100 payload × 32 байта при 15 дБ — 100/100, 0 ошибок, `PASS`. 45 новых тестов, всего 240 проходят. Артефакты: `acoustic-lab/results/{ber.csv,ber.png,per.png,benchmark.json,M0-report.md}`; WAV, CSV, PNG и JSON остаются локальными, коммитится только `M0-report.md`.
- Кодовой базы ядра по-прежнему нет: реализованы инфраструктура, сканер и файловые передатчик/приёмник/benchmark Acoustic Lab. SCNP, криптография и mesh не реализованы.
- Следующий шаг: закрытие M0 по фактическим результатам и решение Product Owner о переходе к M1 (SN-022). Для M1 нужен подтверждённый второй Windows-ПК.
- Блокеры: второй Windows-ПК не подтверждён (нужен только для M1); Rust toolchain отложен решением Product Owner; контракт `docs/android-core-api.md` отсутствует (запрос Gemini `REQ-CORE-001`).

**Обновлено 2026-11-02 (Agent 1, SN-016 UI redesign):**

- Windows `acoustic-lab/desktop_app.py` переведён на светлую палитру, две рабочие колонки и прогрессивное раскрытие. Главные действия: выбрать динамик, создать/воспроизвести сигнал, открыть WAV телефона. Технические параметры — в «Дополнительных настройках», PSD/спектрограмма/полный отчёт — в «Подробнее». Добавлена прокрутка содержимого, чтобы раскрытые детали не обрезались.
- Android: светлая тема `Theme.AppCompat.Light.NoActionBar`, русские строки, упрощённый Status без вымышленных `Connected/Online/Delivered`, Audio Lab с крупной записью, таймером, авто-анализом и человеческим вердиктом. Передатчик сохранён и вынесен во второстепенный раскрываемый раздел. ID, используемые Kotlin, сохранены.
- Фактические проверки: Python `441 passed`; `python desktop_app.py --smoke-test` и `--hardware-test --smoke-test` — exit 0, `play_enabled=False` (сигнал не создан); Android `test --rerun-tasks` — 118 запусков (59 debug + 59 release), failures 0, errors 0; `assembleDebug` — BUILD SUCCESSFUL.
- PLAY разблокирован по запросу Product Owner: кнопка доступна без `--hardware-test` после создания сигнала; подтверждение перед каждым PLAY сохранено. При запуске автоматически выбирается системный динамик, иначе первый выходной. `snlab.desktop.play_generated` сохраняет внутренний guard `hardware_test=True`, GUI передаёт его сам.
- Windows-скриншоты (main, frequency, result) сняты локально в `%TEMP%\opencode\sn016\` и не добавлены в Git. Android-скриншоты не сняты: эмулятор и system-images отсутствуют, `adb devices` пуст.
- **CORE/DSP/PROTOCOL INTEGRITY — NOT CHANGED.** Физическая передача и запись — `PHYSICAL AUDIO TESTING — PENDING`.

**Обновлено 2026-11-02 (Agent 1, план Acoustic Data Link v0):**

- Подготовлен план `docs/acoustic-data-link-plan.md`: передача UTF-8 текста в существующем кадре Frame v1 через BFSK, сначала Windows → Android, затем обратное направление.
- Предлагаемый основной профиль — слышимый `1200/2200 Гц`, 300 бод; причина: запись телефона подтвердила 1 кГц, а полоса 18–19 кГц в ней практически пустая (−103.18 dBFS). Ультразвук остаётся экспериментом, а не критерием первого успеха.
- Предлагаемый лимит payload — 64 байта UTF-8; Frame v1, CRC-16 и существующие WAV/JSON-контракты не меняются.
- Добавлены задачи SN-024…SN-028: контракт и векторы, Windows TX/RX, Android TX/RX, кросс-проверка Python ↔ Kotlin, физический тест и отчёт.
- **Код ещё не менялся.** Нужны решения Product Owner по диапазону, приоритету направления, лимиту payload и порогам PASS.

**Обновлено 2026-11-02 (Agent 1, SN-024 выполнен):**

- Создан контракт `docs/acoustic-data-link.md`: профили, Frame v1 без изменений, лимит 64 байта UTF-8, режим `acoustic` 28/32, типы ошибок, JSON-схема и правила кросс-языковых векторов.
- В `snlab/config.py` добавлены `PROFILE_DATA_AUDIBLE` (1200/2200 Гц, 300 бод) и `PROFILE_DATA_ROBUST` (1000/2000 Гц, 100 бод), `DATA_PROFILES`. `DEFAULT_PROFILE` оставлен `bfsk-18k`, чтобы не менять существующее поведение.
- Реализован `snlab/datalink.py`: текст → UTF-8 payload → Frame v1 → CPFSK → WAV с lead/tail тишиной и обратное декодирование с человекочитаемым `result`/`error_type`. CRC-16 явно помечен как не-аутентификация.
- `snlab/receiver.py` получил параметр `min_preamble_matches` (по умолчанию 32/32) во всех путях декодирования. Строгий режим сохранён; 28/32 включается явно.
- Созданы `tests/fixtures/data-link-vectors.json` и копия `apps/android/app/src/test/resources/data-link-vectors.json`: ASCII, кириллица, 64 байта, пустой payload, 65 байт, повреждённый CRC, обрезанный кадр, чистый шум.
- `tests/test_datalink.py` — 24 теста; обновлён `test_config.py`. Полный прогон: **471 passed**. Физическая передача через воздух не выполнялась и не заявляется.

**Обновлено 2026-11-02 (Agent 1, SN-025 выполнен):**

- Добавлен `snlab/datalink_desktop.py`: генерация WAV+JSON, PLAY через движок с проверкой SHA-256 файла, запись микрофона и декодирование. Схемы отчётов `data-link-playback/1` и `data-link-receive/1`.
- В `desktop_app.py` добавлены карточки «Передать сообщение» и «Принять сообщение»: выбор профиля, подтверждение PLAY, выбор микрофона, запись по кнопке, человекочитаемый вердикт и `error_type`, пути к WAV/JSON.
- `tests/test_sn025_datalink_desktop.py` — 17 тестов: бэкенд и GUI с `FakePlaybackEngine` и фейковой записью. Полный прогон: **488 passed**.
- `python desktop_app.py --smoke-test` — exit 0. Физическая передача через воздух не выполнялась: это SN-028.

**Обновлено 2026-11-03 (Agent 1, SN-026 и SN-027 выполнены):**

- Android: добавлен пакет `org.silentnetwork.app.audiolab.datalink` — `BfskProfile`, `FrameV1` (CRC-16/CCITT-FALSE), `BfskModulator` (непрерывная фаза, как в `snlab/bfsk.py`), `BfskDemodulator` (некогерентная энергия mark/space, поиск преамбулы по фазам, уточнение, проверки sync/version/length/CRC), `DataLinkCodec` (текст ↔ PCM/WAV ↔ отчёт JSON, строгий UTF-8).
- Исправлена ошибка подтверждения преамбулы: считались только положительные значения метрики (16/32 вместо 32/32). Теперь наблюдаемый бит сравнивается с ожидаемым `1010…`.
- `payload_utf8_error` теперь сохраняет `crc_ok=true` и данные кадра: целостность кадра подтверждена, текст — нет (как в Python).
- В `AudioLabFragment` добавлена всегда развёрнутая карточка «Передача данных»: сообщение до 64 байт UTF-8, профиль `data-audible`/`data-robust`, подтверждение PLAY, запись 5/10/15 с, фоновое декодирование, человекочитаемый вердикт, принятый текст, метрики записи, экспорт WAV/JSON. Ручная остановка записи — отмена, а не приём; это явно написано в статусе.
- Добавлены `DataLinkVectorsTest` и `DataLinkStateTest`: векторы Frame v1, round-trip, `data-robust`, неверный профиль, лимиты payload, повреждённый CRC, обрезанный кадр, чистый шум, UTF-8-ошибка при верном CRC, вердикты. Фактически: `gradlew test --rerun-tasks` — 73 debug + 73 release, failures 0, errors 0; `assembleDebug` — BUILD SUCCESSFUL; APK скопирован в `dist/silentnetwork-android-debug.apk`.
- SN-027: `acoustic-lab/cross_check_android.py` создаёт WAV Python-кодеком, запускает `CrossLanguageTest` через `-PsnCrosscheckDir`, затем декодирует Kotlin-WAV обратно Python-кодеком. Фактически 4/4 кейса в обе стороны (ASCII, кириллица, mixed, 64 байта), преамбула 32/32, CRC совпал; отчёт `acoustic-lab/results/sn027-cross-check.md`.
- Python-набор после изменений: **488 passed**. Физическая передача через воздух не выполнялась: SN-028 остаётся `BLOCKED` до реального прогона и утверждения порогов PASS. Android-скриншоты по-прежнему недоступны: эмулятор и `adb` отсутствуют.
- Подготовлен протокол SN-028: `docs/sn028-physical-test-protocol.md` — фиксированные тексты M1–M3, по 10 попыток на 30 см / 1 м / 3 м, 10 записей шума, таблица результатов, требования к артефактам. Прогон не выполнялся: нет утверждённых порогов и физического доступа к паре устройств.
- `dist/SilentNetwork-Desktop/` обновлён текущими `desktop_app.py` и `snlab/*.py` (включая `datalink.py`, `datalink_desktop.py`), `__pycache__` удалён; `python desktop_app.py --smoke-test` из `dist` — exit 0. `dist/SilentNetwork-Android.apk` заменён актуальной debug-сборкой (совпадает по SHA-256 с `dist/silentnetwork-android-debug.apk`), чтобы не устанавливали старую версию без раздела «Передача данных».

**Обновлено 2026-11-03 (Agent 1, SN-028 preflight):**

- Утверждённые Product Owner параметры зафиксированы: `data-audible` основной, `data-robust` диагностический, payload ≤ 64 байта, 30 см 10/10, 1 м ≥ 8/10, 3 м исследовательски, `bfsk-18k` не профиль приёмки. Профили, пороги и протокол не менялись.
- Добавлен `acoustic-lab/sn028_preflight.py`: инвентаризация устройств, безопасная запись фона, контрольный тон в петле ноутбука; результаты пишутся в `acoustic-lab/results/sn028/`. Добавлены `snlab.audio.select_preferred_device`, `device_warning`, `is_builtin_device`, `is_virtual_device`, `is_bluetooth_device`.
- Автовыбор устройств исправлен: сначала встроенное устройство, Bluetooth и виртуальные не выбираются; если допустимых нет — выбор пустой и предупреждение. Инцидент: в 18:19 системным выходом были Bluetooth-наушники `inkax-T02`, к 18:21 они исчезли. Тесты политики: `tests/test_sn028_device_selection.py` (10), обновлён `tests/test_sn015_desktop.py`.
- Preflight фактически: 20 endpoint'ов (11 входов, 9 выходов); default output MME `[3] Speaker (Realtek(R) Audio)`, default input MME `[1] Microphone Array (AMD Audio Dev`; форматы 8000–192000 Гц, `float32/int16/int24/int32`; 48000 Гц поддерживается. Входы ноутбука: MME/WASAPI — насыщенный шум с клиппингом, DirectSound — цифровая тишина, WDM-KS — ошибка `PaErrorCode -9999`.
- Product Owner подтвердил: встроенный микрофон ноутбука не работает, динамик работает, микрофон Android работает. Поэтому объём этапа изменён: выполняется только Laptop → Phone; Phone → Laptop — `PENDING EXTERNAL MICROPHONE`, из статистики исключён.
- Контрольный тон 1 кГц в петле ноутбука не обнаружен (когерентная амплитуда −46.0 dBFS во время тона против −47.4 / −46.6 до и после) — следствие нерабочего микрофона, а не измерение динамика. Windows при этом доводит сигнал до endpoint: пик-метр −10.7 dBFS.
- Тесты: Python **499 passed**; Android **73 debug + 73 release**, failures 0; кросс-проверка Python ↔ Kotlin 4/4, преамбула 32/32, CRC совпал. APK: 3 474 862 байта, SHA-256 `17F5F0B1…C782E3F`.
- Android не подключён: `adb devices` пуст, MTP-устройства нет. Установка APK, маршрут микрофона, `RECORD_AUDIO` и контрольный тон Laptop → Android не проверены. Физических передач, WAV/JSON и PASS/FAIL SN-028 нет.
- Отчёт: `acoustic-lab/results/sn028-preflight.md`. `dist/SilentNetwork-Desktop/` обновлён (`desktop_app.py`, `snlab/audio.py`), smoke-test exit 0.

**Обновлено 2026-11-03 (Agent 1, SN-029 standalone EXE):**

- `snlab/desktop.py` и `snlab/datalink_desktop.py` в режиме `sys.frozen` пишут результаты рядом с `.exe` (`results/hardware_calibration/`), а не во временный `_MEIPASS`. Добавлен `tests/test_sn028_packaging.py` (2 теста).
- Собран `dist/SilentNetwork-Desktop.exe`: PyInstaller 6.22.3, `--onefile --windowed`. Первая сборка без исключений — 234 873 087 байт; с исключением `torch`, `pandas`, `matplotlib`, `IPython`, `pytest`, `sphinx` — 66 450 541 байт.
- Фактическая проверка: `--smoke-test` exit 0; обычный запуск — процесс жив через 12 с, окно «SilentNetwork — Акустическая лаборатория»; Python **501 passed**.
- Добавлена инструкция `docs/sn028-packaging.md` с точной командой сборки и ограничениями. EXE не подписан; передача через воздух не выполнялась.
- Android-APK не пересобирался: исходники Android не менялись после сборки (последнее изменение `DataLinkStateTest.kt` — 00:38, APK — 08:26).

**Обновлено 2026-11-03 (Agent 1, первый контакт проверен):**

- Product Owner сохранил в `reports/` файлы `datalink_rx_data-audible.wav` и `datalink_rx_data-audible_report.json`. Agent 1 проверил их независимо: SHA-256 WAV `aed02e7b1526ceaba65130f55c366426cfc796e095e65741b39adb52caa00a42` совпал с JSON; кадр Frame v1 декодирован Python-приёмником при порогах 28/32 и 32/32; текст `SILENTNETWORK`, payload hex `53494c454e544e4554574f524b`, CRC-16 `0xD8B5`, версия 1, преамбула 32/32, `start_sample=114880`, `start_time_s=2.3933`.
- Метрики записи: 5.0 с, 48 кГц PCM16 mono, 240 000 отсчётов, RMS −45.37 dBFS, пик −26.84 dBFS, клиппинг 0. Контраст «кадр/фон»: до кадра −62.99 dBFS, кадр −37.02 dBFS, после −60.85 dBFS, то есть ≈24–26 дБ. Доминанта в полосе 900–2500 Гц — 2156 Гц, mark 1200 Гц присутствует.
- Статус Laptop → Phone: `FIRST CONTACT VERIFIED AT FILE LEVEL — SERIES NOT STARTED`. Первый контакт не входит в статистику SN-028: нет дистанции, громкости, модели телефона и помещения; текст не M1–M3; запись 5 с вместо 10 с.
- Оформлен отчёт `acoustic-lab/results/sn028-first-contact.md` с фактическими числами, отклонениями от протокола и ограничениями.
- Создан `docs/sn028-stage-1-runbook.md`: точный порядок 10 попыток на 30 см, тексты M1–M3, имена файлов, шапка метаданных, таблица попыток, команда разбора, правила при неудаче.
- Исправлен дефект JSON Android: `buildReceiveReport` принимает `minPreambleMatches` и записывает порог (28), а не фактическое совпадение (32); `AudioLabFragment` передаёт `DataLinkState.ACOUSTIC_MIN_PREAMBLE`. Добавлен тест `receiveReportStoresThresholdNotActualMatches`. Android: **74 debug + 74 release**, failures 0, errors 0; `assembleDebug` — BUILD SUCCESSFUL.
- Обновлён `dist/SilentNetwork-Android.apk` (и `dist/silentnetwork-android-debug.apk`): 3 520 640 байт, SHA-256 `c4384015a6b97a6466d3d000540a3fcc106a163644d98ccb0fc337e621f18034`. EXE не менялся.
- `reports/` не коммитится: WAV игнорируется `*.wav`, пользовательские артефакты остаются локально.

**Обновлено 2026-11-03 (Agent 1, первый успех и план):**

- Product Owner сообщил, что сообщение успешно передалось. На тот момент это было зафиксировано как `USER-REPORTED SUCCESS — EVIDENCE PENDING`; позже WAV/JSON были предоставлены и проверены — см. блок «первый контакт проверен» выше.
- Добавлен `acoustic-lab/sn028_series.py` и CLI `acoustic-lab/sn028_series.py`: пакетный разбор экспортированных WAV, независимое декодирование Python-приёмником, сверка текста и CRC, метрики записи, CSV/JSON/Markdown, вердикты по утверждённым порогам (30 см 10/10, 1 м ≥ 8/10, 3 м исследовательски, шум 0 ложных кадров). Неизвестная дистанция/неполная серия дают `INCOMPLETE`, а не PASS.
- Тесты `tests/test_sn028_series.py` (11): успешное декодирование, кириллица, несовпадение текста, тишина, пороги, фильтрация дистанций, запись отчётов. Python **512 passed**.
- Исправлен быстрый старт в протоколе: запись на Android имеет фиксированную длительность 10 с; ручная кнопка «Стоп» отменяет запись, а не принимает её. Предыдущая формулировка «остановить через 3–4 с» была ошибочной.
- Добавлен план `docs/sn028-next-plan.md`: этап 0 — зафиксировать первый успех, этап 1 — 30 см ×10, этап 2 — 1 м и 3 м, этап 3 — шум, этап 4 — диагностика, этап 5 — Phone → Laptop после внешнего микрофона, этап 6 — отчёт и решение Product Owner. Отдельно предложены варианты после SN-028 (укрепление канала, Security/SCNP, M1).

**Обновлено 2026-11-02 (Agent 1, интеграция SN-015):**

- Android-ветка `agent1/sn015-android` (`5069513`) объединена в `agent1/m0-acoustic-lab` через `--no-ff`; merge commit `aba35cc`. Конфликтов нет. Android-файлы идентичны `5069513`, Windows-код идентичен `f497f66`.
- Готовы: Android APK (`dist/SilentNetwork-Android.apk`), Windows launcher (`dist/SilentNetwork-Desktop/`), единый WAV/JSON контракт, инструкция первого теста `docs/hardware-calibration/SN-015-FINAL-DELIVERY.md`.
- Статусы: Windows Desktop Lab — `DONE`; Android Receiver — `DONE`; WAV compatibility — `DONE`; JSON contract — `DONE`; Physical Laptop → Phone — `PENDING`.
- Числа 439 Python-тестов и 112 Android-тестов ранее были получены независимыми прогонами до merge и сами по себе слияние не подтверждают. После merge выполнены повторные прогоны: Python `439 passed` (0 failed, 19.62 s), Android debug 56 + release 56 = `112`, failures 0, errors 0; APK собран заново (`3 392 577` байт). Детали — в `docs/hardware-calibration/SN-015-FINAL-DELIVERY.md` §10.1.
- PyInstaller в окружении отсутствует; standalone EXE не собирался. Windows-версия поставляется как Python launcher + инструкция, без изменения системного окружения.

**Обновлено 2026-11-02 (Agent 1):**

- `SN-015` — код Windows Desktop Lab готов: Tkinter GUI; playback-сервис с `FakePlaybackEngine` и STOP; строгая проверка SHA-256 перед PLAY; JSON только после фактической попытки; импорт PCM16 WAV телефона с анализом, CSV, PSD и спектрограммой. На момент SN-015 PLAY требовал `--hardware-test` и подтверждения; с SN-016 PLAY доступен без флага, системный динамик выбирается автоматически. 39 новых тестов, полный прогон `439 passed`; `python desktop_app.py --smoke-test` выполнен без звука. **Физическое воспроизведение и LAPTOP → PHONE не выполнялись** — статус `PENDING`, критерии в `docs/hardware-calibration/SN-015-LAPTOP-TO-PHONE.md`.
- `SN-014` остаётся `IN_PROGRESS`: аппаратная калибровка не выполнялась, физический PASS не заявляется.

**Обновлено 2026-11-01 (Gemini):**

- `SN-002` — аудит Android выполнен: `docs/audit/android-environment-audit.md`. Статус `DONE`. Проведена успешная тестовая сборка Debug APK (792 КБ) во временном каталоге за 51 с.
- Окружение Android: JDK 17 фактически найден в `C:\jdk-17.0.2` (`$env:JAVA_HOME`), Android SDK 34/35 и NDK 27 доступны в `C:\Android\sdk`. Блокер по отсутствию JDK 17 снят.
- В репозитории нет Android-кода. Создание скелета приложения запланировано в рамках `SN-004` (статус `READY`).
- Оформлен технический запрос `REQ-CORE-001` к Agent 1 на разработку спецификации `docs/android-core-api.md`.

### Требует решения Product Owner

1. Принять или отклонить SN-021 и SN-013 по отчётам Agent 1; закрытие M0 выполняется только после проверки фактических результатов Product Owner.
2. Согласовать структуру и содержание `docs/architecture.md` (SN-003).
3. Подтвердить второго участника Windows ↔ Windows до начала M1.
4. Закрыть SN-004: Android app skeleton уже создан и собирается, требуется подтверждение Product Owner.
5. Разрешить физический прогон SN-015 (динамик ноутбука → микрофон Android) и утвердить пороги PASS/FAIL из `docs/hardware-calibration/SN-015-LAPTOP-TO-PHONE.md`. Программы готовы: `dist/SilentNetwork-Android.apk` и `dist/SilentNetwork-Desktop.exe`; порядок запуска — `docs/hardware-calibration/SN-015-FINAL-DELIVERY.md`.
6. Принять SN-016 по `docs/UI-REDESIGN-SN-016.md`. Для закрытия визуальной проверки Android нужен эмулятор или физическое устройство; скриншоты Android сейчас заблокированы инфраструктурой.
7. Утвердить план Acoustic Data Link v0 (`docs/acoustic-data-link-plan.md`): слышимый профиль `1200/2200 Гц`, 300 бод, как основной для первого доказательства передачи данных.
8. Подтвердить приоритет направления Windows → Android, затем Android → Windows.
9. Утвердить лимит payload 64 байта UTF-8 для v0.
10. ~~Утвердить пороги PASS из `docs/acoustic-data-link-plan.md` §9.4~~ — утверждены Product Owner 2026-11-03 (30 см 10/10, 1 м ≥8/10, 3 м исследовательски).
11. SN-028: Laptop → Phone — `FIRST CONTACT VERIFIED AT FILE LEVEL — SERIES NOT STARTED`; следующий шаг — серия 30 см ×10 по `docs/sn028-stage-1-runbook.md` с WAV/JSON и метаданными. Phone → Laptop — `PENDING EXTERNAL MICROPHONE`; нужен рабочий проводной или USB-микрофон ноутбука.
12. Подтвердить план `docs/sn028-next-plan.md` и приоритет этапов; после SN-028 выбрать направление: укрепление канала, Security/SCNP или M1.
13. Подтвердить, что для EXE достаточно существующей передачи/приёма текста, или запросить отдельный режим работы с произвольными аудиофайлами (сейчас такого режима нет).
14. Одобрить или отклонить файловый слой v0 (SN-034): лимит 8192 байт, имя до 40 байт UTF-8, один файл без ACK, отчёты `.tx.json`/`.rx.json`. Windows Desktop Lab и Android-карточка приёма реализованы локально; изменение контракта и воздушный тест — после одобрения. Первый воздушный файл 43 байта подготовлен по `docs/sn034-first-air-runbook.md`; телефон не подключён, прогон не выполнялся. Лимит 8192 байта требует записи около 420 с и проверки памяти на устройстве.
15. Одобрить или отклонить непрерывный чат v0 (SN-035): постоянное прослушивание, отправка текста/файла сразу в PLAY, полудуплекс, резерв префикса `SF`, отсутствие ACK/шифрования. Проверка на телефоне и передача через воздух не выполнялись; после подключения устройства нужен отдельный прогон Laptop → Phone.

## 6. Журнал изменений

| Дата | Агент | Изменение | Ссылка / результат |
|---|---|---|---|
| 2026-11-01 | Product Owner / ChatGPT | Создана начальная доска задач на основе SilentNetwork Architecture v1.0 | Версия 1.0 |
| 2026-11-01 | Agent 1 | Аудит репозитория: `docs/audit/2026-11-01-repository-audit.md`; предложен milestone M0 `docs/current-milestone.md`; добавлены задачи SN-005…SN-009; SN-001 → REVIEW, SN-003 → IN_PROGRESS | Версия 1.1 |
| 2026-11-01 | Gemini | Аудит Android-окружения: `docs/audit/android-environment-audit.md`; обнаружен JDK 17 (`C:\jdk-17.0.2`), Android SDK 34/35/NDK 27; успешный тест сборки APK; SN-002 → DONE, SN-004 → READY | Версия 1.2 |
| 2026-11-01 | Agent 1 | SN-005…SN-009 выполнены: git-репозиторий и ветка `agent1/m0-acoustic-lab`, каноническая копия Architecture v1.0 (SHA-256 сверен), `README.md`, `requirements.txt`, каркас Acoustic Lab (52 теста); SN-005…SN-009 → DONE | Версия 1.3 |
| 2026-11-01 | Agent 1 | SN-010 выполнен: `snlab/audio.py` и `scanner.py` — устройства, форматы, захват 10 с, RMS/peak/шумовой пол/PSD/полосы/клиппинг; 100 тестов; реальный захват 48 кГц проверен; измерены ограничения тракта (низкий уровень микрофона, медленный холодный старт MME); SN-010 → DONE, SN-011 → READY | Версия 1.4 |
| 2026-11-01 | Agent 1 | SN-011 выполнен: кадр с преамбулой/CRC-16, CPFSK-модулятор, рампы, PCM16 WAV+JSON, ADR-001; 153 теста; 20/20 одинаковых PCM и WAV SHA-256; SN-011 и SN-020 → DONE, SN-021 → READY | Версия 1.5 |
| 2026-11-01 | Agent 1 | SN-021 выполнен: файловый BFSK-приёмник с поиском преамбулы 32/32, некогерентной демодуляцией, проверкой sync word/версии/длины/CRC-16, CLI и JSON-отчётом; 44 новых теста, 2 заглушки удалены, 195 тестов проходят; SN-021 → DONE, SN-013 → READY | Версия 1.6 |
| 2026-11-01 | Agent 1 | SN-013 выполнен: воспроизводимый BER/PER benchmark на AWGN, 100 испытаний на точки 0/5/10/15/20 дБ, диагностический BER до CRC, отдельная M0-6 100×32 при 15 дБ (100/100, PASS); 45 новых тестов, 240 всего; SN-013 → DONE | Версия 1.7 |
| 2026-11-02 | Agent 1 | SN-015: Windows Desktop Lab — `desktop_app.py`, `snlab/desktop.py`, `snlab/playback.py`, `.bat`, инструкция; 39 новых тестов, 439 passed; GUI smoke-test без звука; физика PENDING | Версия 1.9 |
| 2026-11-02 | Agent 1 | SN-015 integration: merge `agent1/sn015-android` (`5069513`) в `agent1/m0-acoustic-lab`, merge commit `aba35cc`; Android APK и Windows launcher в `dist/`; SN-015 → REVIEW (код DONE, физика PENDING) | Версия 2.0 |
| 2026-11-02 | Agent 1 | SN-016: единый светлый UI Windows и Android; Windows `desktop_app.py` переработан, Android layouts/themes/strings обновлены, Audio Lab получил авто-анализ и вердикт; Python 439 passed, Android 118 запусков (0 failures), `assembleDebug` успешен; Android-скриншоты BLOCKED; физика PENDING | Версия 2.1 |
| 2026-11-02 | Agent 1 | SN-016 playback: PLAY доступен без `--hardware-test` после создания сигнала, системный динамик выбирается автоматически; добавлены GUI-тесты автоселекта и PLAY без флага; Python 441 passed; документация обновлена; физика по-прежнему PENDING | Версия 2.2 |
| 2026-11-02 | Agent 1 | Проанализирована запись телефона `sn015_..._rx.wav`: PCM16, 48 кГц, моно, 8.1 с; обнаружен тон 999.9 Гц при ожидаемых 1000 Гц, смещение −0.1 Гц, запас обнаружения 54.5 дБ, клиппинга нет; SNR одиночной записи — N/A | Версия 2.3 |
| 2026-11-02 | Agent 1 | Проект сведён в одну папку `C:\Projects\SilentNetwork`; worktree `SilentNetwork-agent1` и `SilentNetwork-android` удалены; Gemini выведен из проекта, задачи переназначены Agent 1; `GEMINI.md` и `PROMPT_GEMINI.txt` удалены; AGENTS/README/PROMPT обновлены | Версия 2.4 |
| 2026-11-02 | Agent 1 | Составлен план Acoustic Data Link v0: передача текста в Frame v1 через слышимый профиль 1200/2200 Гц; задачи SN-024…SN-028; Windows → Android приоритетный; требования по кросс-проверке Python ↔ Kotlin и физическому тесту; пороги ждут PO | Версия 2.5 |
| 2026-11-02 | Agent 1 | SN-024: контракт `docs/acoustic-data-link.md`, профили данных, `snlab/datalink.py`, `receiver.min_preamble_matches` (32/32 по умолчанию), кросс-языковые векторы; Python 471 passed; физика PENDING | Версия 2.6 |
| 2026-11-02 | Agent 1 | SN-025: Windows Desktop Lab — передача и приём текста через звук, `snlab/datalink_desktop.py`, карточки в `desktop_app.py`, WAV/JSON, фейковые тесты; Python 488 passed; физика PENDING | Версия 2.7 |
| 2026-11-03 | Agent 1 | SN-026: Android BFSK-модем и Frame v1, `audiolab/datalink/*`, `DataLinkCodec`, карточка «Передача данных», `DataLinkVectorsTest`/`DataLinkStateTest`; Android 73+73 теста, failures 0; `assembleDebug` успешен; APK в `dist/`; физика PENDING | Версия 2.8 |
| 2026-11-03 | Agent 1 | SN-027: `cross_check_android.py` + `CrossLanguageTest`, 4/4 кейса Python ↔ Kotlin в обе стороны, преамбула 32/32, CRC совпал; отчёт `acoustic-lab/results/sn027-cross-check.md`; SN-028 → BLOCKED до физического прогона | Версия 2.8 |
| 2026-11-03 | Agent 1 | SN-026 и SN-027 закоммичены раздельно (`fa54323`, `8010185`); подготовлен протокол `docs/sn028-physical-test-protocol.md`; `dist/SilentNetwork-Desktop/` обновлён и проверен smoke-тестом, `dist/SilentNetwork-Android.apk` приведён к текущей сборке; SN-028 остаётся BLOCKED | Версия 2.8 |
| 2026-11-03 | Agent 1 | SN-028 preflight: `sn028_preflight.py`, безопасный автовыбор устройств (встроенное вместо Bluetooth), тесты 10 + обновление SN-015; Python 499 passed; Android 73+73; кросс-проверка 4/4; отчёт `acoustic-lab/results/sn028-preflight.md`; Laptop → Phone BLOCKED (нет Android), Phone → Laptop PENDING EXTERNAL MICROPHONE | Версия 2.9 |
| 2026-11-03 | Agent 1 | SN-029: standalone `dist/SilentNetwork-Desktop.exe` (66 МБ, onefile/windowed, исключены torch/pandas/matplotlib), frozen-каталог результатов рядом с `.exe`, тесты упаковки; smoke-test exit 0, окно открывается, Python 501 passed; инструкция `docs/sn028-packaging.md`; передача через воздух не выполнялась | Версия 3.0 |
| 2026-11-03 | Agent 1 | Первый успех Laptop → Phone по сообщению Product Owner зафиксирован как `USER-REPORTED SUCCESS — EVIDENCE PENDING`; добавлен пакетный анализатор WAV `sn028_series.py` (11 тестов, Python 512 passed) и план `docs/sn028-next-plan.md`; исправлена ошибка быстрого старта про ручную остановку записи | Версия 3.1 |
| 2026-11-03 | Agent 1 | WAV/JSON первого контакта проверены независимо: SHA-256, Frame v1, CRC `0xD8B5`, текст `SILENTNETWORK`, 32/32 преамбулы, клиппинг 0, контраст ≈24–26 дБ; статус `FIRST CONTACT VERIFIED AT FILE LEVEL — SERIES NOT STARTED`; отчёт `acoustic-lab/results/sn028-first-contact.md`; runbook `docs/sn028-stage-1-runbook.md`; JSON Android исправлен (порог 28 вместо фактических 32), Android 74+74, APK 3 520 640 байт SHA-256 `c4384015…f18034` | Версия 3.2 |
| 2026-11-04 | Agent 1 | SN-034: файловый слой v0 — `docs/acoustic-file-transfer.md`, `snlab/file_transfer.py`, CLI `file_transfer.py`, векторы `file-transfer-vectors.json`, 71 тест; round-trip 0…4096 байт, потеря кадра, конфликты, SHA-256; Python 583 passed; 100 байт → 10.42 с WAV, 4096 байт → 181.9 с; передача через воздух и UI не выполнялись; SN-034 → REVIEW | Версия 3.3 |
| 2026-11-04 | Agent 1 | SN-034 этап 1: Windows Desktop Lab для файлов — `snlab/file_transfer_desktop.py`, карточки «Передать файл»/«Принять файл», отдельные JSON-схемы tx/playback/receive, отдельный каталог на каждый приём; 19 новых тестов; Python 602 passed; GUI smoke-test exit 0; воздух и Android не проверялись | Версия 3.4 |
| 2026-11-04 | Agent 1 | SN-034 этап 3: Android-приём файла — Kotlin-порт `FileTransferCodec.kt`, offset-aware `findFrameStart(from)`, карточка «Файлы через звук» (15/30/60 с, экспорт файла и JSON), `DataLinkState` и строки, 17 JVM-тестов на точных векторах; Android 91 debug + 91 release, failures 0; `assembleDebug` успешен, APK 3 605 757 байт SHA-256 `27E355B9…B838`; передача через воздух не выполнялась | Версия 3.5 |
| 2026-11-04 | Agent 1 | SN-034 кросс-проверка: `cross_check_android.py` и `CrossLanguageTest` расширены файловыми кейсами — текст 40 Б, кириллическое имя 23 Б, 120 Б / 3 чанка, пустой файл; Python → Kotlin и Kotlin → Python, сверка имени, размера, чанков и SHA-256; 8/8 PASS, отчёт `acoustic-lab/results/sn027-cross-check.md`; Android 91+91, Python 602; воздух не проверялся | Версия 3.6 |
| 2026-11-04 | Agent 1 | SN-034 первый воздушный файл подготовлен: JSON 43 байта, SHA-256 `59634320…19f5a`, `data-audible`, `transfer_id=1`, WAV 8.28 с SHA-256 `835cb4ae…c085`; L0 round-trip совпал; runbook `docs/sn034-first-air-runbook.md`; телефон не подключён, воздух не проверялся | Версия 3.7 |
| 2026-11-04 | Agent 1 | SN-034 лимит файлового слоя поднят до 8192 байт по решению Product Owner: 152 DATA-чанка, 164 пакета, ≈364 с звука; Python `MAX_FILE_BYTES`/`MAX_CHUNKS` и векторы обновлены, `MAX_RECORD_SECONDS=600`; Android-запись 15/30/60/120/240/420 с, `largeHeap`, подсказка о памяти; `bitMetric` переведён на `FloatArray` со скользящей корреляцией; Python 608 passed, кросс-проверка Python ↔ Kotlin 8/8; Android 94 debug + 94 release, failures 0, errors 0, skipped 1; `assembleDebug` успешен; APK 3 567 860 байт SHA-256 `90633FFE…090C0C`, EXE 66 498 521 байт SHA-256 `E925CD0A…456990`; приём 8192 байт через воздух не проверялся | Версия 3.9 |
| 2026-11-04 | Agent 1 | SN-035 непрерывный чат: Python `snlab/chat.py` (`StreamingFrameDecoder`, `ChatSession`, `encode_text_message`/`encode_file_message`), `ContinuousCapture` в `snlab/audio.py` (bounded queue, pause/resume, `on_gap`), вкладка «Чат» в Windows Desktop Lab с авто-прослушиванием и отправкой сразу в PLAY; Android `ChatSession.kt`, `StreamFrameDecoder.kt`, `ContinuousAudioCapture.kt`, `MessengerFragment` с авто-стартом и сохранением файлов в `received/rx-<millis>-<transferId>/`; полудуплекс, резерв префикса `SF`; тесты `test_chat.py` 25 + `test_sn035_chat_desktop.py` 11; Python 644 passed; Android 200 (failures 0, errors 0, skipped 2); `assembleDebug` успешен; APK 3 624 071 байт SHA-256 `F5189067…C12601`; EXE 66 531 101 байт SHA-256 `1733F5E5…A923DE7A`, smoke-test exit 0; работа на телефоне и передача через воздух не проверены | Версия 4.0 |
| 2026-11-05 | Agent 1 | SN-035 исправления: порядок кандидатов преамбулы и приоритет `IncompleteFrame` в Python `receiver.py`/`chat.py`/`file_transfer.py` и Kotlin `BfskDemodulator.kt`/`StreamFrameDecoder.kt`/`FileTransferCodec.kt`; ускорение `BfskModulator.kt` ≈2.7–3.4× (текст 64 Б 4.2 → 1.2 мс, файл 8192 Б 290–325 → 109–111 мс); регрессии на блочный поток, шумный разрезанный кадр и файлы 0/1/40/200/1000 Б; Python 651 passed; Android 204 (failures 0, errors 0, skipped 2); кросс-проверка 8/8; APK 3 624 071 байт SHA-256 `C9C6684E…DACFF7`; EXE 66 530 850 байт SHA-256 `5B1C3ED6…DCB6A0`, smoke-test exit 0; воздух и телефон не проверены | Версия 4.1 |
| 2026-11-07 | Agent 1 | SN-033/SN-036: ART/1 (MANIFEST/DATA/STATUS/ABORT, битовая карта, выборочный повтор, SHA-256) и его интеграция в Python-чат Windows Desktop Lab; чекбокс ART/1, автоматический ответ `STATUS`, отмена и прогресс; Python 777 passed (`test_reliable.py` 107, `test_reliable_chat.py` 12, `test_sn036_reliable_desktop.py` 7, `test_sn035_chat_desktop.py` 11). Пересобраны артефакты: EXE 66 581 462 байта SHA-256 `FD951AC3…9BEC1B2`, smoke-test exit 0; APK 3 734 143 байта SHA-256 `5E7FF916…56B052C`; Android 204 (failures 0, errors 0, skipped 2). Воздух, Android-порт ART/1 и кросс-проверка ART/1 не проверялись | Версия 4.2 |
| 2026-11-07 | Agent 1 | SN-037: исправлена отправка Android-чата — общий валидатор принимал только data-профили, поэтому `chat-normal`/`chat-excellent`/`chat-highfreq` отклонялись; добавлен `BfskProfile.supportedProfiles()`, оба кодека принимают chat-профили. Кнопка «Воспроизвести» в Audio Lab (режим «Сообщение») больше не отключена до ручного создания: она активна всегда и при необходимости кодирует текущий текст. Новый `ChatProfileCodecTest` — 6 тестов; Android 216 (108 debug + 108 release, failures 0, errors 0, skipped 2); `assembleDebug` успешен; APK 3 734 143 байта SHA-256 `277342375B014DEBF1A9D76C405FC4A0FBA6BD6E24F662D474E54DD6A0B68251`. Телефон и воздух не проверялись | Версия 4.3 |

## 7. Шаблон добавления задачи

```text
ID:
Название:
Ответственный:
Статус:
Контекст:
Разрешённые файлы:
Запрещённые изменения:
Зависимости:
Критерии приёмки:
Тесты:
Результат / отчёт:
Блокер / следующий владелец:
```
