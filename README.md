# SilentNetwork

Автономная акустическая система связи: данные передаются от динамика к
микрофону через звуковую волну. Интернет, Wi-Fi, Bluetooth и сотовая связь не
используются как транспорт данных и не являются резервным каналом.

**Текущее состояние: исследовательский этап.** Работающего акустического
модема, криптографии, протокола SCNP, mesh-сети и Android-приложения пока нет.
Ни одна передача через воздух не выполнялась и не подтверждена.

## Что уже есть

| Область | Состояние |
|---|---|
| Нормативные документы проекта | есть (`AGENTS.md`, `GEMINI.md`, `TASK_BOARD.md`) |
| Аудит репозитория и окружения | выполнен: `docs/audit/2026-11-01-repository-audit.md` |
| Исходная архитектура v1.0 | каноническая копия в `docs/reference/` |
| Acoustic Lab: инфраструктура | есть и покрыта тестами (`acoustic-lab/snlab/`) |
| Acoustic Lab: сканер устройств и фона | реализован, реальный захват 10 с проверен (SN-010) |
| Acoustic Lab: BFSK-передатчик (WAV) | реализован, 20/20 одинаковых SHA-256 (SN-011) |
| BFSK-приёмник и синхронизация | не реализованы (SN-021) |
| Реальная передача Windows ↔ Windows | не выполнялась |
| Rust Core, SCNP, криптография | не реализованы |
| Android-приложение | не реализовано (зона Gemini) |

## Структура репозитория

```text
SilentNetwork/
├── AGENTS.md                     # правила Agent 1 (Chief Engineer)
├── GEMINI.md                     # правила Agent 2 (Android)
├── PROMPT_AGENT_1.txt            # стартовый промпт Agent 1
├── PROMPT_GEMINI.txt             # стартовый промпт Agent 2
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
    ├── receiver.py               # SN-021 (пока только интерфейс)
    └── benchmark.py              # SN-013 (пока только интерфейс)
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

Все тесты проверяют инфраструктуру (конфигурацию, WAV, метрики, AWGN),
анализаторы сканера и BFSK-передатчик; аудиобэкенд в тестах подменяется
фейком, реальный микрофон и динамик не открываются.

## Документы

- `docs/current-milestone.md` — milestone M0 и 11 измеримых критериев приёмки.
- `docs/decisions/ADR-001-acoustic-frame-v1.md` — формат кадра, CRC-16 и
  BFSK-модуляция Acoustic Lab.
- `docs/audit/2026-11-01-repository-audit.md` — состояние репозитория,
  окружения, риски и блокеры.
- `docs/reference/SilentNetwork_Architecture_v1.0.txt` — утверждённая
  архитектура, SHA-256 `A8849A52A260A07B1DB74438F9AA9B6458551A76065086DFE328E0525EE6E9D5`.
- `docs/reference/README.md` — правила работы с каноническими документами.

## Чего нельзя утверждать

- Что SilentNetwork уже передаёт данные через воздух.
- Что подтверждены дальность, скорость или неслышимость сигнала.
- Что работает на любом оборудовании.
- Что CRC заменяет аутентифицированное шифрование.

Показатели из Architecture v1.0 (10–50 м, 5–15 кбит/с и др.) — целевые
гипотезы до стендовых измерений.

## Роли

- **Agent 1** — архитектура, Acoustic Lab, DSP/PHY, Rust Core, SCNP, mesh.
- **Gemini** — Android-приложение, аудиоадаптер, интеграция по утверждённому API.
- **Product Owner** — решения по требованиям, контрактам и приёмке этапов.

## Лицензия

Не выбрана. До публикации требуется решение Product Owner.
