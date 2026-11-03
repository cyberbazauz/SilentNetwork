# Acoustic Data Link v0 — контракт

**Статус:** DRAFT (SN-024…SN-027 реализованы; физический тест SN-028 — `PENDING`)  
**Дата:** 2026-11-03  
**Связанные:** `docs/acoustic-data-link-plan.md`, `docs/decisions/ADR-001-acoustic-frame-v1.md`, `TASK_BOARD.md`

## 1. Назначение
Acoustic Data Link v0 — первый рабочий обмен реальными данными через звук (UTF-8 текст) поверх существующего кадра **Frame v1**. Цель — доказать передачу через воздух, не вводя SCNP/ACK/повторы/криптографию.

## 2. Профили передачи
Для первого доказательства используется слышимый диапазон:

| Профиль | Fs, Гц | mark / space, Гц | Символьная скорость | Амплитуда | Назначение |
|---|---|---|---|---|---|
| `data-audible` | 48000 | 1200 / 2200 | 300 бод | 0.30 | основной для Windows↔Android |
| `data-robust` | 48000 | 1000 / 2000 | 100 бод | 0.30 | запасной при высоком PER |
| `bfsk-18k` | 48000 | 18000 / 19000 | 300 бод | как задано | только сравнение, не критерий приёмки |

Оба data-профиля совместимы с 44100 Гц. Валидация `BfskProfile` обязательна (Найквист, разнос ≥ символ.скорости, `sample_rate % symbol_rate == 0`).

Параметры воспроизведения для v0: ramp 5 мс, тишина перед кадром 1000 мс, тишина после кадра 500 мс (рекомендуется).

## 3. Формат кадра
**Frame v1 не изменяется.** Структура перед модуляцией (биты MSB-first):

```
преамбула 32 бита (0101...) | sync 0x1ACF 16 | версия 0x01 8 | длина payload 16 BE | payload N байт | CRC-16/CCITT-FALSE 16
```

- Payload — UTF-8 байты текста. Пустой payload запрещён.
- Для v0 максимум **64 байта** UTF-8.
- Полный лимит Frame v1 (512 байт) остаётся, но не используется в приёмке.
- CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflect). CRC **обнаруживает ошибки, не аутентифицирует**.

## 4. Модуль `snlab/datalink.py` (контракт)
Модуль реализует преобразование текст↔кадр↔PCM и обратное преобразование с человекочитаемыми ошибками.

### 4.1 API (предложение)
```python
class DataLinkError(Exception): ...
class DataLinkPayloadError(DataLinkError): ...
class DataLinkProfileError(DataLinkError): ...

@dataclass(frozen=True)
class DataLinkFrame:
    text: str
    payload: bytes
    frame: "snlab.frame.Frame"
    profile: "snlab.config.BfskProfile"

def text_to_payload(text: str, *, max_bytes: int = 64) -> bytes:
    """Преобразовать UTF-8 текст в payload. Проверить длину."""

def payload_to_text(payload: bytes) -> str:
    """Декодировать payload как UTF-8. При ошибке — DataLinkError."""

def encode_text_to_wav(
    text: str,
    profile: "snlab.config.BfskProfile",
    out_path: str | Path,
    *,
    max_bytes: int = 64,
    lead_silence_s: float = 1.0,
    tail_silence_s: float = 0.5,
    ramp_ms: float = 5.0,
) -> dict[str, Any]:
    """Собрать кадр, смодулировать, добавить lead/tail тишину, записать WAV.
    Вернуть JSON-совместимый отчёт."""

def decode_wav_to_text(
    path: str | Path,
    profile: "snlab.config.BfskProfile",
    *,
    min_preamble_matches: int = 32,
    max_payload_bytes: int = 64,
) -> dict[str, Any]:
    """Декодировать WAV в текст с человекочитаемым вердиктом.
    Вернуть dict с полями: ok, text|error, error_type, profile, ..."""
```

Ограничения: UTF-8 до 64 байт, пустой текст запрещён. Ошибки маппятся на человекочитаемые сообщения.

## 5. Приёмник: режим `acoustic`
В `snlab/receiver.py` добавить параметр `min_preamble_matches: int = 32` (по умолчанию не меняет поведение).

Поведение:
- Для файловых тестов/benchmark по умолчанию `32/32` (жёсткий).
- Для реального акустического канала (режим `acoustic`) можно передавать `28/32`. 
- Оконательное решение — по sync word, версии, длине, CRC. Если CRC не совпал — кадр не принят.
- При несовпадении преамбулы выбрасывать `SignalNotFound` с понятным текстом, содержащим `min_preamble_matches`.

Параметр должен прокидываться через `find_frame_start`/`decode_*` без скрытого изменения глобальных настроек.

## 6. Человекочитаемые ошибки приёма
Приёмник/декодер должны различать и объяснять:
- `signal_not_found` — сигнал не найден / преамбула не найдена
- `preamble_too_few` — преамбула найдена, но недостаточно совпадений
- `sync_mismatch` — преамбула найдена, но sync word не совпал
- `version_unsupported` — неподдерживаемая версия
- `length_invalid` — длина вне допустимого диапазона (1..max_payload_bytes)
- `incomplete_frame` — запись обрезана, не хватает символов
- `crc_mismatch` — CRC не совпал
- `wav_format_error` / `empty_recording` — неверный формат WAV/пустая запись
- `payload_utf8_error` — payload не является валидным UTF-8 (для data-link)

## 7. Тестовые векторы (кросс-языковая совместимость)
Единый набор векторов сохраняется в:
- `acoustic-lab/tests/fixtures/data-link-vectors.json`
- копия в `apps/android/app/src/test/resources/data-link-vectors.json`

Векторы должны включать:
- `ascii` — `SILENTNETWORK`
- `cyrillic` — UTF-8 кириллица (например `Проверка связи`)
- `max64` — payload ровно 64 байта
- `empty` / `too_long65` — негативные (построение должно завершиться ошибкой)
- `corrupted_crc` — повреждённый CRC (декодирование должно вернуть `crc_mismatch`)
- `truncated` — обрезанный кадр (декодирование должно вернуть `incomplete_frame`)

Для каждого вектора: `text`, `payload_hex`, `payload_sha256`, `profile`, `frame` (версия/sync/длина/crc/overhead), `bits_hex` или просто проверка по кадру. 
Совместимость проверяется **декодированием** (PCM SHA-256 может отличаться между ОС из-за `np.sin`/libm).

## 8. JSON-отчёт data-link
Схема: `silentnetwork.acoustic-lab.data-link/1`

Обязательные поля для попытки декодирования: `direction` (tx|rx), `profile`, `timestamp`, `result` (ok|error), `error_type`, `wav_path`, `duration_s`, `metrics` (rms/peak/clipping при наличии), `device_params`.
Для успеха: `text`, `payload_hex`, `payload_sha256`, `crc_ok`, `start_time_s`, `preamble_matches`, `decoded_frame`.

## 9. Критерии приёмки SN-024
- [ ] `docs/acoustic-data-link.md` создан и согласован с планом
- [ ] `snlab/datalink.py` реализован, API соответствует контракту
- [ ] `snlab/receiver.py` поддерживает `min_preamble_matches` (по умолчанию 32), поведение существующих тестов не нарушено
- [ ] Тестовые векторы созданы в обеих локациях
- [ ] `tests/test_datalink.py` добавлен, все тесты проходят
- [ ] `python -m pytest tests -q` проходит без регрессий
- [ ] Документация обновлена при необходимости

**Примечание:** на этом этапе только контракт и Python-ядро. Реальный воздух-тест — SN-028.

## 10. Windows Desktop Lab (SN-025)
`snlab/datalink_desktop.py` связывает data link с устройствами Windows и отчётами:

| Функция | Назначение |
|---|---|
| `list_input_devices()` | перечислить микрофоны без открытия потока |
| `get_data_profile(name)` | разрешить только `data-audible`/`data-robust` |
| `generate_data_message(...)` | текст → WAV + JSON, без воспроизведения |
| `play_data_message(...)` | PLAY через движок после `hardware_test=True`, JSON после попытки |
| `receive_data(...)` | запись микрофона → WAV + JSON + декодирование |

Схемы отчётов:

- `silentnetwork.acoustic-lab.data-link-playback/1` — фактический PLAY: устройство, файл, SHA-256, кадр, результат движка;
- `silentnetwork.acoustic-lab.data-link-receive/1` — запись и декодирование: capture-метрики, `result`, `error_type`, `text`, `crc_ok`.

UI: карточки «Передать сообщение» и «Принять сообщение» в `desktop_app.py`. PLAY требует подтверждения; запись начинается только по кнопке; системная громкость не меняется. Ошибки приёма показываются человеческим вердиктом и машинным `error_type`.

Ограничения: успешный PLAY означает воспроизведение WAV, а не приём. Запись микрофона в том же помещении ещё не является доказательством дальности или устойчивости. CRC-16 не аутентифицирует отправителя.

## 11. Критерии приёмки SN-025
- [x] Карточки «Передать сообщение» и «Принять сообщение» в Windows Desktop Lab
- [x] Подтверждение перед PLAY и запись только по кнопке
- [x] Человекочитаемый вердикт и машинный `error_type`
- [x] WAV + JSON для передачи и приёма
- [x] Тесты с фейковыми движком и записью (`tests/test_sn025_datalink_desktop.py`)
- [x] `python -m pytest tests -q` проходит без регрессий
- [ ] Физическая передача через воздух — SN-028, отдельный отчёт

## 12. Android: BFSK + Frame v1 (SN-026)
Пакет `apps/android/app/src/main/java/org/silentnetwork/app/audiolab/datalink/`:

| Файл | Назначение |
|---|---|
| `BfskProfile.kt` | `data-audible` и `data-robust` с теми же параметрами, что в `snlab/config.py` |
| `FrameV1.kt` | преамбула `1010…`, sync `0x1ACF`, версия `1`, длина 16 BE, payload, CRC-16/CCITT-FALSE |
| `BfskModulator.kt` | CPFSK с непрерывной фазой, ramp 5 мс; биты — `ByteArray` 0/1 |
| `BfskDemodulator.kt` | некогерентная энергия mark/space на окне символа, поиск преамбулы по фазам, уточнение, проверка sync/version/length/CRC |
| `DataLinkCodec.kt` | текст ↔ payload ↔ PCM/WAV, отчёты JSON, строгий UTF-8, лимит 64 байта |

UI: всегда развёрнутая карточка «Передача данных» в Audio Lab. PLAY требует подтверждения; запись начинается только по кнопке и имеет фиксированную длительность 5/10/15 с. Ручная остановка — отмена записи, частичный буфер не декодируется и приём не заявляется. Для реального канала используется порог преамбулы 28/32 (`ACOUSTIC_MIN_PREAMBLE_MATCHES`), для файлов — 32/32.

JSON-отчёт Android следует схеме `silentnetwork.acoustic-lab.data-link/1` с одним отличием: вместо локального `wav_path` пишется объект `wav` с `filename` и `sha256`, потому что Android работает через scoped storage и выбранный пользователем URI не является стабильным путём. `crc_is_authentication` всегда `false`. `payload_utf8_error` сохраняет `crc_ok=true` и кадр: целостность подтверждена, текст — нет.

Ограничения: успешный PLAY означает воспроизведение WAV, а не приём; запись на телефоне в той же комнате не доказывает дальность. Если `AudioRecord` вынужденно отдал 44 100 Гц вместо 48 000, декодер вернёт `wav_format_error`, а не будет молча ресемплировать.

## 13. Автоматическая кросс-проверка Python ↔ Kotlin (SN-027)
`acoustic-lab/cross_check_android.py`:

1. создаёт WAV Python-кодеком для 4 кейсов: ASCII `data-audible`, кириллица `data-audible`, mixed `data-robust`, 64 байта `data-robust`;
2. запускает `CrossLanguageTest` через Gradle с `-PsnCrosscheckDir=<каталог>`;
3. Kotlin декодирует Python-WAV и создаёт свои WAV тем же профилем;
4. Python декодирует Kotlin-WAV и сверяет текст, `crc_ok` и преамбулу 32/32;
5. пишет Markdown-отчёт в `acoustic-lab/results/sn027-cross-check.md`.

Фактический прогон 2026-11-03: **PASS 4/4 в обе стороны**. Без `-PsnCrosscheckDir` тест Kotlin пропускается, поэтому обычный `gradlew test` не зависит от внешних файлов. Проверка относится только к файловому каналу L0 и не заменяет физический тест.
