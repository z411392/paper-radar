# 初始化本機工作區並發布可增減的關注範圍：技術方案

Story：https://github.com/z411392/paper-radar/issues/5。方案基線，非已凍結的 implementation contract。

## 依賴與 ownership

Context 及程式邊界：[Context Map](../../docs/architecture/context-map.md)。SQL／檔案／index 契約：[data model](../../docs/data-model.md)。

直接上游情境：沒有上游產品 Story；需要本機可用的 Python/uv 與隔離工作目錄。這是資料／能力相依，不要求無關 Task 完成。

| Task | owner | 預定實作位置 | SC／AC |
|---|---|---|---|
| https://github.com/z411392/paper-radar/issues/6 | runtime-ops | `pyproject.toml`, `Makefile`, `src/apps/cli/`, `src/libs/kernel/tests/architecture/` | AC01 的工程前置 |
| https://github.com/z411392/paper-radar/issues/7 | kernel | `src/libs/kernel/`, `migrations/0001-object-registry.sql` | AC03 |
| https://github.com/z411392/paper-radar/issues/8 | watch_profiles | `src/libs/watch_profiles/`, `config/`, `migrations/0002-watch-profiles.sql` | AC02 |

## 接線與交易

Apps 只呼叫 feature inbound port；libs 不讀 apps transport DTO。跨模組交接由 research_workflow 呼叫公開 port，不直接寫另一 owner 的 SQLite 表。共用檔案物件以 ObjectRef 交接，不傳未檢查路徑。所有 outbound I/O 在短交易之外。

本 Story 的 transaction／freshness 以資料模型和對應 Task 的 exact frozen oracle 為準。更動 shared schema 前，由 migration 的唯一 writer 協調，不把整個 migrations 目錄授予多名實作者平行覆寫。

## 正反外部 oracle

- https://github.com/z411392/paper-radar/issues/6：跨 lib 直接 import adapter 必須被防線拒絕；合法 port import 必須通過。原規劃 `src/apps/cli/tests/acceptance/test_t01_local_workspace.py` 涵蓋真實初始化，保留 S3 後續凍結；本次 S1／S2 的作者工程測試依下節。
- https://github.com/z411392/paper-radar/issues/7：檔案已落盤而 SQL 未提交只可留下可回收孤兒；不得出現指向未完成 bytes 的有效業務列。預定 oracle 位於 `src/libs/kernel/tests/contract/test_t02_local_workspace.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/8：相同 fingerprint 冪等；無效項不得半發布；暫停不得清空後續所需歷史。預定 oracle 位於 `src/libs/watch_profiles/tests/contract/test_t03_local_workspace.py`；正式 dispatch 前由 Architect 凍結。

## 驗證方法

各 Task body 的每個 S1／S2／S3 應有對應 Given／When／Then 與反例；不可複製整個 Story 的結果來冒充每個工程切片。Task #6 下節已限定工程 oracle，#7／#8 仍需在其施工前固定。測試檔須先建立並證明因缺少目標行為而失敗；不得將不存在的命令寫成 PASS。

Focused unit／integration → contract／acceptance → `make ci-fast` → 獨立 Reviewer exact-candidate review → Commander 保留 Subtask commits 合流。真來源、真模型、受控郵件及復原能力各自另記收據。

## 開工前必填

真實 Task branch、base SHA、candidate SHA、cwd、write owner、frozen paths、provider／live authority 依 Rule15 Fresh Task Pack 補齊。未補齊不宣稱 READY。模型、收件者或後續 HTTP 未定只阻擋直接需要它的操作，不阻擋 hermetic 契約工作。

## 2026-09-22 Task #6 工程切片契約

此節由使用者授權的本對話整理，是作者技術方案；不是獨立 Architect 的 frozen receipt。保留 spec 的 AC01–AC03，不把工程安裝／防線冒稱成工作區持久化或關注發布。

S1：Hatchling／uv 將 apps、libs 安裝成 namespace packages。`uv run --locked python -m apps.cli version` 透過明確 DI 的 ReadRuntimeVersionPort 讀取套件版本，只輸出 package_name、package_version、python_version；不讀設定、建立 workspace 或連網。隔離 `python -I` 從 repo 外啟動仍成立，未知命令失敗，import 不自動執行 CLI。正反 oracle 在 `src/apps/cli/tests/e2e/test_cli_package.py`；DI integration、use-case unit、port contract 各有 own tests。這只支持 AC01 的入口前置，不證明 AC01 本身。

S2：AST 掃描 source（排除測試）驗證 Rule10／40 的 namespace、薄 app、私有跨 feature 引用、Context Map 允許邊、DAG、內層 I/O 與動態 import/path bypass。違規 fixture 和相近合法 fixture 建在 tmp_path，不修改正式 source；語法錯誤不得被跳過。位置為 `src/libs/kernel/tests/architecture/test_dependency_direction.py`。治理檢查跟 kernel owner，`make ci-fast` 執行所有目前已實作的 guards、lint、typecheck、contract、unit、integration、e2e；不建立空 acceptance target 冒充 Story 通過。

S3：保留原定「真實 CLI 工作區初始化」。先由 #7 提供 InitializeWorkspace inbound 契約、SQLite／檔案持久化與重開證據，再接初始化 handler；關注設定另由 #8 提供。S1 的 version 命令不是 S3 替代品。S1／S2 不因 #7／#8 未完成而延後，但本 Task 在 S3 和獨立驗收之前不得結案。

本次 writer：本對話；branch `codex/6-engineering-foundation`；base `76e822b40d240884cc4c1e58c3ac94c6e22563f8`；本次隔離工作目錄 `/mnt/data/paper-radar-work/checkout`（只識別本次執行，不是後續 agent 的路徑依賴）。可寫範圍是 Python／uv manifest、Makefile、CLI 啟動與 version driving adapter、research_workflow 的 RuntimeVersion DTO／ports／query／adapter、各自測試、kernel 架構／治理測試，以及直接受影響的 Rule15／40／90、CLAUDE、README、Context Map 和本 Story plan／progress。不改 spec、共通需求、Roadmap、migrations 或其他產品 Task 的實作。設計與測試屬作者產物，尚無既存 frozen tests 的寫權。

本次技術候選允許安裝開發依賴並在 GitHub Actions 執行隔離驗證；不做 source／model／mail live。驗證工具與 CI 是機械執行，不是獨立 Reviewer。每次改動保留原 Subtask 與修正原因；未獨立驗收前只發布分支／PR。
