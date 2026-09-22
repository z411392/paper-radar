# 補齊羽球與跨出版社來源，分清每個領域的實際覆蓋：技術方案

Story：https://github.com/z411392/paper-radar/issues/29。方案基線，非已凍結的 implementation contract。

## 依賴與 ownership

Context 及程式邊界：[Context Map](../../docs/architecture/context-map.md)。SQL／檔案／index 契約：[data model](../../docs/data-model.md)。

直接上游情境：https://github.com/z411392/paper-radar/issues/9, https://github.com/z411392/paper-radar/issues/13。這是資料／能力相依，不要求無關 Task 完成。

| Task | owner | 預定實作位置 | SC／AC |
|---|---|---|---|
| https://github.com/z411392/paper-radar/issues/30 | discovery | `src/libs/discovery/adapters/driven/pubmed_source_adapter.py`, `src/libs/scholarly_catalog/adapters/driven/pmc_full_text_adapter.py`, `src/libs/discovery/tests/` | AC01 |
| https://github.com/z411392/paper-radar/issues/31 | discovery | `src/libs/discovery/adapters/driven/crossref_source_adapter.py`, `src/libs/scholarly_catalog/domain/services/classify_work_relation.py`, `src/libs/discovery/tests/` | AC02 |
| https://github.com/z411392/paper-radar/issues/32 | watch_profiles | `src/libs/watch_profiles/`, `src/libs/discovery/application/queries/read_harvest_coverage.py`, `src/libs/watch_profiles/tests/` | AC03 |

## 接線與交易

Apps 只呼叫 feature inbound port；libs 不讀 apps transport DTO。跨模組交接由 research_workflow 呼叫公開 port，不直接寫另一 owner 的 SQLite 表。共用檔案物件以 ObjectRef 交接，不傳未檢查路徑。所有 outbound I/O 在短交易之外。

本 Story 的 transaction／freshness 以資料模型和對應 Task 的 exact frozen oracle 為準。更動 shared schema 前，由 migration 的唯一 writer 協調，不把整個 migrations 目錄授予多名實作者平行覆寫。

## 正反外部 oracle

- https://github.com/z411392/paper-radar/issues/30：書目成功而全文 unavailable 只阻擋該篇全文分析，不抹除書目。 預定 oracle 位於 `src/libs/discovery/tests/contract/test_t19_five_domain_coverage.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/31：更正 relation 保留獨立事件，不直接合併 publication content。 預定 oracle 位於 `src/libs/discovery/tests/contract/test_t20_five_domain_coverage.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/32：總體命中率不能掩蓋羽球誤收；未枚舉母體不得宣稱全域 recall。 預定 oracle 位於 `src/libs/watch_profiles/tests/contract/test_t21_five_domain_coverage.py`；正式 dispatch 前由 Architect 凍結。

## 驗證方法

各 Task body 的每個 S1／S2／S3 都提供 Given／When／Then 與反例。命令是目標接口，尚未執行。測試檔須先建立並證明因缺少目標行為而失敗；不得將不存在的命令寫成 PASS。

Focused unit／integration → contract／acceptance → `make ci-fast` → 獨立 Reviewer exact-candidate review → Commander 保留 Subtask commits 合流。真來源、真模型、受控郵件及復原能力各自另記收據。

## 開工前必填

真實 Task branch、base SHA、candidate SHA、cwd、write owner、frozen paths、provider／live authority 依 Rule15 Fresh Task Pack 補齊。未補齊不宣稱 READY。模型、收件者或後續 HTTP 未定只阻擋直接需要它的操作，不阻擋 hermetic 契約工作。
