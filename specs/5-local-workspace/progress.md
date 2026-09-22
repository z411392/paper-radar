# 初始化本機工作區並發布可增減的關注範圍：證據與決策

Story：https://github.com/z411392/paper-radar/issues/5

## 2026-09-22 規劃基線

已將使用者的本機儲存與 Kaledoxa 治理要求反映到 [spec](spec.md)、[plan](plan.md) 與 Issues 階層。本輪交付的是規劃，不是產品程式完成。

文件影響：需求見共通 R01–R18；成果順序見 Roadmap；事件語言見 Event Storming；owner／交接見 Context Map；工作分解見本 Story 原生 child Tasks。舊 PostgreSQL／獨立 worker／平行文件庫提案不作現行前提。

本 Story 的產品驗證命令尚未執行，exit code 為 N/A；真來源／真模型／真實寄送／獨立 ACCEPT 收據皆未提供。建庫資料包自己的靜態檢查與 SQL 測試，不構成本 Story 的產品證據。

後續重大決策與測試在此追加帶日期的證據，記 exact SHA、命令、結果與範圍；不在本檔維護 Status／Priority／Sprint，也不因 Issue 已建立而宣稱 Story accepted。

## 2026-09-22 Task #6 S1／S2 工程候選

使用者[暫行直接實作裁決](https://github.com/z411392/paper-radar/issues/1#issuecomment-5774317170) 取代這個批次先前的規劃限定，Rule15 保存唯一例外。本次未啟動或借用 Codex／Gemini 具名 agent；作者是本對話，沒有獨立 ACCEPT。

原 #6 三個 Subtask 都複製 Story 的持久化結果，無法各自判定。本次在 plan／Issue 區分：S1 是真實安裝與 CLI 接線；S2 是架構、治理與驗證防線；S3 保留實際 workspace 初始化，必須取得 #7 的直接契約與儲存實作，不能由 version 輸出抵扣。Story AC01–AC03 本身未修改。

Base：`76e822b40d240884cc4c1e58c3ac94c6e22563f8`。候選分支：`codex/6-engineering-foundation`。S1 原始碼提交 `0a985f7df5bef3ff4e593ef5db8737796e6fd790`；S2 防線提交 `6c88ecea5ffc541c41342376a91014526a6b51da`；角色／切片文件提交 `a58028472efbc5f043bdf0ec49c41b23b26592b3`。後續排版／收據提交不改前述實作原意；完整 candidate 以 Task #6／PR 的最新 exact SHA 收據讀回。

### 實際驗證及失敗記錄

| 範圍 | 實際命令／執行 | 結果與邊界 |
|---|---|---|
| S1 實作前 RED | 容器透過 `uv run --no-project --python /opt/pyvenv/bin/python python -m pytest src/apps/cli/tests/e2e/test_cli_package.py -q` | 3 failed、1 passed；缺 apps 套件使正例失敗，unknown-command 反例已失敗退出。這是隔離前置驗證，不是產品 locked 環境 GREEN。 |
| S2 防線 RED | 同樣透過 uv 執行 architecture tests，scanner 尚未實作 | 27 failed、8 passed；違規漏報使正例失敗，不靠修改期待結果換綠燈。 |
| S2 初次 GREEN | 同一 architecture 測試檔 | 35 passed；後續增加 kernel adapter、catalog→discovery 限制、內層 open 與 app 層目錄案例，另含治理測試的本機執行為 45 passed。 |
| uv 鎖檔產生 | [run 35712246677](https://github.com/z411392/paper-radar/actions/runs/35712246677)，`uv lock --python 3.13.5` | exit 0；lock blob `0c3a44b09a819a826a24c0cbb3f697c7315b5b08`，不是手工拼寫。 |
| S1 跨平台 | [run 35712673332](https://github.com/z411392/paper-radar/actions/runs/35712673332)，exact `0a985f7` | Ubuntu Python 3.12／3.13.5、macOS Python 3.13.5 的 `uv sync --locked`、env-check、tests、typecheck、lint 與 tracked-file readback 通過。不是使用者本機已測。 |
| S2 首次 CI | [run 35713399986](https://github.com/z411392/paper-radar/actions/runs/35713399986)，exact `6c88ecea` | lint 的 ruff check 通過，但 format check 在一處布林式換行失敗；後面的完整 gate 未跑，不能宣稱該 candidate 全綠。 |
| 排版修正 | [run 35713764626](https://github.com/z411392/paper-radar/actions/runs/35713764626) | ruff 只重排該工程測試檔，AST before／after 相同；另修 plan 的繁體字。只產生兩個 blob，不自行 commit／移動 branch。其暫用 workflow 在接收產物後移除。 |

容器無法解析套件站，沒有假裝本機已安裝完整產品依賴。正式 locked 依賴與跨平台 gate 由真實 GitHub Actions 執行，沒有把 CI 機械執行冒作獨立審閱。後續 exact candidate 的完整 `make ci-fast` 結果以 #6 追加收據為準；未執行的項目不填 exit 0。

本機 RED／GREEN 原始 log 保存於本次工作環境的 repo 外 evidence 目錄；SHA256 分別為 `caff18afc27d316b9e761432c0e46715389ed37b8621e498f975dcee461f5119`（S1 RED）、`df8aef0e3a80238aac28fc80adb4178a5e8759e40c707ba74f5ffdf1cbf80e83`（S2 RED）、`4af59690f181e1ee3c176189de7772498c1533953e107b6079b104a78db94839`（S2 初次 GREEN）、`bb95bea8fde017295f3104a645884d639024a0157980efaa7c80d7d90b57078a`（擴充防線＋治理）。這些 hash 不是原文已永久上傳的宣稱；可重跑的測試在 repo，GitHub CI 保留遠端執行紀錄。

### 完成界線與文件影響

CLI 只有真實 version 命令；workspace、profile、來源、模型、FAISS、email 尚未實作。S3 未完成，獨立 Reviewer 未執行，main 未合流，Task／Story／Exit 不提升完成判定。

需求、Roadmap、Event Storming 語意不變。Context Map 只新增工程接線；Rule15／40／90、CLAUDE、README、plan 與原 Issue 修正當前執行和精確 oracle。測試按 owner 放置，沒有 root 無主測試目錄或第二份工作狀態庫。Project 欄位沒有存取或同步證據，Issue 維護不等於看板已更新。

### 2026-09-22 S1／S2 完整候選驗證

[PR #53](https://github.com/z411392/paper-radar/pull/53) 保存本批候選。實作與測試修正後的 exact SHA：`aa36f9a570c37b032751ca77b6bc7ed61f57f3fa`。

先前 [run 35714028532](https://github.com/z411392/paper-radar/actions/runs/35714028532) 在 `5ffc694882e0ad0b7caf8797ebed444f52156d2c` 通過 lint、format、typecheck 與 55 項測試，但 wheel 隔離安裝測試失敗。原因是測試假設已安裝套件的離線快取必定包含新的 registry 解析資料；這個假設不成立。失敗不是被忽略或標成 PASS。

S1 修正先以 `uv sync --locked --offline --no-dev --no-install-project` 將 lock 指定的 runtime dependencies 放進獨立環境，再安裝實際建置的 wheel、執行 `uv pip check`。安裝 wheel 時 `--no-deps` 不免除依賴驗證；相依套件已由 lock 準備並在安裝後檢查。保留 repo 外 `python -I` 啟動，新增 import 路徑必須位於該獨立環境的斷言，防止 editable source 意外混入。沒有開啟測試網路或降低既定輸出判準。

[push run 35714287554](https://github.com/z411392/paper-radar/actions/runs/35714287554) 實際 checkout `aa36f9a570c37b032751ca77b6bc7ed61f57f3fa`。Ubuntu Python 3.12、Ubuntu Python 3.13.5、macOS Python 3.13.5 三個 jobs 的依賴安裝、`make ci-fast` 與 `git diff --exit-code` 全部成功。Linux Python 3.13.5 job `106701977097` 的完整 log 顯示 `56 passed`、pyright `0 errors, 0 warnings`、ruff check 與 format 通過；命令 exit 0。另 [PR run 35714292053](https://github.com/z411392/paper-radar/actions/runs/35714292053) 完成 success。

這是 2026-09-22 的工程候選證據，不是永久測試數量契約、使用者本人 Mac 實測或獨立 Reviewer ACCEPT。S1／S2 已有程式與自測候選；S3、#7／#8 的產品行為及 Task／Story 結案仍未完成。本次文件收據提交不修改 source；若 reviewer 或後續工作改動 candidate，必須重新核對 exact SHA 與適用測試。


## 2026-09-23 Task #7 SQLite／檔案候選

延續 #7 既有開工紀錄與使用者「繼續」要求。本批實作 workspace identity、短 SQLite transaction、explicit migration bundle、hash 內容發布、登錄去重、跨程序讀取與唯讀完整性診斷。未合流 main，沒有獨立 Reviewer、Task Done 或 Project 欄位操作收據。

直接基線為 #53 的未驗收候選 `69249a59a7d0dca49b84ace6a6ed8fa37c06a05e`。本次隔離施工目錄 `/mnt/data/paper-radar-next/checkout` 僅記錄當次執行，不是使用者機器的路徑依賴。

本機透過 uv 的 subsystem RED 首次因未實作 adapter 無法 import；實作後修正測試自己的 repo-root 定位錯誤，再有 27 passed。補強的反例得到 4 failed／42 passed（DB 遺失後誤採用舊內容、陌生 state 檔、未診斷暫存檔與 metadata 控制字元）；修正 production 後 46 passed。另用固定時序重現初始化者在掃描期間完成 DB 發布，使另一個初始化者誤報 foreign_workspace，該反例先失敗；修正重新核對後 subsystem 為 47 passed。這些是作者測試，不是獨立 oracle。

S1 初始提交 `d654e9ff30e10300b6bf74ecc76ce496d59e4227`；S2 提交 `2aefa49ae892eb228e185619cde7669faf074562`；S3 提交 `b947c1b66a32fcc69ae6fbec1c50a1dc0ea69e9e`；初始化競爭修正 `ad622cdf01f9bee890983e32e6988ed85fa120dc`。後續格式／文件提交保留這些歷史，不 squash。

本機套件站 DNS 不可用，subsystem 使用 `uv run --no-project --python /opt/pyvenv/bin/python python -m pytest` 在隔離 src 下執行；不稱為 locked 全庫 gate。完整鎖定環境、ruff／pyright／全庫測試由 GitHub runner 執行，正式結果以本卡與 PR 的 exact candidate CI readback 為準。原始 log 雜湊由 #7 append-only 收據保存，不以 hash 冒稱原始檔永久可下載。

需求、Roadmap、Event Storming、Story AC、原 migrations 與架構／治理測試未變；技術補充放本 Story plan 和 data-model。CLI 初始化、profile、FAISS、真來源、模型與郵件未執行。所有階段只交付已測候選，沒有自我 ACCEPT。

## 2026-09-23 CLI 初始化與儲存競爭修復

此次 fresh-read 發現前次中斷已留下 [PR #54](https://github.com/z411392/paper-radar/pull/54) 和 #7 的 `8499f43a807d27fdc94abf086552e826eeeac456`。接續而非重建。新的 [PR #55](https://github.com/z411392/paper-radar/pull/55) 疊在 #54 之上，分支 `codex/6-workspace-initialization`；本對話直接透過 GitHub 提交，完整驗證在 runner 執行，沒有宣稱本次在使用者 Mac 或容器跑過完整 locked 測試。

### RED、缺陷與實際修正

- `893b1e7f859955b44b229952b9dfa9eeb8aabeb9` 新增 init／resource 測試；[run 35756593266](https://github.com/z411392/paper-radar/actions/runs/35756593266) 只到格式失敗，不是行為 RED。`ec2527bdb703947e548dac10cdc6dee5970587a8` 僅修兩處排版。
- `ec2527b` 的 [run 35756815359](https://github.com/z411392/paper-radar/actions/runs/35756815359)，Ubuntu 3.13.5 為 13 failed／104 passed。其中 11 項是 init／resource 未實作的預期 RED；另 2 項是原 #7 並行測試暴露 sidecar 消失和 WAL 切換爭鎖，不能算入預期 RED 或靠重跑忽略。
- 原 #7 owner 新增固定時序測試，`d455543e171407795727fcd5df9d0a2415cbb64d` 的 [run 35757336843](https://github.com/z411392/paper-radar/actions/runs/35757336843) 重現 2 failed／105 passed。`6a030bf8e5c2adfa8183cc36ff7a5a91c0315ff7` 只改兩個 adapter；[run 35757666306](https://github.com/z411392/paper-radar/actions/runs/35757666306) 已讀回 success。#6 用 `bd1b935fd58ebac99eead2bad89e95c8600f8f1a` 合併接收，沒有改寫上游歷史。
- CLI／resource 初始實作 `8e154d63be237b065cd7aab01ca76d8d4197dd7b`；整合候選的 [run 35757851165](https://github.com/z411392/paper-radar/actions/runs/35757851165) 為 6 failed／115 passed。失敗在 Injector 對非 runtime-checkable Protocol 的 instance 分類，不是資源打包失敗。修正 `bdc36196b39bc1ff7752ab23f6f85c13368d85ed` 使用明確 InstanceProvider，未修改 kernel ports；增加 DI 本身無副作用的測試。

### 完整 GREEN

對 head `bdc36196b39bc1ff7752ab23f6f85c13368d85ed` 的 [PR run 35758081473](https://github.com/z411392/paper-radar/actions/runs/35758081473)，Ubuntu 3.12／Ubuntu 3.13.5／macOS 3.13.5 三個 jobs 均已讀回 completed／success。PR runner 實際 checkout 的合併候選是 `37c107e7571bb21311052ad2093be090f6841032`，不是聲稱直接 checkout head。

Ubuntu 3.13.5 job `106848821719` 完整 log：`uv sync --locked`、`make ci-fast`、`git diff --exit-code` 均成功；122 passed，ruff check／format 通過，pyright 0 errors／0 warnings。全部既有測試仍執行，沒有 skip 或放寬期待。這是日期限定的候選收據，不是永久測試數契約或独立驗收。

### 本批交付與保留邊界

工作區現在可以透過真正 CLI 初始化、新程序重開，已驗證相同身分、外部副作用關閉、中文／空白／相對路徑、錯誤參數不先寫入、陌生資料保持、symlink／遺失 DB 拒絕。非 editable wheel 在 repo 外也可初始化與重開；sdist 保留 canonical SQL，缺資源不從 cwd 補齊。

S1／S2／S3 已有各自作者實作與驗證，但 independent Reviewer 未執行，main 不合流，#6／#7／Story #5 不結案。#8 profile、來源、模型、FAISS、郵件仍未實作。本批只觸及 runner 隔離目錄，不改使用者既有資料。Issue／PR 更新不表示 Project Status／Priority／Sprint 已同步。

五類影響：需求、Roadmap、Event Storming、Story spec 不變；Context Map 補 init 與 package resource 的 owner 接線；本 plan／progress 及 #6／#7 記固定契約、缺陷與證據。README 更新可執行命令，SQL 原文、既有 architecture／governance 防線與角色例外不變。


## 2026-09-23 Task #8 作者候選：版本化關注設定

本次依使用者追加要求，把探索、推論及結果記在 Issue comments。#7 的 sidecar／WAL 研究與 fast-forward 衝突已記在該卡；遠端已有相同修復，未 force push。#8 從 `6a030bf8e5c2adfa8183cc36ff7a5a91c0315ff7` 另開分支，不覆蓋 #6／#7 的進行中工作。

新增設定語法及五領域種子、種子只新增的匯入、profile 原子發布、expected_revision 衝突檢查、舊請求冪等且不回退 current、lifecycle 與歷史查詢。原 SQL／Story spec／共通需求／架構及治理判準不變。

本機 subsystem 前置 RED 是缺少 watch_profiles adapters 而 collection failure；實作後原 19 項、擴充後 28 項測試通過。使用 `uv run --no-project --python /opt/pyvenv/bin/python python -m pytest ... --import-mode=importlib -q`，從隔離 source 目錄執行；不是完整產品 locked 環境，也不是獨立 Reviewer。新增實際 kernel factory 整合案例留完整 CI 驗證。正式 exact SHA／完整 gate／跨平台結果以 Issue #8 與 PR 的 readback receipt 為準，不預填成功。

目前只交付作者候選；不得推導獨立 ACCEPT、main 合流、Task Done、CLI 設定介面完成或 Project 已同步。

輸入邊界額外探索：三個 Unicode／revision 上限反例先 RED，修正後本機子系統 31 passed；真正 kernel factory 整合另留完整 CI，不計入本機已測。來源與推論已記於 Issue #8 comments。


## 2026-09-23 Task #8 S4：CLI 整合、探索與驗證

研究／探索與失敗持續追加於 [Issue #8](https://github.com/z411392/paper-radar/issues/8)，包括 comments 5781290867（限定契約）、5781468364（設定檔有界讀取）、5781495408（文件合併）、5781519488（pytest大型案例名稱）、5781577257（開啟DB的錯誤邊界）。不是只留在聊天，也不建立第二份看板。

本機9項設定檔unit測試透過uv既存interpreter執行通過，僅是有限子系統證據；完整locked gate由GitHub runner執行。固定技術依賴 #55 `85a7d270`、#56 `a01b87e`，不包含另一條 profile-boundary-hardening 草稿。

run35764763421 在文件合併檢查停止，未跑產品測試。#55 正確改寫過期規劃段落，#56 為新增段落；修正整合假設，保留完整 #55＋核對共同基線後的 #56 追加內容，不選邊丟棄歷史。

run35764897506 的實作後完整 gate 為193 passed／1 setup error。超大 bytes 參數被 pytest 自動放入 node ID與PYTEST_CURRENT_TEST，造成子程序啟動前Errno7；這是測試fixture問題，不是產品已通過。修正只提供四個短ids，超大輸入、上限與斷言均保留。當次RED中的該錯誤不算缺少功能的RED。

[run35765102449](https://github.com/z411392/paper-radar/actions/runs/35765102449) 重跑：CLI在合併後未實作來源為5 failed／8 passed／11 setup errors，皆因新接口不存在；實作後194 passed、ruff check／format通過、pyright零錯誤／警告。真正非editable wheel在repo外初始化第2版、匯入、發布及讀回。其他新測試不冒稱各自已有獨立RED／freeze。

合併commit `1de9c7da512cc4dc844b6b03f52254bf50ec9acb`；測試commit `9e3a0e0dcc0993fc7bcc9ebb0fc18c151a6cb40f`；程式候選 `44a9d684a5377be276241c3238726612a82f9a26`；tree `608121c36a41321e0d106c4ef84170c0444bf0ea`。該tree比合併依賴多40項測試；122和139共享測試不可直接相加。機械runner只輸出git objects，本對話才非force更新專属分支，沒有移動上游或main。

其後source readback發現schema wrapper在try外開啟DB，可能漏出sqlite3.Error。作者反例commit `c67fea5546c5edbf0be529c1d1102a89ca64f02a` 新增2項損毀DB案例。此次收尾流程要求兩項先確實失敗，再將開啟sqlite3.Error轉成既有schema_verification_failed；不回顯原始錯誤、不覆蓋合成損毀檔。完整收尾run與最終head由Issue／PR追加讀回，不在尚未執行時填PASS。

實際測試SQLite為3.49.1，sqlite_source_id為 `2025-02-18 13:38:58 873d4e274b4988d260ba8354a9718324a1c26187a4ab4c1cc0227c03d0f10e70`。這增加runtime識別證據，並未證明WAL-reset已修補；#50風險仍保留。所有來源、模型、FAISS、真郵件與正式資料操作未執行。

候選 [PR #57](https://github.com/z411392/paper-radar/pull/57) 尚待獨立Reviewer；作者與CI不是ACCEPT，不關閉Task／Story、不合流main。完整命令在README，技術接線在Context Map，本檔只留證據，不存Status／Priority／Sprint。原SQL、lock、Story AC與既有工程防線不變。
