# Mirror API-first integration

## UC-MIR-005

**Когда:** нужно использовать read-only Mirror inventory или owner-controlled
Windows Pull через API/browser UI.

**Цель:** выбрать поддерживаемый source/runtime и проверить границы доступа.
Целевая единая архитектура и versioned schemas —
[MIRROR_API_CONTRACT_V1](../MIRROR_API_CONTRACT_V1.md), принятый P0 contract;
документ сам по себе не включает новые операции.

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
проверяется отдельно. P0/P2 source приняты owner merge; P3a добавляет шесть
source-only READ v2, P3b добавляет durable principal-bound preview/lookup.
Signed atomic reservation и P4 effects остаются открытыми задачами. Установка/активация v2 требуют отдельных gates. Нативная GitHub публикация Repo — только typed Gateway
по [UC-CONT-003](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/delivery.md#uc-cont-003).
При неизвестном backend или scope — UNKNOWN/STOP, не credential fallback.

**Готово:** source readiness и установленный Windows runtime проверены раздельно;
конкретная операция подтверждена либо её acceptance остаётся MANUAL-PENDING.

Unified READ v2 source/activation, named inventory scopes, pagination and remaining
principal-bound preview/lookup gates: [READ-V2](../READ-V2.md). Follow UC-GW-012
for transport/onboarding; source merge alone does not enable an operation.
