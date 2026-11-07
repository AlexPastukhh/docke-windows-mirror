# Mirror API-first integration

## UC-MIR-005

**Когда:** нужно использовать read-only Mirror inventory или owner-controlled
Windows Pull через API/browser UI.

**Цель:** выбрать поддерживаемый source/runtime и проверить границы доступа.

**Как:** открыть [API-FIRST](../API-FIRST.md) — единственного владельца
API, source map, команд запуска и acceptance. Source candidate находится
в `main`; Windows listener/TLS/WebAuthn и настоящий browser UI ещё требуют
owner acceptance. Сначала установить, какая версия реально запущена, какие
routes доступны и какие результаты имеют свежий source SHA. Browser read-only
UI не даёт AI Git write route. GitHub READ/публикация — только typed Gateway
по [UC-CONT-003](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/delivery.md#uc-cont-003).
При неизвестном backend или scope — UNKNOWN/STOP, не credential fallback.

**Готово:** source readiness и установленный Windows runtime проверены раздельно;
конкретная операция подтверждена либо её acceptance остаётся MANUAL-PENDING.
