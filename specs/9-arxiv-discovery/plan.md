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


## 2026-09-23 Task #10：來源查詢與分頁契約

本批依暫行直接實作授權，使用 PR #57 的 `2ffd067a79ec8dcb3a87b424eecacdc694c6dc14` 作技術基線，不當成已驗收 main。新分支 codex/10-arxiv-query-contract；S1–S3 分別保留 commits，沒有獨立 Architect freeze 或 Reviewer ACCEPT。

輸入為 discovery 自己的 SourceQueryInput／DomainQuerySnapshot，含 profile identity、revision、fingerprint、精確 domain revision、provider-specific categories、詞組及時間窗。workflow 後續負責取得已發布且仍有效的設定並映射；discovery 不 import watch_profiles、不讀它的私有表。輸入 hash 不是簽章，編譯也不證明設定此刻仍 active/current。

CompileSourceQuery inbound 用例只呼叫 SourceQueryCompilerPort，arXiv 語法由 ArxivQueryCompilerAdapter 擁有。純編譯不執行 HTTP、SQLite、clock 或模型。分類 OR 領域 aliases/include 的 title/abstract 詞組，再 AND 個人 include、ANDNOT 排除詞，是本案明示的召回政策，不是 arXiv 或研究報告指定的普遍語意。分類只核對語法；來源是否仍接受需 #11 真實能力核對。

官方 User Manual §§3.1.1／3.3／5.1： https://info.arxiv.org/help/api/user-manual.html 。本次只核對到 submittedDate 的原生日期篩選，GMT、分鐘精度；lastUpdatedDate 是排序能力，不冒稱同樣支援完整修訂時間窗。只接受明確時區與分鐘邊界，轉為 inclusive-minutes-utc。新投稿窗口不能取代舊文章更新的 reconciliation。

language、free_only、allow_preprints=false、自然語言 scope 都不是此 compiler 的原生篩選。預設 reject；只有呼叫端明示 deferred_mode=defer 才產生候選查詢，並將原始條件和值、unsupported_at_source、必須處理的下游階段放入 plan。下游尚未驗證時不能當作符合條件。引號／反斜線片語沒有已查證的 escape 時直接拒絕，不刪字改義。

完整 provenance 保存 compiler/capability/policy 版本、設定 snapshot、查詢、UTC 時間窗、每頁大小與後置條件。query_fingerprint 識別整份計画；request_fingerprint 另綁 GET URL／offset／page size，不把每一頁當成新關注內容。預設200筆、單頁上限2000、窗口上限30000及16000-byte查詢上限是本版明示工程限制，不是來源 SLA。

EvaluateSourcePage 只回下一頁提案，不寫 checkpoint。失敗頁、query/offset不符、頁內重複identity、異常空頁、未到結尾的短頁、已知total改變與超窗總量皆明確失敗。只有成功且有效的首次total=0空頁可標verified_empty。arXiv offset pagination沒有本次已核對的snapshot／同時間次排序保證；total不變也不能證明完整。fixtures是合成parsed-page DTO，不是真實論文或HTTP/Atom解析證據。

真正HTTP／Atom parser與全provider共用至少3秒及單一連線節流屬 #11；durable raw保存、binding/version checkpoint與bounded reconciliation屬 #12。官方節流來源：https://info.arxiv.org/help/api/tou.html 。本批只記能力不執行收集。新測試位置 src/libs/discovery/tests/contract/test_t04_arxiv_discovery.py；既有 Story AC、source、SQL、lock、架構／治理測試不修改。
