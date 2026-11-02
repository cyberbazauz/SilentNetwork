# Отчёт по этапу SN-014-ANDROID: Подготовка калибровки аудиотракта Android

**Дата:** 2026-11-01  
**Ответственный агент:** Agent 2 — Gemini (Android & Application Developer)  
**Режим выполнения:** `CODE ONLY — NO PHYSICAL AUDIO TESTS TODAY`  
**Область изменений:** строго внутри `apps/android/`  
**Ветка:** `gemini/android-audio-calibration`  

> [!NOTE]
> Раздел 9 добавлен по задаче **SN-015**: Android Audio Lab Receiver,
> совместимость WAV/JSON с Windows Desktop Lab и статус физического теста.
> Разделы 1–8 сохранены как исторический отчёт SN-014.

---

## 1. Цель и контекст этапа

Цель задачи **SN-014-ANDROID** — подготовить в Android-приложении SilentNetwork полноценный автономный диагностический раздел:
**«Audio Lab / Калибровка аудио»**.

Раздел спроектирован для физической верификации аппаратного аудиотракта смартфонов:
1. Проверка способности излучателя (динамика) воспроизводить частоты 15–22 кГц (включая предварительный BFSK-профиль 18–19 кГц).
2. Проверка способности приёмника (микрофона) захватывать аудио в диапазоне 15–22 кГц на частоте дискретизации 48 кГц без искажений.
3. Проверка уровня нелинейных искажений, гармоник, постоянного смещения (DC offset) и клиппинга.
4. Исследование доступности режима `AudioSource.UNPROCESSED` и отключения аппаратных фильтров (AEC, AGC, Noise Suppression).
5. Экспорт записей в стандартном формате PCM16 mono WAV для сквозного анализа инструментами Python Acoustic Lab Agent 1 (`acoustic-lab/`).
6. Экспорт структурированного диагностического отчёта в формате JSON.

> [!IMPORTANT]
> **Режим позднего времени строго соблюдён:**  
> Сегодня **не производилось** физическое воспроизведение звуков через динамик (`AudioTrack.play()` не вызывался), **не производился** захват с реального микрофона (`AudioRecord.startRecording()` не вызывался) и **не запускались** физические loopback-тесты. Все проверки выполнены программно на уровне юнит-тестов с mock/fake аудиодвижками.

---

## 2. Реализованные компоненты и функции

### A. Раздел TRANSMITTER (Передатчик тестового сигнала)
- **Сетка частот:** выбор частот 1000 Гц (слышимый контрольный тон), 15, 16, 17, 18, 18.5, 19, 20, 21, 22 кГц.
- **Маркировка гипотезы:** частоты 18–19 кГц явно маркируются бейджем `ГИПОТЕЗА M0` (экспериментальный профиль, не гарантирующий универсальную слышимость).
- **Параметры теста:** выбор Sample Rate (48 000 Гц целевой, 44 100 Гц fallback), длительности (1, 2, 3, 5 сек) и безопасной амплитуды (0.05..0.50, по умолчанию безопасный уровень 0.15 = -16.5 dBFS).
- **Сглаживание фронтов (Tukey/Hann ramp):** генератор синусоидального сигнала применяет 5 мс косинусный сглаживающий фронт в начале и конце импульса, предотвращая щелчки диффузора мембраны.
- **Беззвучная генерация WAV:** кнопка «Сгенерировать WAV» создаёт чистый файл PCM16 mono WAV в кэше без включения аудиотракта.
- **Безопасное воспроизведение:** кнопка «PLAY» требует явного подтверждения в диалоговом окне предупреждения с указанием частоты и амплитуды.
- **Экстренная остановка:** кнопка «STOP» мгновенно останавливает вывод и освобождает поток воспроизведения.
- **Экспорт WAV:** экспорт созданного файла через Android Storage Access Framework (SAF) в системное хранилище.

### B. Раздел RECEIVER (Запись с микрофона)
- **Параметры захвата:** выбор длительности (3, 5, 10 сек) и частоты дискретизации (48 000 Гц, 44 100 Гц).
- **Статус разрешений:** отображение и запрос системного разрешения `RECORD_AUDIO`.
- **Контроль запуска:** запись начинается **только** по явному нажатию «НАЧАТЬ ЗАПИСЬ». Фоновый или скрытый захват исключён архитектурно.
- **Аудиоисточник:** приоритетное использование `MediaRecorder.AudioSource.UNPROCESSED` (API 24+) для обхода встроенных эквалайзеров и шумоподавителей; безопасный fallback на `VOICE_RECOGNITION` и `MIC`.
- **Безопасная остановка:** досрочная остановка по кнопке «ОСТАНОВИТЬ ЗАПИСЬ» и автоматическое освобождение `AudioRecord` при сворачивании фрагмента (`onPause`, `onDestroyView`).
- **Экспорт и интеграция:** сохранение захваченного аудио в стандартный PCM16 mono WAV и прямая передача в бортовой анализатор.

### C. Раздел ANALYZER (Бортовой спектральный анализ)
- **Импорт WAV:** возможность загрузить любой WAV-файл из файловой системы Android через системный Document Picker.
- **Метрики качества:**
  - **RMS (двойной формат):** линейный и в dBFS ($20 \log_{10}(RMS)$);
  - **Peak (двойной формат):** линейный и в dBFS;
  - **Клиппинг:** счётчик отсчётов с амплитудой $\ge 0.999$ с цветовой индикацией (`КЛИППИНГ: НЕТ` / `КЛИППИНГ ОБНАРУЖЕН`);
  - **DC offset:** расчёт постоянной составляющей смещения нуля;
  - **Число отсчётов и точная длительность.**
- **Спектральный анализ:**
  - Рассчёт алгоритмом Герцеля (Goertzel) для дискретных тонов;
  - Быстрое преобразование Фурье (Radix-2 FFT) для распределения спектральной плотности мощности (PSD);
  - Определение доминирующей частоты пика и топ-5 спектральных гармоник с относительными долями энергии;
  - Расчёт доли энергии в полосе 18–19 кГц относительно полного спектра.
- **Честный статус SNR:**
  - В соответствии с регламентом, приложение **не отображает** вымышленный SNR для одиночной записи без эталонного сигнала. В интерфейсе выводится явное уведомление:  
    *«SNR не рассчитывается (для одиночной записи без известной модели шума и эталонного сигнала расчёт SNR некорректен).»*
- **Визуализация спектра (`SpectrumView`):**
  - Пользовательский Canvas View с аппаратным ускорением.
  - Отображение спектральных полос от 0 до 24 кГц.
  - Контрастное выделение полосы 18–19 кГц неоновым контуром и полупрозрачным свечением.
  - Сетка частот (5k, 10k, 15k, 18k, 19k, 22k) и уровней в dB (-20, -40, -60, -80 dB).

### D. Формат результатов и экспорт
- **WAV:** стандартный PCM16 mono RIFF/WAVE с заголовком 44 байта, 100% совместимый со строгим считывателем `snlab/wavio.py` в Acoustic Lab Agent 1.
- **JSON-отчёт:**
  - Дата и время (ISO-8601 UTC);
  - Спецификация оборудования: производитель, модель, версия Android, SDK level;
  - Запрошенный и фактический sample rate, формат сэмплов, audio source, размер буфера;
  - Наличие и статус аппаратных эффектов (AEC, AGC, NS);
  - Параметры проведённого теста (частота, амплитуда, длительность);
  - Все результаты спектрального анализа и список пиков;
  - Предупреждения (клиппинг, низкий уровень);
  - **Нулевая телеметрия:** полное отсутствие персональных данных, геопозиции, MAC/IMEI адресов;
  - Экспорт работает автономно без интернета через системный Storage Access Framework.

---

## 3. Список созданных и изменённых файлов

Все изменения выполнены строго в каталоге `apps/android/`:

| Файл | Действие | Описание |
|---|---|---|
| `app/build.gradle.kts` | Изменён | Добавлены `testImplementation("org.json:json:20231013")` и `testOptions.unitTests.isReturnDefaultValues = true`. |
| `app/src/main/res/values/strings.xml` | Изменён | Добавлены строки интерфейса Audio Lab, предупреждения M0, описания тестов и позиционные спецификаторы. |
| `app/src/main/res/drawable/ic_tab_audiolab.xml` | Создан | Векторная иконка звуковой волны для навигационной панели. |
| `app/src/main/res/drawable/bg_button_secondary.xml` | Создан | Стиль второстепенной кнопки с тёмной подложкой и скруглением. |
| `app/src/main/res/drawable/bg_button_danger.xml` | Создан | Стиль кнопки экстренной остановки STOP (кораллово-красный). |
| `app/src/main/res/layout/activity_main.xml` | Изменён | Добавлена вкладка `tab_audio_lab` в нижнюю навигационную панель. |
| `app/src/main/res/layout/fragment_status.xml` | Изменён | Добавлена кнопка быстрого перехода «Открыть Audio Lab». |
| `app/src/main/res/layout/fragment_audio_lab.xml` | Создан | Макет экрана Audio Lab с тремя секциями (Transmitter, Receiver, Analyzer) и спектрограммой. |
| `app/src/main/java/org/silentnetwork/app/MainActivity.kt` | Изменён | Обработка клика по вкладке Audio Lab, подсветка и транзакции фрагмента. |
| `app/src/main/java/org/silentnetwork/app/state/AppPresentationState.kt` | Изменён | Добавлен `R.id.tab_audio_lab` в менеджер навигации `NavigationStateManager`. |
| `app/src/main/java/org/silentnetwork/app/ui/status/StatusFragment.kt` | Изменён | Обработка нажатия кнопки быстрого открытия Audio Lab. |
| `app/src/main/java/org/silentnetwork/app/ui/audiolab/SpectrumView.kt` | Создан | Canvas-компонент отображения спектральной плотности мощности (0–24 кГц) с подсветкой 18–19 кГц. |
| `app/src/main/java/org/silentnetwork/app/ui/audiolab/AudioLabFragment.kt` | Создан | Контроллер Audio Lab: передатчик, приёмник, анализатор, диалоги безопасности и экспорт SAF. |
| `app/src/main/java/org/silentnetwork/app/audiolab/dsp/SignalGenerator.kt` | Создан | Синтез гармонических колебаний, косинусные рампы, конвертер PCM16 little-endian. |
| `app/src/main/java/org/silentnetwork/app/audiolab/dsp/WavFileHandler.kt` | Создан | Строгое кодирование и декодирование PCM16 WAV с проверкой RIFF-заголовков. |
| `app/src/main/java/org/silentnetwork/app/audiolab/dsp/AudioAnalyzer.kt` | Создан | Бортовой анализатор: RMS, Peak, dBFS, клиппинг, DC offset, алгоритм Герцеля и FFT. |
| `app/src/main/java/org/silentnetwork/app/audiolab/audio/AudioEnginesContract.kt` | Создан | Контракты для аудиоплеера, рекордера и инспекции аудиоэффектов. |
| `app/src/main/java/org/silentnetwork/app/audiolab/audio/AudioEffectsInspector.kt` | Создан | Безопасная инспекция доступности AEC, AGC, NoiseSuppressor. |
| `app/src/main/java/org/silentnetwork/app/audiolab/audio/AndroidAudioPlayerEngine.kt` | Создан | Реальная реализация `AudioTrack` с запуском строго по кнопке пользователя. |
| `app/src/main/java/org/silentnetwork/app/audiolab/audio/AndroidAudioRecorderEngine.kt` | Создан | Реальная реализация `AudioRecord` с приоритетом `UNPROCESSED` и безопасным освобождением. |
| `app/src/main/java/org/silentnetwork/app/audiolab/audio/FakeAudioEngines.kt` | Создан | Изолированные mock/fake аудиодвижки для детерминированного тестирования без звука. |
| `app/src/main/java/org/silentnetwork/app/audiolab/presentation/AudioLabState.kt` | Создан | Состояния, валидаторы параметров и форматтеры меток интерфейса. |
| `app/src/main/java/org/silentnetwork/app/audiolab/reporting/CalibrationJsonReport.kt` | Создан | Сериализатор отчёта калибровки в JSON без PII. |
| `app/src/test/java/org/silentnetwork/app/NavigationStateTest.kt` | Изменён | Тесты навигации с учётом новой вкладки `tab_audio_lab`. |
| `app/src/test/java/org/silentnetwork/app/audiolab/SignalGeneratorTest.kt` | Создан | 5 юнит-тестов синтеза синуса, амплитуды, PCM16 и пауз. |
| `app/src/test/java/org/silentnetwork/app/audiolab/WavFileHandlerTest.kt` | Создан | 5 юнит-тестов кодирования/декодирования WAV и проверки повреждённых файлов. |
| `app/src/test/java/org/silentnetwork/app/audiolab/AudioAnalyzerTest.kt` | Создан | 6 юнит-тестов RMS, Peak, клиппинга, DC offset, FFT и Goertzel. |
| `app/src/test/java/org/silentnetwork/app/audiolab/CalibrationJsonReportTest.kt` | Создан | Юнит-тест структуры JSON-отчёта и отсутствия приватных данных. |
| `app/src/test/java/org/silentnetwork/app/audiolab/AudioEnginesContractTest.kt` | Создан | 3 юнит-теста жизненного цикла fake-движков и отсутствия автозапуска HAL. |
| `app/src/test/java/org/silentnetwork/app/audiolab/AudioLabPresentationTest.kt` | Создан | 4 юнит-теста валидации параметров, классификации диапазонов и форматтеров. |
| `ANDROID_AUDIO_TEST_PLAN.md` | Создан | Пошаговое руководство для Product Owner по завтрашним физическим испытаниям. |
| `ANDROID_AUDIO_CALIBRATION_REPORT.md` | Создан | Настоящий итоговый инженерный отчёт. |

---

## 4. Результаты автоматизированного тестирования

Выполнен запуск полного набора модульных тестов:  
`./gradlew.bat test`

### Сводка результатов:

| Тестовый класс | Количество тестов | Успешно | Ошибки |
|---|---|---|---|
| `SignalGeneratorTest` | 5 | 5 | 0 |
| `WavFileHandlerTest` | 5 | 5 | 0 |
| `AudioAnalyzerTest` | 6 | 6 | 0 |
| `CalibrationJsonReportTest` | 1 | 1 | 0 |
| `AudioEnginesContractTest` | 3 | 3 | 0 |
| `AudioLabPresentationTest` | 4 | 4 | 0 |
| `DomainModelsTest` | 4 | 4 | 0 |
| `MessengerValidatorTest` | 5 | 5 | 0 |
| `NavigationStateTest` | 3 | 3 | 0 |
| `SettingsAndStatusStateTest` | 4 | 4 | 0 |
| **ИТОГО** | **40** | **40** | **0** |

**Статус выполнения:** `BUILD SUCCESSFUL (100% тестов пройдено)`.

### Ключевые проверенные сценарии:
1. **Синтез синусоидального сигнала:** генерация тонов на 1000 Гц, 18 000 Гц, 18 500 Гц, 19 000 Гц с точным расчётом числа отсчётов и длительности.
2. **Преобразование PCM16:** строгий little-endian порядок байт, корректное округление и ограничение $[-1.0, 1.0] \rightarrow [-32768, 32767]$.
3. **Кодирование и декодирование WAV:** формирование корректных заголовков RIFF, WAVE, fmt (16 бит PCM, mono), data. Декодирование с обратным получением нормализованных сэмплов.
4. **Устойчивость к повреждённым файлам:** отказ при усечённом файле (< 44 байт), неверном magic ID и отсутствующем блоке `data`.
5. **Спектральный анализ:** точное совпадение пиков FFT и алгоритма Герцеля на частотах 1000 Гц (±12 Гц) и 18 500 Гц (±25 Гц); подтверждение доминирования энергии в полосе 18–19 кГц (> 70%).
6. **Клиппинг и DC offset:** обнаружение перегруженных отсчётов и вычисление постоянного сдвига.
7. **JSON-отчёт:** валидация структуры, соответствие типов, отсутствие персональных данных и геопозиции.
8. **Безопасность HAL:** подтверждено, что при инициализации приложения и создании классов `AudioTrack` и `AudioRecord` **не открываются**.

---

## 5. Результаты сборки APK

Выполнен запуск сборки отладочного пакета:  
`./gradlew.bat assembleDebug`

- **Статус сборки:** `BUILD SUCCESSFUL in 17s`
- **Путь к собранному APK:**  
  `apps/android/app/build/outputs/apk/debug/app-debug.apk`
- **Размер APK:** `3 369 397 байт (~3.21 МБ)`
- **Архитектура:** DEX bytecode, `minSdk 26`, `targetSdk 34`, `compileSdk 34`.

---

## 6. Что НЕ проверялось сегодня и почему

1. **Физическое воспроизведение звука:** `AudioTrack.play()` не вызывался на физических телефонах или динамиках (запрет ночного времени).
2. **Физический захват звука:** `AudioRecord.startRecording()` не вызывался на физических микрофонах (запрет ночного времени).
3. **Физическая акустическая дальность и Roll-off:** частотная характеристика физического излучателя смартфона и микрофона будет замерена завтра.
4. **Сквозной акустический обмен Телефон ↔ ПК:** будет проверен завтра по разработанному `ANDROID_AUDIO_TEST_PLAN.md`.

---

## 7. Готовность к завтрашним физическим испытаниям

Для проведения завтрашних тестов Product Owner:
1. Запускает скомпилированный APK `app-debug.apk` на Android-смартфоне.
2. Открывает вкладку **Audio Lab**.
3. Следует инструкциям в [ANDROID_AUDIO_TEST_PLAN.md](file:///c:/Projects/SilentNetwork/apps/android/ANDROID_AUDIO_TEST_PLAN.md).
4. Проводит тесты «Телефон → ПК» и «ПК → Телефон».
5. Экспортирует записанные WAV-файлы и JSON-отчёты и передаёт их Agent 1 для совместного анализа в Acoustic Lab.

---

## 8. Итоговый вердикт этапа

- **ANDROID AUDIO LAB CODE — DONE** (Кодовая база калибровочной лаборатории полностью реализована, протестирована и собрана в APK).
- **PHYSICAL AUDIO TESTING — PENDING** (Физические испытания излучателя и приёмника запланированы на завтра в соответствии с планом испытаний).

---

## 9. Обновление SN-015: Android Receiver и совместимость WAV/JSON

**Дата:** 2026-11-02  
**Базовая точка:** `68b6029`  
**Ветка Android-изменений:** `agent1/sn015-android`  
**Windows Desktop Lab:** commit `f497f66`  
**Физические аудиотесты:** не выполнялись.

### 9.1. Что изменено

- **Receiver:** запись только по кнопке; запрашивается `TYPE_BUILTIN_MIC`,
  фиксируются запрошенный и фактический маршрут (`requested_device`,
  `actual_routed_device`), фактический sample rate и число каналов.
  `AudioRecord` освобождается при остановке, `onPause` и `onDestroyView`.
- **Test ID:** формат `SN015-YYYYMMDDTHHMMSSZ-XXXXXXXX`; генерируется на
  телефоне или вводится с Windows.
- **Ожидаемая частота:** выбирается из общего списка; анализатор ищет пик в
  окне ±300 Гц с порогом 6 дБ над медианой и вычисляет
  `frequency_error_hz = detected - expected`.
- **Состояния:** `READY`, `PERMISSION_REQUIRED`, `RECORDING`,
  `RECORDING_COMPLETE`, `ANALYZED`, `ERROR`. После записи показывается
  `RECORDING COMPLETE`, успешный приём не заявляется до анализа.
- **WAV:** RIFF/WAVE, PCM16 LE, mono; имя `sn015_<test_id>_rx.wav`;
  нормализация `/32768.0f`, как в `snlab/wavio.py`; SHA-256 сырых байт файла.
- **JSON:** обязательные top-level поля контракта
  `docs/hardware-calibration/SN-015-RESULT-FORMAT.md`; старые вложенные
  структуры (`device`, `hardware`, `test_parameters`, `analysis`, `wav`)
  сохранены. Имя `sn015_<test_id>_result.json`.
- Изменённые файлы: `AudioLabFragment.kt`, `AndroidAudioRecorderEngine.kt`,
  `AudioRouteInfo.kt` (новый), `AudioLabState.kt`, `CalibrationJsonReport.kt`,
  `AudioAnalyzer.kt`, `WavFileHandler.kt`, `fragment_audio_lab.xml`,
  `strings.xml`; тесты `CalibrationJsonReportTest`, `Sn015ContractTest`,
  `AudioAnalyzerTest`, `WavFileHandlerTest`.

### 9.2. Автоматизированные тесты

Команда: `.\gradlew.bat test --no-daemon`

- `BUILD SUCCESSFUL in 33s`
- Debug: **56 тестов**, failures `0`, errors `0`.
- Release: **56 тестов**, failures `0`, errors `0`.
- Всего: **112**, failures `0`, errors `0`.

Покрыто: контракт JSON (обязательные поля и типы), формат `test_id`,
`expected`/`detected`/`frequency_error_hz`, PCM16 и кратность фрейма,
sample rate, clipping, отсутствие PII в отчёте, lifecycle `AudioRecord`,
отсутствие автоматической записи, WAV/SHA-256.

### 9.3. Совместимость WAV с Python Acoustic Lab

Синтетический WAV, созданный Android unit-тестом, прочитан строгим
`acoustic-lab/snlab/wavio.py::read_pcm16`:

```text
sample_rate=48000
samples=48000
duration=1.000000
peak=0.250000
dominant_hz=18500.000
sha256=468a59852ff364c082fc03380e2d60359221c2f2b51e953d1573c5781110cf04
PYTHON_WAV_READ_OK
```

Размер файла — `96 044` байта. Проверка не открывала аудиоустройства.

### 9.4. Совместимость JSON с контрактом

Синтетический Android JSON проверен Python-скриптом:

```text
schema_version=silentnetwork.sn015.result/1.0
test_id=SN015-20261102T120000Z-A1B2C3D4
device_role=android_receiver
expected_hz=18500
detected_hz=18499.819
frequency_error_hz=-0.181
wav_sha256=468a59852ff364c082fc03380e2d60359221c2f2b51e953d1573c5781110cf04
samples=48000
PYTHON_JSON_CONTRACT_OK
```

Все обязательные поля присутствуют и имеют верные типы; `wav_sha256`
совпадает с фактическим WAV; `frequency_error_hz` согласован с
`detected - expected`. Windows Desktop Lab пока не импортирует Android JSON в
GUI — это зафиксировано как будущая доработка.

### 9.5. Сборка APK

Команда: `.\gradlew.bat assembleDebug --no-daemon`

- **Статус:** `BUILD SUCCESSFUL in 31s`
- **Путь:** `apps/android/app/build/outputs/apk/debug/app-debug.apk`
- **Размер:** `3 392 577` байт (~3.24 МБ)

### 9.6. Что не проверялось

- `AudioRecord.startRecording()` на физическом телефоне не запускался.
- `AudioTrack.play()` и воспроизведение через динамик не запускались.
- Реальный акустический тест `LAPTOP → PHONE` не выполнялся.
- Windows-импорт Android JSON не автоматизирован.

### 9.7. Статусы SN-015

| Компонент | Статус |
|---|---|
| `SN-015 WINDOWS DESKTOP` | **DONE** |
| `SN-015 ANDROID RECEIVER` | **DONE** (код и синтетические тесты) |
| `SN-015 WAV COMPATIBILITY` | **DONE** |
| `SN-015 JSON COMPATIBILITY` | **DONE** |
| `PHYSICAL LAPTOP → PHONE` | **PENDING** |

`DONE` относится к коду и форматам файлов, а не к подтверждённому приёму звука
на конкретном устройстве.

### 9.8. Финальная интеграция

- Android-ветка `agent1/sn015-android` (`5069513`) объединена в
  `agent1/m0-acoustic-lab` merge-коммитом `aba35cc`; конфликтов не было.
- Android-файлы в объединённой ветке идентичны `5069513`, Windows-код —
  `f497f66`.
- APK для Product Owner: `dist/SilentNetwork-Android.apk`.
- Windows-версия: `dist/SilentNetwork-Desktop/`.
- Инструкция первого физического теста:
  `docs/hardware-calibration/SN-015-FINAL-DELIVERY.md`.
- Числа 56/56 (112) ранее были получены независимым прогоном до merge. После
  merge тесты и сборка APK перезапущены: debug 56 + release 56 = `112`,
  failures 0, errors 0; APK `3 392 577` байт. Детали — в финальном отчёте и
  `docs/hardware-calibration/SN-015-FINAL-DELIVERY.md`.
- Физический тест `LAPTOP → PHONE` остаётся `PENDING` и требует отдельного
  разрешения Product Owner.
