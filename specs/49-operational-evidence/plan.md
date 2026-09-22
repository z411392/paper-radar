# 看見來源健康、解說品質與成本，完成有證據的整合驗收：技術方案

Story：https://github.com/z411392/paper-radar/issues/49。方案基線，非已凍結的 implementation contract。

## 依賴與 ownership

Context 及程式邊界：[Context Map](../../docs/architecture/context-map.md)。SQL／檔案／index 契約：[data model](../../docs/data-model.md)。

直接上游情境：https://github.com/z411392/paper-radar/issues/25, https://github.com/z411392/paper-radar/issues/29, https://github.com/z411392/paper-radar/issues/33, https://github.com/z411392/paper-radar/issues/37, https://github.com/z411392/paper-radar/issues/45。這是資料／能力相依，不要求無關 Task 完成。

| Task | owner | 預定實作位置 | SC／AC |
|---|---|---|---|
| https://github.com/z411392/paper-radar/issues/50 | runtime-ops | `src/libs/research_workflow/application/queries/inspect_health.py`, `src/libs/paper_explanations/adapters/driven/`, `src/apps/cli/adapters/driving/inspect_health.py` | AC01 |
| https://github.com/z411392/paper-radar/issues/51 | runtime-ops | `src/libs/kernel/tests/architecture/`, `src/libs/paper_explanations/tests/`, `src/libs/scholarly_catalog/tests/`, `src/libs/delivery/tests/` | AC02 |
| https://github.com/z411392/paper-radar/issues/52 | runtime-ops | `src/apps/cli/tests/e2e/`, `src/libs/kernel/tests/governance/` | AC03 |

## 接線與交易

Apps 只呼叫 feature inbound port；libs 不讀 apps transport DTO。跨模組交接由 research_workflow 呼叫公開 port，不直接寫另一 owner 的 SQLite 表。共用檔案物件以 ObjectRef 交接，不傳未檢查路徑。所有 outbound I/O 在短交易之外。

本 Story 的 transaction／freshness 以資料模型和對應 Task 的 exact frozen oracle 為準。更動 shared schema 前，由 migration 的唯一 writer 協調，不把整個 migrations 目錄授予多名實作者平行覆寫。

## 正反外部 oracle

- https://github.com/z411392/paper-radar/issues/50：健康 API 不可自己填零值掩蓋未接線或失敗。 預定 oracle 位於 `src/apps/cli/tests/acceptance/test_t34_operational_evidence.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/51：論文內指令不得改收件人／模型路由；測試不得透過給模型更多工具來通過。 預定 oracle 位於 `src/apps/cli/tests/acceptance/test_t35_operational_evidence.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/52：人工樣本與 hermetic PASS 不證全部來源召回；本 Task 不能自我 ACCEPT。 預定 oracle 位於 `src/apps/cli/tests/acceptance/test_t36_operational_evidence.py`；正式 dispatch 前由 Architect 凍結。

## 驗證方法

各 Task body 的每個 S1／S2／S3 都提供 Given／When／Then 與反例。命令是目標接口，尚未執行。測試檔須先建立並證明因缺少目標行為而失敗；不得將不存在的命令寫成 PASS。

Focused unit／integration → contract／acceptance → `make ci-fast` → 獨立 Reviewer exact-candidate review → Commander 保留 Subtask commits 合流。真來源、真模型、受控郵件及復原能力各自另記收據。

## 開工前必填

真實 Task branch、base SHA、candidate SHA、cwd、write owner、frozen paths、provider／live authority 依 Rule15 Fresh Task Pack 補齊。未補齊不宣稱 READY。模型、收件者或後續 HTTP 未定只阻擋直接需要它的操作，不阻擋 hermetic 契約工作。
