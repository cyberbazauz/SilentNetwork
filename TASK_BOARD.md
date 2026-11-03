# SilentNetwork — общая доска задач

**Версия:** 3.0
**Дата создания:** 2026-11-01
**Последнее обновление:** 2026-11-03 — Agent 1: собран standalone Windows EXE `dist/SilentNetwork-Desktop.exe` (66 МБ, smoke-test exit 0, окно открывается, 501 passed); добавлен `docs/sn028-packaging.md`; Laptop → Phone по-прежнему `BLOCKED` без Android; Phone → Laptop — `PENDING EXTERNAL MICROPHONE`
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
| SN-016 | Редизайн Windows Desktop Lab и Android UI в едином светлом стиле | Agent 1 | REVIEW | Выполнен без изменения DSP, кадра, CRC, WAV/JSON, Test ID и lifecycle. Windows: две колонки, «Динамик ноутбука», «Отправить тестовый сигнал», «Проверить запись телефона», человеческий вердикт, компактные метрики, прокрутка при раскрытии «Подробнее». PLAY доступен без `--hardware-test` после создания сигнала; системный динамик выбирается автоматически; перед каждым PLAY — подтверждение. Android: светлая тема, Status без фейковых состояний, Audio Lab с крупной записью и авто-анализом, передатчик вынесен во второстепенный раздел. Python `441 passed`; GUI smoke-test FILE-ONLY и `--hardware-test` — `play_enabled=False` (сигнал не создан); Android `118` запусков (59 debug + 59 release), failures/errors 0; `assembleDebug` успешен. Описание: `docs/UI-REDESIGN-SN-016.md`. Windows-скриншоты сняты локально и не коммитятся; **Android-скриншоты — BLOCKED**: нет эмулятора/устройства. Физический аудиотест — `PENDING` |

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
| SN-028 | Физический тест передачи данных и отчёт | Agent 1 | IN_PROGRESS | Параметры и пороги утверждены Product Owner 2026-11-03. **Laptop → Phone:** `BLOCKED` по устройству — Android не подключён (`adb devices` пуст, MTP нет), физическая серия не начата. **Phone → Laptop:** `PENDING EXTERNAL MICROPHONE` — встроенный микрофон ноутбука не работает, в статистику не входит. Preflight: 20 endpoint'ов, форматы 8–192 кГц, профили Python/Kotlin совпадают, Python 499 passed, Android 73+73, кросс-проверка 4/4. Отчёт: `acoustic-lab/results/sn028-preflight.md`; протокол: `docs/sn028-physical-test-protocol.md`. |
| SN-029 | Standalone Windows EXE: отправка, приём и декодирование | Agent 1 | DONE | `dist/SilentNetwork-Desktop.exe` собран PyInstaller 6.22.3 (onefile, windowed) из `acoustic-lab/desktop_app.py`; исключены `torch`, `pandas`, `matplotlib`, тестовые пакеты — размер 66 450 541 байт вместо 234 873 087. Результаты в frozen-режиме пишутся рядом с `.exe` (`snlab/desktop.py`, `snlab/datalink_desktop.py`), тесты `tests/test_sn028_packaging.py`. Фактически: smoke-test exit 0, окно «SilentNetwork — Акустическая лаборатория» открывается, `501 passed`. Инструкция: `docs/sn028-packaging.md`. Передача через воздух не выполнялась. |

### Этап 3 — Security, Identity и SCNP

| ID | Задача | Ответственный | Статус | Критерий приёмки / результат |
|---|---|---|---|---|
| SN-030 | Модель угроз и схема идентичности устройств | Agent 1 | BACKLOG | `docs/security-model.md` и тестируемые требования |
| SN-031 | Реализовать сессионные ключи и AEAD на проверенной библиотеке | Agent 1 | BACKLOG | Положительные и отрицательные crypto-тесты; секреты не попадают в логи |
| SN-032 | Зафиксировать SCNP wire format v1 | Agent 1 | BACKLOG | `docs/protocol-spec.md`, ограничения длин, endian, nonce, versioning и test vectors |
| SN-033 | ACK/NACK, sequence, replay protection и retransmission | Agent 1 | BACKLOG | Тесты потерь, дубликатов, повторов, таймаутов и переполнения счётчиков |

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
11. SN-028: физический прогон Laptop → Phone разрешён; нужен подключённый Android по USB с отладкой (сейчас `adb devices` пуст). Phone → Laptop — `PENDING EXTERNAL MICROPHONE`; нужен рабочий проводной или USB-микрофон ноутбука.
12. Подтвердить, что для EXE достаточно существующей передачи/приёма текста, или запросить отдельный режим работы с произвольными аудиофайлами (сейчас такого режима нет).

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
