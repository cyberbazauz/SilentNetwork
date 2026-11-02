# Полный аудит Android-окружения для SilentNetwork

**Дата проведения:** 2026-11-01  
**Ответственный агент:** Agent 2 — Gemini (Android & Application Developer)  
**Задача:** [SN-002](file:///c:/Projects/SilentNetwork/TASK_BOARD.md#L49)  
**Цель:** подготовка и верификация Android-окружения хоста для беспрепятственной сборки APK и последующей интеграции с Rust Core.

---

## 1. Итоговый вердикт

Окружение хоста **полностью готово к разработке Android-приложения и компиляции APK**.  
Фактическая сквозная тестовая сборка во временном каталоге (`$env:TEMP\silentnetwork_env_test`) успешно собрала реальный `app-debug.apk` (792 920 байт) за 51 секунду при помощи связки **JDK 17 + Gradle 8.3 + AGP 8.2.1 + Android SDK 34**. Никаких изменений в основной репозиторий при тестировании не вносилось.

Дополнительных установок JDK, Android SDK или Gradle для сборки проекта **не требуется**.

---

## 2. Что установлено и версии компонентов

| Компонент | Расположение | Версия | Статус | Примечание |
|---|---|---|---|---|
| **JDK 17** | `C:\jdk-17.0.2` | OpenJDK 17.0.2+8-86 (64-bit) | **Установлен** | Задан в системной переменной `$env:JAVA_HOME`. Совместим с AGP 8.x. |
| **Системная Java** | `C:\Program Files (x86)\Common Files\Oracle\Java\javapath` | JRE 1.8.0_511-b33 | Присутствует в PATH | Используется по умолчанию в PATH. Не подходит для сборки Android. Требует явной фиксации `org.gradle.java.home`. |
| **Android SDK** | `C:\Android\sdk` | — | **Установлен** | Задан в `$env:ANDROID_HOME`. |
| **SDK Platforms** | `C:\Android\sdk\platforms` | `android-34` (API 34), `android-35` (API 35) | **Установлены** | Покрывают современные требования Android 14 и Android 15. |
| **Build-Tools** | `C:\Android\sdk\build-tools` | `34.0.0`, `33.0.1` | **Установлены** | `aapt2`, `d8`, `zipalign`, `apksigner` присутствуют. |
| **Android NDK** | `C:\Android\sdk\ndk\27.0.12077973` | NDK r27 (27.0.12077973) | **Установлен** | Полный набор для кросс-компиляции C/C++ и Rust Core JNI (`.so`). |
| **Cmdline-tools** | `C:\Android\sdk\cmdline-tools\latest\bin` | latest | **Установлены** | `sdkmanager.bat`, `avdmanager.bat`, `apkanalyzer.bat`, `lint.bat`. |
| **Platform-tools** | `C:\Android\sdk\platform-tools` | adb 1.0.41 (37.0.1) | **Установлены** | `adb.exe`, `fastboot.exe`, `sqlite3.exe`. |
| **Лицензии SDK** | `C:\Android\sdk\licenses` | — | **Приняты** | Файлы `android-sdk-license` и др. на месте, повторное подтверждение не требуется. |
| **Gradle** | `C:\Users\user\.gradle\wrapper\dists` | 8.3 (распакован), 7.5, 7.5.1, 9.2.0, 9.3.1, 9.4.1 | **Доступен в кэше** | Готов к использованию через Gradle Wrapper (`gradlew`). |
| **AGP кэш** | `C:\Users\user\.gradle\caches\modules-2` | 8.1.0, 8.2.1, 8.3.1, 8.5.0, 8.5.1 | **Закэширован** | Позволяет собирать проект без длительного скачивания базовых плагинов. |
| **Kotlin кэш** | `C:\Users\user\.gradle\caches\modules-2` | 1.9.22, 2.1.10, 2.2.0 и др. | **Закэширован** | `kotlin-gradle-plugin` доступен локально. |

---

## 3. Какие команды реально запускались

1. **Проверка Java-окружения:**
   ```powershell
   & "$env:JAVA_HOME\bin\java.exe" -version
   # Результат: openjdk version "17.0.2" 2022-01-18, OpenJDK 64-Bit Server VM
   java -version
   # Результат: java version "1.8.0_511", Java(TM) SE Runtime Environment
   ```
2. **Проверка состава Android SDK:**
   ```powershell
   Get-ChildItem "C:\Android\sdk\platforms"
   Get-ChildItem "C:\Android\sdk\build-tools"
   Get-ChildItem "C:\Android\sdk\ndk"
   Get-ChildItem "C:\Android\sdk\cmdline-tools\latest\bin"
   Get-ChildItem "C:\Android\sdk\licenses"
   ```
3. **Проверка эмуляторов и устройств:**
   ```powershell
   $env:JAVA_HOME = "C:\jdk-17.0.2"
   & "C:\Android\sdk\cmdline-tools\latest\bin\avdmanager.bat" list avd
   # Результат: Available Android Virtual Devices: (список пуст)
   & "C:\Android\sdk\platform-tools\adb.exe" devices
   # Результат: List of devices attached (пусто)
   ```
4. **Проверка локального дистрибутива Gradle:**
   ```powershell
   $env:JAVA_HOME = "C:\jdk-17.0.2"
   & "C:\Users\user\.gradle\wrapper\dists\gradle-8.3-all\6en3ugtfdg5xnpx44z4qbwgas\gradle-8.3\bin\gradle.bat" -v
   # Результат: Gradle 8.3, JVM 17.0.2, OS Windows 11 10.0 amd64
   ```
5. **Изолированная сквозная сборка тестового Android-проекта:**
   - Во временном каталоге `C:\Users\user\AppData\Local\Temp\silentnetwork_env_test` сгенерирован минимальный Android-проект (`compileSdk 34`, `minSdk 26`, AGP 8.2.1, Kotlin 1.9.22, манифест с `hasCode="false"`).
   - Выполнена сборка:
     ```powershell
     $env:JAVA_HOME = "C:\jdk-17.0.2"
     & "C:\Users\user\.gradle\wrapper\dists\gradle-8.3-all\6en3ugtfdg5xnpx44z4qbwgas\gradle-8.3\bin\gradle.bat" assembleDebug --no-daemon
     ```
   - **Результат:** `BUILD SUCCESSFUL in 51s`, выполнена 31 задача.
   - **Полученный артефакт:** `app-debug.apk` размером 792 920 байт в каталоге `app\build\outputs\apk\debug`.
   - Временный каталог полностью удалён после проверки. Основной репозиторий `C:\Projects\SilentNetwork` не изменялся.

---

## 4. Какие проверки успешно пройдены

- [x] Наличие JDK 17 подтверждено, JVM работает стабильно.
- [x] Android SDK Platforms 34 и 35 установлены и валидны.
- [x] Android Build-Tools 34.0.0 работоспособны (d8, aapt2 отработали штатно).
- [x] Лицензии Android SDK приняты и не блокируют автоматическую сборку.
- [x] Дистрибутив Gradle 8.3 распакован, доступен и совместим с JDK 17.
- [x] Зависимости AGP 8.2.1 и Kotlin 1.9.22 находятся в локальном кэше Gradle.
- [x] Сквозная компиляция реального APK (`assembleDebug`) завершается со статусом `SUCCESSFUL`.
- [x] Android NDK 27.0.12077973 установлен для будущей сборки нативных библиотек.

---

## 5. Что отсутствует

1. **Gradle в системном PATH:** команда `gradle` не найдена напрямую.  
   *Решение:* стандартно для Android проектов — использование Gradle Wrapper (`gradlew.bat`), генерируемого в рамках задачи SN-004.
2. **Подключённые физические устройства:** ни один Android-смартфон не подключён по USB/Wi-Fi (`adb devices` пуст).
3. **Android Virtual Device (AVD) / Emulator:** утилита `emulator.exe` и системные образы (system-images) не установлены в SDK, виртуальные устройства не созданы.
4. **Контракт Android ↔ Core:** спецификация [docs/android-core-api.md](file:///c:/Projects/SilentNetwork/docs/android-core-api.md) отсутствует (задача SN-040 у Agent 1).
5. **Rust Toolchain:** `rustc` и `cargo` на хосте не установлены.

---

## 6. Что необходимо настроить / установить

1. **В проекте Android (при создании SN-004):**
   - В файле `gradle.properties` обязательно прописать:
     ```properties
     org.gradle.java.home=C:\\jdk-17.0.2
     ```
     Это предотвратит запуск Gradle через системную JRE 8 из PATH.
   - В файле `local.properties` (не коммитится в git) прописать:
     ```properties
     sdk.dir=C:\\Android\\sdk
     ```
2. **Для тестирования на этапе M1/M2:**
   - Подключить физический Android-смартфон с включённой «Отладкой по USB» для тестирования аудиотракта (`AudioRecord` / `AudioTrack`).
   - *Примечание:* Установка эмулятора не является приоритетной, так как виртуальный эмулятор не отражает реальную акустическую передачу через динамик и микрофон.

---

## 7. Возможные проблемы будущей сборки APK и интеграции

1. **Фоновая работа микрофона на Android 14+:**
   - Начиная с Android 14 (API 34), фоновый захват аудио требует обязательного запуска `Foreground Service` с типом `foregroundServiceType="microphone"` и соответствующим runtime permission. Без этого ОС завершает захват при блокировке экрана.
2. **Аппаратная обработка звука (AEC/NS/AGC):**
   - Встроенные алгоритмы шумоподавления и эхоподавления смартфонов оптимизированы под человеческую речь (300–3400 Гц) и могут агрессивно фильтровать частоты 16–20 кГц. В `AudioRecord` потребуется явно использовать аудиоисточник `MediaRecorder.AudioSource.VOICE_RECOGNITION` или `UNPROCESSED` (если поддерживается устройством) без применения программных эффектов.
3. **Сборка нативных библиотек Rust Core (`.so`):**
   - На хосте нет установленного Rust (`rustc`/`cargo`). Для интеграции Rust Core с Android через JNI потребуется либо установить Rust с Android-таргетами (`aarch64-linux-android` и др.) и утилитой `cargo-ndk`, либо получать предварительно собранные бинарные `.so` библиотеки от Agent 1 / CI. NDK версии 27 уже на месте и готов к линковке.
4. **Несовместимость Java в CLI:**
   - Любые скрипты автоматизации сборки должны явно передавать `JAVA_HOME="C:\jdk-17.0.2"`, чтобы не происходило сбоев из-за Java 8.

---

## 8. Статус готовности к следующей задаче

Окружение готово к выполнению **SN-004** (подготовка базового Android app skeleton с Gradle Wrapper, настройкой манифеста, разрешениями `RECORD_AUDIO` и экранами UI-каркаса).
