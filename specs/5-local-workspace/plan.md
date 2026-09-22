# 初始化本機工作區並發布可增減的關注範圍：技術方案

Story：https://github.com/z411392/paper-radar/issues/5。方案基線，非已凍結的 implementation contract。

## 依賴與 ownership

Context 及程式邊界：[Context Map](../../docs/architecture/context-map.md)。SQL／檔案／index 契約：[data model](../../docs/data-model.md)。

直接上游情境：沒有上游產品 Story；需要本機可用的 Python/uv 與隔離工作目錄。。這是資料／能力相依，不要求無關 Task 完成。

| Task | owner | 預定實作位置 | SC／AC |
|---|---|---|---|
| https://github.com/z411392/paper-radar/issues/6 | runtime-ops | `pyproject.toml`, `Makefile`, `src/apps/cli/`, `src/libs/kernel/tests/architecture/` | AC01 |
| https://github.com/z411392/paper-radar/issues/7 | kernel | `src/libs/kernel/`, `migrations/0001-object-registry.sql` | AC03 |
| https://github.com/z411392/paper-radar/issues/8 | watch_profiles | `src/libs/watch_profiles/`, `config/`, `migrations/0002-watch-profiles.sql` | AC02 |

## 接線與交易

Apps 只呼叫 feature inbound port；libs 不讀 apps transport DTO。跨模組交接由 research_workflow 呼叫公開 port，不直接寫另一 owner 的 SQLite 表。共用檔案物件以 ObjectRef 交接，不傳未檢查路徑。所有 outbound I/O 在短交易之外。

本 Story 的 transaction／freshness 以資料模型和對應 Task 的 exact frozen oracle 為準。更動 shared schema 前，由 migration 的唯一 writer 協調，不把整個 migrations 目錄授予多名實作者平行覆寫。

## 正反外部 oracle

- https://github.com/z411392/paper-radar/issues/6：跨 lib 直接 import adapter 必須被防線拒絕；合法 port import 必須通過。 預定 oracle 位於 `src/apps/cli/tests/acceptance/test_t01_local_workspace.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/7：檔案已落盤而 SQL 未提交只可留下可回收孤兒；不得出現指向未完成 bytes 的有效業務列。 預定 oracle 位於 `src/libs/kernel/tests/contract/test_t02_local_workspace.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/8：相同 fingerprint 冪等；無效項不得半發布；暫停不得清空後續所需歷史。 預定 oracle 位於 `src/libs/watch_profiles/tests/contract/test_t03_local_workspace.py`；正式 dispatch 前由 Architect 凍結。

## 驗證方法

各 Task body 的每個 S1／S2／S3 都提供 Given／When／Then 與反例。命令是目標接口，尚未執行。測試檔須先建立並證明因缺少目標行為而失敗；不得將不存在的命令寫成 PASS。

Focused unit／integration → contract／acceptance → `make ci-fast` → 獨立 Reviewer exact-candidate review → Commander 保留 Subtask commits 合流。真來源、真模型、受控郵件及復原能力各自另記收據。

## 開工前必填

真實 Task branch、base SHA、candidate SHA、cwd、write owner、frozen paths、provider／live authority 依 Rule15 Fresh Task Pack 補齊。未補齊不宣稱 READY。模型、收件者或後續 HTTP 未定只阻擋直接需要它的操作，不阻擋 hermetic 契約工作。
