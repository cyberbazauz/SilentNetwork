# SN-027: кросс-проверка Python ↔ Kotlin

- Дата: 2026-11-03 03:27 UTC
- Канал: файловый L0 (WAV), не воздух.
- Проверка: Python WAV → Kotlin-декодер и Kotlin WAV → Python-декодер.
- Рабочий каталог: `C:\Projects\SilentNetwork\acoustic-lab\results\cross-check`
- Команда Kotlin: `C:\Projects\SilentNetwork\apps\android\gradlew.bat :app:testDebugUnitTest --tests org.silentnetwork.app.audiolab.datalink.CrossLanguageTest -PsnCrosscheckDir=C:\Projects\SilentNetwork\acoustic-lab\results\cross-check --rerun-tasks --console=plain`

| Кейс | Профиль | Python → Kotlin | Kotlin → Python | Преамбула | CRC-16 |
|---|---|---|---|---|---|
| ascii | data-audible | OK | OK | 32/32 | 0xF6D0 |
| cyrillic | data-audible | OK | OK | 32/32 | 0xD8D1 |
| mixed | data-robust | OK | OK | 32/32 | 0xF649 |
| max64 | data-robust | OK | OK | 32/32 | 0x24CA |

**Итог: PASS (4/4 кейсов в обе стороны).**

CRC-16 обнаруживает ошибки, но не является аутентификацией. Результат не подтверждает передачу через воздух.
