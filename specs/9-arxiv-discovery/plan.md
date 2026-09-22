# 增量取得 arXiv 論文並在失敗後從正確位置續跑：技術方案

Story：https://github.com/z411392/paper-radar/issues/9。方案基線，非已凍結的 implementation contract。

## 依賴與 ownership

Context 及程式邊界：[Context Map](../../docs/architecture/context-map.md)。SQL／檔案／index 契約：[data model](../../docs/data-model.md)。

直接上游情境：https://github.com/z411392/paper-radar/issues/5。這是資料／能力相依，不要求無關 Task 完成。

| Task | owner | 預定實作位置 | SC／AC |
|---|---|---|---|
| https://github.com/z411392/paper-radar/issues/10 | discovery | `src/libs/discovery/ports/`, `src/libs/discovery/dtos/`, `src/libs/discovery/domain/` | AC01 |
| https://github.com/z411392/paper-radar/issues/11 | discovery | `src/libs/discovery/adapters/driven/arxiv_source_adapter.py`, `src/libs/discovery/tests/` | AC02 |
| https://github.com/z411392/paper-radar/issues/12 | discovery | `src/libs/discovery/application/`, `src/libs/discovery/adapters/driven/sqlite_harvest_store_adapter.py`, `migrations/0004-discovery.sql` | AC03 |

## 接線與交易

Apps 只呼叫 feature inbound port；libs 不讀 apps transport DTO。跨模組交接由 research_workflow 呼叫公開 port，不直接寫另一 owner 的 SQLite 表。共用檔案物件以 ObjectRef 交接，不傳未檢查路徑。所有 outbound I/O 在短交易之外。

本 Story 的 transaction／freshness 以資料模型和對應 Task 的 exact frozen oracle 為準。更動 shared schema 前，由 migration 的唯一 writer 協調，不把整個 migrations 目錄授予多名實作者平行覆寫。

## 正反外部 oracle

- https://github.com/z411392/paper-radar/issues/10：不支援的查詢 filter 明示 unsupported，不接受後忽略。 預定 oracle 位於 `src/libs/discovery/tests/contract/test_t04_arxiv_discovery.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/11：多領域共用 provider budget；無許可的 live 測試不執行、不冒稱來源能力已證。 預定 oracle 位於 `src/libs/discovery/tests/contract/test_t05_arxiv_discovery.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/12：成功空頁、partial 與 failed 分開；只提交已保存的來源時間範圍。 預定 oracle 位於 `src/libs/discovery/tests/contract/test_t06_arxiv_discovery.py`；正式 dispatch 前由 Architect 凍結。

## 驗證方法

各 Task body 的每個 S1／S2／S3 都提供 Given／When／Then 與反例。命令是目標接口，尚未執行。測試檔須先建立並證明因缺少目標行為而失敗；不得將不存在的命令寫成 PASS。

Focused unit／integration → contract／acceptance → `make ci-fast` → 獨立 Reviewer exact-candidate review → Commander 保留 Subtask commits 合流。真來源、真模型、受控郵件及復原能力各自另記收據。

## 開工前必填

真實 Task branch、base SHA、candidate SHA、cwd、write owner、frozen paths、provider／live authority 依 Rule15 Fresh Task Pack 補齊。未補齊不宣稱 READY。模型、收件者或後續 HTTP 未定只阻擋直接需要它的操作，不阻擋 hermetic 契約工作。
