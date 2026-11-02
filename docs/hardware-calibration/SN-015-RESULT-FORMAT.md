# SN-015 — единый формат результата WAV/JSON

**Задача:** SN-015 — Android Audio Lab Receiver + Windows Desktop Acoustic Lab  
**Версия контракта:** `silentnetwork.sn015.result/1.0`  
**Статус:** Android-экспорт реализован и проверен синтетикой; физический тест `LAPTOP → PHONE` остаётся `PENDING`  
**Дата:** 2026-11-02

> Контракт описывает **результат измерения**, а не доказательство передачи
> данных. Наличие WAV и совпадение частоты не означают PASS для модема,
> дальности 50–100 м или «неслышимости». Автоматический PASS не выставляется.

## 1. Область действия

Контракт применяется к паре файлов одного теста:

```text
<test_id>.wav   — запись микрофона Android (PCM16 mono)
<test_id>.json  — результат анализа этой записи
```

Windows Desktop Acoustic Lab создаёт стимул и playback JSON, Android Audio Lab
создаёт WAV и JSON приёма. Обмен выполняется только файлами: сетевой транспорт
SilentNetwork для этого не используется.

## 2. WAV

Обязательный формат:

| Свойство | Значение |
|---|---|
| Контейнер | RIFF/WAVE |
| Кодек | PCM без сжатия |
| Битность | 16 бит little-endian (`pcm_s16le`) |
| Каналы | 1 (mono) |
| Sample rate | фактический `AudioRecord` sample rate; обычно 48000 или 44100 Гц |
| `data` chunk | длина должна совпадать с числом полных PCM16-фреймов |
| Нормировка | `sample_i16 / 32768.0`, диапазон `[-1.0, 0.999969]` |

WAV обязан читаться строгим `acoustic-lab/snlab/wavio.py::read_pcm16`.
Обрезанный файл, неверный `fmt`, стерео или 8/24/32-бит считаются ошибкой
формата, а не «почти успешным» результатом.

`wav_sha256` — SHA-256 **сырых байт файла**, нижний регистр hex, 64 символа.
Он позволяет проверить, что анализировался именно экспортированный файл.

## 3. Обязательные top-level поля JSON

Все поля обязательны. Если значение неизвестно, используется `null`
(`JSONObject.NULL` в Kotlin), а не пустая строка и не вымышленное число.

| Поле | Тип | Единица | Описание |
|---|---|---|---|
| `schema_version` | string | — | Ровно `silentnetwork.sn015.result/1.0` |
| `test_id` | string | — | `SN015-YYYYMMDDTHHMMSSZ-XXXXXXXX` |
| `timestamp_utc` | string | UTC | ISO-8601, например `2026-11-02T12:00:00Z` |
| `device_role` | string | — | `android_receiver` (запись телефона) или `android_analyzer` (импорт WAV) |
| `expected_frequency_hz` | number \| null | Гц | Частота, выбранная на Windows до PLAY |
| `actual_sample_rate_hz` | integer | Гц | Фактический sample rate записи |
| `duration_seconds` | number | с | Длительность анализируемого сигнала |
| `rms_dbfs` | number | dBFS | RMS относительно полной шкалы |
| `peak_dbfs` | number | dBFS | Абсолютный пик |
| `dominant_frequency_hz` | number | Гц | Глобальный доминирующий пик спектра |
| `clipping_count` | integer | отсчёты | Число отсчётов на пределе шкалы |
| `wav_filename` | string \| null | — | Имя WAV без пути |
| `wav_sha256` | string \| null | — | SHA-256 сырых байт WAV |
| `warnings` | array of string | — | Предупреждения; пустой массив допустим |

Дополнительные поля Android:

| Поле | Тип | Описание |
|---|---|---|
| `requested_sample_rate_hz` | integer | Что запросили у `AudioRecord` |
| `detected_frequency_hz` | number \| null | Пик в окне ±300 Гц вокруг ожидаемой частоты |
| `frequency_error_hz` | number \| null | `detected - expected` |
| `report_schema_version` | string | Алиас `schema_version` для старых потребителей |

`expected_frequency_hz`, `detected_frequency_hz` и `frequency_error_hz`
образуют согласованную тройку:

```text
frequency_error_hz = detected_frequency_hz - expected_frequency_hz
```

Если пик не найден, `detected_frequency_hz` и `frequency_error_hz` равны
`null`, а в `warnings` добавляется запись о том, что приём не подтверждён.

## 4. `test_id`

Формат:

```text
SN015-YYYYMMDDTHHMMSSZ-XXXXXXXX
```

- `YYYYMMDDTHHMMSSZ` — время создания ID в UTC;
- `XXXXXXXX` — 8 hex-символов в верхнем регистре.

Android генерирует ID локально и позволяет заменить его значением, показанным
на Windows. Один `test_id` используется для WAV, JSON и имени экспортируемых
файлов. Сеть для синхронизации не нужна.

## 5. Смысл полей приёмника

`dominant_frequency_hz` — самый сильный пик во всём спектре. Он может быть
посторонним шумом.

`detected_frequency_hz` — максимум в окне `expected ± 300 Гц`. Пик считается
значимым только при превышении медианы окна не менее чем на 6 дБ и уточняется
параболической интерполяцией. Если условие не выполнено, возвращается `null`.

Поэтому:

- близость `detected_frequency_hz` к `expected_frequency_hz` — один из
  показателей, но **не PASS**;
- `detected_frequency_hz = null` при наличии `dominant_frequency_hz` означает,
  что ожидаемого тона в окне нет;
- `rms_dbfs` и `peak_dbfs` описывают только данный файл, а не помещение.

## 6. Совместимость с Windows Desktop Lab

Windows Desktop Lab (`acoustic-lab/desktop_app.py`, commit `f497f66`) сейчас
создаёт собственные JSON со схемами:

- `silentnetwork.acoustic-lab.desktop-tone/1` — параметры стимула;
- `silentnetwork.acoustic-lab.desktop-playback/1` — факт PLAY;
- `silentnetwork.acoustic-lab.phone-analysis/1` — анализ импортированного WAV.

Эти документы **не переименовываются**: Android-контракт добавляет
унифицированные top-level поля, а старые вложенные структуры Android
(`device`, `hardware`, `test_parameters`, `analysis`, `wav`) сохраняются.

Минимальное соответствие:

| Android SN-015 | Windows phone-analysis | Примечание |
|---|---|---|
| `actual_sample_rate_hz` | `sample_rate` | Одно и то же значение Fs |
| `duration_seconds` | `duration_s` | Секунды |
| `rms_dbfs` | `channels[].summary.level.rms_dbfs` | Android считает по всему файлу |
| `peak_dbfs` | `channels[].summary.level.peak_dbfs` | dBFS |
| `clipping_count` | `channels[].summary.clipping.clipped_samples` | Целое число |
| `dominant_frequency_hz` | `global_dominant_frequency_hz` | Глобальный пик |
| `detected_frequency_hz` | `detected_frequency_hz` | Поиск в окне ±300 Гц |
| `frequency_error_hz` | `frequency_offset_hz` | `detected - expected` |
| `wav_sha256` | `source_file.sha256` | SHA-256 сырых байт |
| `warnings` | `warnings` | Массив строк |

Правила чтения для будущего Windows-импортёра Android JSON:

1. Проверить `schema_version`; неизвестная major-версия — ошибка, а не догадка.
2. Проверить типы обязательных полей из раздела 3.
3. Принять `null` как «не измерено», не подставлять 0.
4. Игнорировать незнакомые дополнительные поля.
5. Сверить `wav_sha256` с фактическим WAV перед доверием метрикам.
6. Не выставлять PASS автоматически.

Android JSON уже содержит все обязательные поля в top-level и читается
стандартным `json` без ручного преобразования. Проверка выполняется скриптом
`sn015_android_json_check.py` (временный артефакт проверки, не входит в APK).

## 7. Пример Android JSON (сокращённо)

```json
{
  "schema_version": "silentnetwork.sn015.result/1.0",
  "test_id": "SN015-20261102T120000Z-A1B2C3D4",
  "timestamp_utc": "2026-11-02T12:00:00Z",
  "device_role": "android_receiver",
  "expected_frequency_hz": 18500.0,
  "actual_sample_rate_hz": 48000,
  "requested_sample_rate_hz": 48000,
  "duration_seconds": 1.0,
  "rms_dbfs": -18.06,
  "peak_dbfs": -12.04,
  "dominant_frequency_hz": 18499.8,
  "detected_frequency_hz": 18499.8,
  "frequency_error_hz": -0.2,
  "clipping_count": 0,
  "wav_filename": "sn015_sn015-20261102t120000z-a1b2c3d4_rx.wav",
  "wav_sha256": "468a59852ff364c082fc03380e2d60359221c2f2b51e953d1573c5781110cf04",
  "warnings": [],
  "device": {"manufacturer": "Google", "model": "Pixel", "android_release": "14", "sdk_int": 34},
  "hardware": {
    "requested_sample_rate_hz": 48000,
    "actual_sample_rate_hz": 48000,
    "requested_device": "builtin_mic",
    "actual_routed_device": "builtin_mic"
  },
  "wav": {"filename": "sn015_sn015-20261102t120000z-a1b2c3d4_rx.wav", "sha256": "468a..."}
}
```

## 8. Ограничения

- Контракт не заменяет физический тест и не подтверждает поддержку частоты.
- `actual_routed_device` может быть `null`: Android не всегда сообщает маршрут.
- `expected_frequency_hz` может быть `null` при анализе импортированного WAV.
- Windows Desktop Lab пока не импортирует Android JSON в GUI; он импортирует
  WAV и создаёт свой `*.analysis.json`. Совместимость полей зафиксирована этим
  документом и проверена Python-скриптом на синтетическом Android JSON.
- SNR одиночной записи не вычисляется: `snr_db = null` / `SNR = N/A`.
