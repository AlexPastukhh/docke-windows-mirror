# Mirror API-first integration

## UC-MIR-005

**Когда:** нужно использовать read-only Mirror inventory или owner-controlled
Windows Pull через API/browser UI.

**Цель:** выбрать поддерживаемый source/runtime и проверить границы доступа.
Целевая единая архитектура и versioned schemas —
[MIRROR_API_CONTRACT_V1](../MIRROR_API_CONTRACT_V1.md), P0 proposal для security
review; документ сам по себе не включает новые операции.

**Как:** открыть [API-FIRST](../API-FIRST.md) — единственного владельца
API и browser behavior; owner-only Windows Gateway READ startup описан в
[Gateway READ runbook](../GATEWAY-READ-ONLY.md). Source candidate находится
в `main`; внедрение ручного Browser Pull и отдельного Windows listener/TLS/WebAuthn
требуют owner acceptance. Сначала установить, какая версия реально запущена, какие
routes доступны и какие результаты имеют свежий source SHA. Browser UI предоставляет локальное Preview → owner-confirmed Windows Pull, но
**не даёт ИИ Git write route**. Принятый Gateway provider имеет строгий
`get_repository` v1 READ; source/catalog readiness не доказывает Windows TLS
или живой upstream. Отдельный удалённый Gateway ASK остаётся выключен.
P1 source использует [Windows owner-managed gh reader](../WINDOWS-GITHUB-READ.md)
для browser main/PR/CI и свежего Pull preflight; actual Windows acceptance
проверяется отдельно. P0 принят owner merge; P2/P3/P4 и live machine scope
остаются gated. Нативная GitHub публикация Repo — только typed Gateway
по [UC-CONT-003](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/delivery.md#uc-cont-003).
При неизвестном backend или scope — UNKNOWN/STOP, не credential fallback.

**Готово:** source readiness и установленный Windows runtime проверены раздельно;
конкретная операция подтверждена либо её acceptance остаётся MANUAL-PENDING.
