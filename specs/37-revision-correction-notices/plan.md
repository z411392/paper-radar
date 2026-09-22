# 把重要修訂與更正通知給曾經收到研究的人：技術方案

Story：https://github.com/z411392/paper-radar/issues/37。方案基線，非已凍結的 implementation contract。

## 依賴與 ownership

Context 及程式邊界：[Context Map](../../docs/architecture/context-map.md)。SQL／檔案／index 契約：[data model](../../docs/data-model.md)。

直接上游情境：https://github.com/z411392/paper-radar/issues/13, https://github.com/z411392/paper-radar/issues/21。這是資料／能力相依，不要求無關 Task 完成。

| Task | owner | 預定實作位置 | SC／AC |
|---|---|---|---|
| https://github.com/z411392/paper-radar/issues/38 | scholarly_catalog | `src/libs/scholarly_catalog/domain/services/classify_revision_change.py`, `src/libs/scholarly_catalog/application/commands/record_paper_revision.py`, `src/libs/scholarly_catalog/tests/` | AC01 |
| https://github.com/z411392/paper-radar/issues/39 | delivery | `src/libs/delivery/domain/services/compute_notification_identity.py`, `src/libs/delivery/application/`, `src/libs/delivery/tests/` | AC02 |
| https://github.com/z411392/paper-radar/issues/40 | runtime-ops | `src/libs/research_workflow/application/commands/process_revision_notice.py`, `src/libs/research_workflow/tests/`, `src/apps/cli/tests/e2e/` | AC03 |

## 接線與交易

Apps 只呼叫 feature inbound port；libs 不讀 apps transport DTO。跨模組交接由 research_workflow 呼叫公開 port，不直接寫另一 owner 的 SQLite 表。共用檔案物件以 ObjectRef 交接，不傳未檢查路徑。所有 outbound I/O 在短交易之外。

本 Story 的 transaction／freshness 以資料模型和對應 Task 的 exact frozen oracle 為準。更動 shared schema 前，由 migration 的唯一 writer 協調，不把整個 migrations 目錄授予多名實作者平行覆寫。

## 正反外部 oracle

- https://github.com/z411392/paper-radar/issues/38：scope-only 推薦設定改變不盲目 re-embed 或重寫正文。 預定 oracle 位於 `src/libs/scholarly_catalog/tests/contract/test_t25_revision_correction_notices.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/39：identity merge 後仍讀舊已寄紀錄；更正事件有不同 business identity。 預定 oracle 位於 `src/libs/delivery/tests/contract/test_t26_revision_correction_notices.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/40：使用正式 workflow 呼叫，不由測試逐個 API 補齊缺失接線。 預定 oracle 位於 `src/apps/cli/tests/acceptance/test_t27_revision_correction_notices.py`；正式 dispatch 前由 Architect 凍結。

## 驗證方法

各 Task body 的每個 S1／S2／S3 都提供 Given／When／Then 與反例。命令是目標接口，尚未執行。測試檔須先建立並證明因缺少目標行為而失敗；不得將不存在的命令寫成 PASS。

Focused unit／integration → contract／acceptance → `make ci-fast` → 獨立 Reviewer exact-candidate review → Commander 保留 Subtask commits 合流。真來源、真模型、受控郵件及復原能力各自另記收據。

## 開工前必填

真實 Task branch、base SHA、candidate SHA、cwd、write owner、frozen paths、provider／live authority 依 Rule15 Fresh Task Pack 補齊。未補齊不宣稱 READY。模型、收件者或後續 HTTP 未定只阻擋直接需要它的操作，不阻擋 hermetic 契約工作。
