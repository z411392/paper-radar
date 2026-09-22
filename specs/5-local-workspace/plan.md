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

- https://github.com/z411392/paper-radar/issues/6：跨 lib 直接 import adapter 必須被防線拒絕；合法 port import 必須通過。原規劃 `src/apps/cli/tests/acceptance/test_t01_local_workspace.py` 涵蓋真實初始化；2026-09-23 已建立作者測試，並非獨立 frozen receipt，依本頁 S3 契約。
- https://github.com/z411392/paper-radar/issues/7：檔案已落盤而 SQL 未提交只可留下可回收孤兒；不得出現指向未完成 bytes 的有效業務列。作者實測位置見下方 #7 切片；獨立凍結與 ACCEPT 尚未取得。
- https://github.com/z411392/paper-radar/issues/8：相同 fingerprint 冪等；無效項不得半發布；暫停不得清空後續所需歷史。預定 oracle 位於 `src/libs/watch_profiles/tests/contract/test_t03_local_workspace.py`；正式 dispatch 前由 Architect 凍結。

## 驗證方法

各 Task body 的每個 S1／S2／S3 應有對應 Given／When／Then 與反例；不可複製整個 Story 的結果來冒充每個工程切片。Task #6／#7 已限定作者測試，#8 仍需在其施工前固定。測試檔須先建立並證明因缺少目標行為而失敗；不得將不存在的命令寫成 PASS。

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


## 2026-09-23 Task #7 本機儲存切片

延續使用者的暫行直接實作委派；作者自行建立正反測試，沒有獨立 Architect freeze 或 Reviewer ACCEPT。#7 基於 #53 的未驗收候選 `69249a59a7d0dca49b84ace6a6ed8fa37c06a05e`，分支 `codex/7-durable-local-storage`，不移動 #53 或 main。

S1：InitializeWorkspace inbound port → InitializeWorkspace command → WorkspaceBootstrapPort → SqliteWorkspaceBootstrapAdapter。組裝時明示 Migration 序列，本批只驗證 0001，沒有掃描並自動套用全部 migration。新 DB 在同磁碟暫存檔完成 schema 與身份後，以不覆蓋既有目標的發布方式成為正式 DB；競爭初始化者讀回勝出身份，掃描期間出現的 DB 也必須驗證 application_id 與 schema。一般連線不自動建立遺失 DB。WAL、FULL、foreign_keys、busy_timeout 由連線 factory 明確設定。Migration runner 擁有短交易，逐 statement 執行、檢查已套用檔案雜湊；script 不得自行 COMMIT／ATTACH／PRAGMA。

S2：PublishObject 先呼叫 ObjectBytesPort 寫入及校驗完整 bytes，再由 ObjectUnitOfWorkPort 的短交易登錄 ObjectRegistryPort。SQL 連線不穿出 adapter。相同 kind/hash 使用穩定 ObjectRef；重複回傳原紀錄，metadata 衝突失敗。ReadObject 只按 registry identity 取引用，離開 SQL transaction 後才讀檔及驗證 hash／長度。

S3：InspectStorage 讀取 registry 快照，再檢查內容；回報 missing、corrupt、unsafe_path、unregistered、temporary 等診斷，不修改 registry、不修復、不刪除。真程序以 os._exit 中斷在檔案發布後，後續重試接回既有完整檔案，跨程序讀回相同 bytes。診斷屬觀測結果，不是跨 DB／FS 原子快照，也不是刪除許可。

測試位置：`src/libs/kernel/tests/integration/test_sqlite_workspace.py`、`test_local_storage.py`、`test_bootstrap_race.py` 及 `src/libs/kernel/tests/unit/test_object_ref.py`；包含並行初始化／重複發布、唯讀 transaction、rollback、失敗 migration、陌生 DB、遺失 DB 但保留內容、symlink、fsync 失敗與錯誤 metadata。正式完整驗證仍是 `make ci-fast`。

本批可寫 kernel 新增檔案與本 plan／progress／data-model 的必要技術補充；原 migrations、共通需求、Story spec、Roadmap、Event Storming、架構／治理防線和 #53 程式不變。#6 S3 CLI handler、#8 profile、FAISS／來源／模型／郵件不在此切片，不以 library 測試冒稱那些能力完成。

### 接續驗證的儲存競爭修正

2026-09-23 由原 #7 owner 在自己的分支修復：新 DB 完成 schema／identity 後，先在尚未公開的 staging 檔設定 WAL 並關閉連線，再 fsync 及不覆蓋發布；一般 factory 仍負責連線設定，但新初始化者不再爭用 journal mode 升級。可消失的 SQLite sidecar 以單次 lstat 取得型別快照；不存在是正常狀態，symlink／非普通檔案仍拒絕。固定重現測試在 `test_storage_race_regressions.py`，不降低既有安全或並行測試。

## 2026-09-23 Task #6 S3 初始化接線

[PR #55](https://github.com/z411392/paper-radar/pull/55)，分支 `codex/6-workspace-initialization`。初始 base 為 #7 `8499f43`，後以合併提交接收 #7 `6a030bf` 的三個檔案修正。兩條 Task commit 歷史保留；沒有把未驗收上游視為 main 基線。

命令：`uv run --locked python -m apps.cli init --workspace PATH`。全部參數先解析才組裝；缺參數、多餘參數、未知選項退出 2 且不建立工作區。空白／NUL 路徑退出 2。預期 StorageError／OSError 以 stderr JSON error 回報並退出 1；成功才輸出 WorkspaceInfo JSON。version 的既有行為不變。

接線：CLI → InitializeWorkspacePort → InitializeWorkspace → WorkspaceBootstrapPort → SqliteWorkspaceBootstrapAdapter。組裝已存在物件時用 Injector InstanceProvider，不要求 kernel Protocol 為 DI 框架新增 runtime_checkable，也不將 callable command 誤當 provider 先執行。建立 Injector 及取得 port 都不初始化。

只有 canonical `migrations/0001-object-registry.sql` 進入這個命令。wheel 的 force-include 映射到 `libs/kernel/resources/migrations/`，sdist 保存 root 原檔。loader 使用 package resource；缺失／非 UTF-8 是 migration_resource_error，不搜尋 cwd 備援。uv cache-keys 包含該 SQL。後續 migration 有自己的 Task，不掃描整個資料夾自動套用。

可觀察正例：新程序重開相同身分、相對路徑依呼叫者 cwd、中文及空白路徑、wheel 在 repo 外初始化與重開、已套用 0001／未套用 0002、外部副作用預設關閉。反例：陌生目錄／DB 保持原內容、symlink 不觸及目標、DB 遺失但內容存在不得重新初始化、CLI 參數拒絕在任何寫入之前。

作者 oracle：`src/apps/cli/tests/acceptance/test_t01_local_workspace.py`、`src/apps/cli/tests/integration/test_workspace_composition.py`、`test_wheel_install.py`，以及 kernel `tests/integration/test_workspace_resources.py`。完整命令 `make ci-fast`；wheel 專項 `make package-check`。不修改 Story spec、原 SQL 或既有架構／治理防線。AC01 的 profile 部分仍由 #8 承接，不以 workspace 初始化宣稱整個 Story 完成。


## 2026-09-23 Task #8 關注設定實作契約

作者技術方案，依 Issue #8 本批 checkpoint；不是獨立 Architect 的 frozen receipt。#6／#7 另有進行中的候選，這批只在 `codex/8-watch-profile-revisions` 修改 watch_profiles，不覆蓋其分支。固定依賴 base 為 `6a030bf8e5c2adfa8183cc36ff7a5a91c0315ff7`，未獨立驗收。

ImportDomainSeeds、PublishWatchProfile、ReadWatchProfile、SetWatchProfileLifecycle 由公開 callable ports 表達；application 不取得 SQLite connection。driven store 接收 composition 提供的連線 factory，僅操作自己四張表。SQL 仍為原始 0002，這批沒有修改 migration、啟用額外來源或更改 CLI init 預設。

匯入格式選 JSON，拒絕重複 keys、未知欄位、NaN／Infinity、錯誤型別、未知 source ID、重複 domain。source ID 是設定登錄檢查，不是 live capability；五領域種子及類別是查詢設定起點，後續來源 compiler 仍須驗證實際能力。集合欄位按內容排序後計算含 grammar 版本的 fingerprint，scope 原文除頭尾空白外不做語義改寫。

種子只新增不存在的 domain；已存在而內容不同回 preserved，不新增修訂或覆蓋 SQLite。profile 的 domain 引用須指向精確既存 revision；新內容帶 expected_revision，衝突明確失敗。重試命中任何舊 fingerprint 時只回傳該歷史版本，不倒退 current pointer。id／reader_id／name 在這個切片中視為穩定身份，改名及明示還原歷史設定不是本批入口。

暫停／重啟只改目前 lifecycle。發布內容不自動重啟 paused profile。歷史修訂查詢回內容的 revision 與目前 current_revision／lifecycle，後兩者不是假造的歷史狀態。移除領域以新 profile revision 省略對應 reference，舊 reference 仍保留。測試對實際已讀／通知資料表放入合成紀錄，驗證操作前後完全相同；未寄出郵件。

驗證位置：`src/libs/watch_profiles/tests/contract/test_t03_local_workspace.py`。本批尚未新增 CLI 設定管理、來源查詢、模型、推送或其他 Story 功能。來源研究、失敗原因、選擇與修正均追加原 Issue comments；Status／Priority／Sprint 不複製到 Markdown。
