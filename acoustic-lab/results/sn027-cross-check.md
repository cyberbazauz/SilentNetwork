# SN-027: кросс-проверка Python ↔ Kotlin

- Дата: 2026-11-04 19:53 UTC
- Канал: файловый L0 (WAV), не воздух.
- Проверка Data Link: Python WAV → Kotlin-декодер и Kotlin WAV → Python-декодер.
- Проверка File Transfer v0: Python-файл → Kotlin-декодер → файл и SHA-256, затем Kotlin-файл → Python-декодер.
- Рабочий каталог: `C:\Projects\SilentNetwork\acoustic-lab\results\cross-check`
- Команда Kotlin: `C:\Projects\SilentNetwork\apps\android\gradlew.bat :app:testDebugUnitTest --tests org.silentnetwork.app.audiolab.datalink.CrossLanguageTest -PsnCrosscheckDir=C:\Projects\SilentNetwork\acoustic-lab\results\cross-check --rerun-tasks --console=plain`

## Data Link

| Кейс | Профиль | Python → Kotlin | Kotlin → Python | Преамбула | CRC-16 |
|---|---|---|---|---|---|
| ascii | data-audible | OK | OK | 32/32 | 0xF6D0 |
| cyrillic | data-audible | OK | OK | 32/32 | 0xD8D1 |
| mixed | data-robust | OK | OK | 32/32 | 0xF649 |
| max64 | data-robust | OK | OK | 32/32 | 0x24CA |

## File Transfer v0

| Кейс | Профиль | Имя | Размер | Чанки | Python → Kotlin | Kotlin → Python | SHA-256 |
|---|---|---|---|---|---|---|---|
| text | data-audible | py-note.txt | 40 Б | 1 | OK | OK | `65772a52d6f3…` |
| cyrillic | data-robust | привет.txt | 23 Б | 1 | OK | OK | `08b9d447e274…` |
| binary | data-audible | bytes.bin | 120 Б | 3 | OK | OK | `f52b23db1fbb…` |
| empty | data-audible | empty.txt | 0 Б | 0 | OK | OK | `e3b0c44298fc…` |

**Итог: PASS (8/8 кейсов в обе стороны, из них файловых 4).**

CRC-16 обнаруживает ошибки, но не является аутентификацией. SHA-256 проверяет целостность файла, но не аутентифицирует отправителя. Результат не подтверждает передачу через воздух.
