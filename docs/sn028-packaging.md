# SN-028 — сборка standalone Windows EXE

**Дата:** 2026-11-04  
**Статус:** сборка и проверка запуска выполнены; передача через воздух не
выполнялась  
**Связанные:** `README.md`, `docs/sn028-physical-test-protocol.md`,
`TASK_BOARD.md`

Документ описывает, как из `acoustic-lab/desktop_app.py` получается
`dist/SilentNetwork-Desktop.exe`, что именно проверено и какие ограничения
остаются.

## 1. Что умеет EXE

Standalone Windows-приложение (Python не требуется) содержит те же функции, что
и `acoustic-lab/desktop_app.py`:

- генерация WAV с текстовым кадром Frame v1 и профилями `data-audible` /
  `data-robust`;
- воспроизведение WAV через выбранный выходной endpoint;
- запись выбранного входа и декодирование кадра с проверкой CRC-16;
- сохранение WAV и JSON-отчётов;
- передача и приём файла 0…8192 байт через файловый слой v0
  (MANIFEST/DATA/END, SHA-256);
- вкладка «Чат»: непрерывное прослушивание микрофона, отправка текста и файла
  сразу в звук, полудуплекс и инкрементальный приём Frame v1;
- разделы сканера, тонов и анализа записи телефона.

EXE **не** конвертирует произвольные аудиофайлы: передаются либо текстовый
кадр Frame v1 (payload ≤ 64 байта UTF-8), либо файл через отдельный файловый
слой. Приём файла 8192 байта требует записи около 420 с; успешный приём
такого файла через воздух ещё не подтверждён.

## 2. Инструменты

| Компонент | Версия |
|---|---|
| Python | 3.12 (Store-сборка) |
| PyInstaller | 6.22.3 (build-only, в runtime не входит) |
| pyinstaller-hooks-contrib | 2026.9 |
| numpy / scipy / sounddevice | из `acoustic-lab/requirements.txt` |

PyInstaller не добавлен в runtime-зависимости проекта: он нужен только для
сборки.

## 3. Команда сборки

```powershell
cd C:\Projects\SilentNetwork\acoustic-lab
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name "SilentNetwork-Desktop" `
  --paths "C:\Projects\SilentNetwork\acoustic-lab" `
  --distpath "C:\Projects\SilentNetwork\dist" `
  --workpath "$env:TEMP\pyinstaller-build" `
  --specpath "$env:TEMP\pyinstaller-spec" `
  --exclude-module torch --exclude-module torchvision --exclude-module torchaudio `
  --exclude-module pandas --exclude-module matplotlib --exclude-module IPython `
  --exclude-module jupyter --exclude-module notebook --exclude-module pytest `
  --exclude-module sphinx `
  "C:\Projects\SilentNetwork\acoustic-lab\desktop_app.py"
```

`--exclude-module` обязателен: без него hooks затягивают `torch`, `pandas` и
`matplotlib`, и файл вырастает с 66 МБ до 234 МБ. Runtime-код эти пакеты не
использует.

## 4. Каталог результатов

PyInstaller onefile распаковывает код во временный `_MEIPASS`, который
удаляется после закрытия. Поэтому `snlab/desktop.py` и
`snlab/datalink_desktop.py` при `sys.frozen` пишут результаты рядом с `.exe`:

```text
dist/
├── SilentNetwork-Desktop.exe
└── results/
    └── hardware_calibration/
```

В обычном режиме (`python desktop_app.py`) каталог остаётся
`acoustic-lab/results/hardware_calibration/`. Поведение закреплено тестами
`acoustic-lab/tests/test_sn028_packaging.py`.

## 5. Фактическая проверка

| Проверка | Команда | Результат |
|---|---|---|
| Файл собран | `Get-Item dist\SilentNetwork-Desktop.exe` | 66 531 101 байт, 2026-11-04 22:18:00 |
| SHA-256 EXE | `Get-FileHash dist\SilentNetwork-Desktop.exe -Algorithm SHA256` | `1733F5E5D3DA3DBDBBAC97726D14DB33C4CACC0BBE996C8D35BF31A7A923DE7A` |
| Smoke-test | `dist\SilentNetwork-Desktop.exe --smoke-test` | exit 0 |
| Окно открывается | запуск без аргументов, ожидание 12 с | процесс жив, заголовок «SilentNetwork — Акустическая лаборатория» |
| Python-тесты | `cd acoustic-lab; python -m pytest -q` | **644 passed** |
| Android unit-тесты | `gradlew testDebugUnitTest testReleaseUnitTest` | **200** запусков, failures 0, errors 0, skipped 2 |
| Android APK | `gradlew assembleDebug` | 3 624 071 байт, SHA-256 `F51890674EE4F1E5DF3F0DA70FD897F25B6DE7D5E61722C2A6E222645AC12601` |

EXE пересобран 2026-11-04 после добавления вкладки «Чат». Первая сборка без
исключений дала 234 873 087 байт; smoke-test тоже проходил, но размер признан
неприемлемым, поэтому сборка переделана с исключениями.

## 6. Ограничения

- EXE не подписан сертификатом; Windows SmartScreen может показать
  предупреждение.
- Defender/антивирус может проверять onefile-архив при первом запуске.
- Аудиоустройства те же, что у Python-версии: политика автовыбора не
  предпочитает Bluetooth и виртуальные устройства.
- Встроенный микрофон ноутбука нерабочий; приём на ноутбуке не проверялся.
- Вкладка «Чат» проверена только синтетическим PCM и фейковым захватом;
  работа на реальном телефоне и передача через воздух не выполнялись.
- Передача Laptop → Phone через воздух не выполнялась: Android не подключён.
  Smoke-test, запуск окна и файловый round-trip **не** являются доказательством
  акустической передачи.
