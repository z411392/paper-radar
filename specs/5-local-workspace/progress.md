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

Base：`76e822b40d240884cc4c1e58c3ac94c6e22563f8`。候選分支：`codex/6-engineering-foundation`。S1 原始碼提交 `0a985f7df5bef3ff4e593ef5db8737796e6fd790`；S2 防線提交 `6c88ecea5ffc541c41342376a91014526a6b51da`；角色／切片文件提交 `a58028472efbc5f043bdf0ec49c41b23b26592b3`。後續排版／收据提交不改前述實作原意；完整 candidate 以 Task #6／PR 的最新 exact SHA 收據讀回。

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

容器無法解析套件站，沒有假裝本機已安裝完整產品依賴。正式 locked 依賴與跨平台 gate 由真實 GitHub Actions 執行，沒有把 CI 機械執行冒作獨立審閱。後续 exact candidate 的完整 `make ci-fast` 結果以 #6 追加收據為準；未執行的項目不填 exit 0。

本機 RED／GREEN 原始 log 保存於本次工作環境的 repo 外 evidence 目錄；SHA256 分別為 `caff18afc27d316b9e761432c0e46715389ed37b8621e498f975dcee461f5119`（S1 RED）、`df8aef0e3a80238aac28fc80adb4178a5e8759e40c707ba74f5ffdf1cbf80e83`（S2 RED）、`4af59690f181e1ee3c176189de7772498c1533953e107b6079b104a78db94839`（S2 初次 GREEN）、`bb95bea8fde017295f3104a645884d639024a0157980efaa7c80d7d90b57078a`（擴充防線＋治理）。這些 hash 不是原文已永久上傳的宣稱；可重跑的測試在 repo，GitHub CI 保留遠端執行紀錄。

### 完成界線與文件影響

CLI 只有真實 version 命令；workspace、profile、來源、模型、FAISS、email 尚未實作。S3 未完成，獨立 Reviewer 未執行，main 未合流，Task／Story／Exit 不提升完成判定。

需求、Roadmap、Event Storming 語意不變。Context Map 只新增工程接線；Rule15／40／90、CLAUDE、README、plan 與原 Issue 修正當前執行和精確 oracle。測試按 owner 放置，沒有 root 無主測試目錄或第二份工作狀態庫。Project 欄位沒有存取或同步證據，Issue 維護不等於看板已更新。
