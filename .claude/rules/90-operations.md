# 操作與 Git 交付

live source／provider／模型下載／寄信／reset／資料刪除／外部寫入需 Task 明示授權。先解析精確目標，保留 dirty work；不得自行 stage、commit、restore 未授權檔案。失敗 attempt 是證據，不靠刪除它變成綠燈。

本機正式入口與 tests 使用 `uv run --locked`。完整產品 gate 為 make ci-fast；規劃資料包的文件／schema 檢查不是該 gate。Secrets 不進 repo 或 log；cleanup 只對明列可重建產物，不包含 SQLite／raw／evidence／原始向量的唯一副本。

## Python 與 uv

使用者明示 Python 統一使用 uv。Interpreter 選擇由 `.python-version` 管理，相容範圍由 `pyproject.toml` 定義，依賴解析由工具產生的 `uv.lock` 鎖定。日常安裝使用 `uv sync --locked`；執行與測試使用 `uv run --locked ...`。新增／移除依賴使用 `uv add`／`uv remove`；刻意更新 lock 後同批提交 pyproject 與 lock，不用系統 pip 或另一個環境管理器維護第二套依賴。

Task #6 開始正式套件化：`package = true`，Hatchling 安裝 `src/apps`／`src/libs` namespace packages，不使用 PYTHONPATH 或執行時 sys.path 修補。`.python-version` 固定 3.13.5；支援範圍和檢查版本以 pyproject／實際 CI 矩陣為準。`uv sync --locked` 安裝依賴後，`make ci-fast` 採離線執行，不偷偷升級 lock。執行環境、平台與是否實測分別記收據，不把 CI 上的 macOS 當作使用者本人 Mac 已測。

建庫工具本身也使用 uv；其 env check／schema check 不等於產品 `make ci-fast`。排除 `.venv`、runtime data 和 secrets 後才允許發布。鎖檔不同步應明確失敗，不在驗證時靜默升級。

## Task 分支與 Subtask commit

基線之後所有修改先綁真實 Task／Bug／Spike 與 parent Story。Commander 指定 `codex/<task-id>-<slug>`、base SHA、cwd；每 Task 獨立 worktree，不在共用 main 施工。

S1、S2、S3 等有限 Subtask 留在 Task body。每個 Subtask 固定輸入、exact writable/frozen、commands、預期值和停止點，形成一個可理解英文 commit，例如 `task-12 S1: persist immutable evidence objects`。不同 Task／Subtask 不混提交，不作空 commit 偽造進展。

Commander 是預設唯一 Git writer，檢查精確 diff/staged paths；worker 沒有明示授權不 stage／commit／restore。合流保留 Subtask 歷史，不 squash；shared commits 不任意改寫。Push／merge 在本次授權內才執行。

## 完成與 readback

Task 合流需行為正反 tests、owner focused 驗證、同批必要文件更新、frozen schema/oracle 未放寬、獨立 Reviewer exact candidate 收據。milestone／Story 還需必要完整 gate、durable reopen 或 live 證據，按 AC 分帳。

Task Done 還需所有必要 Subtasks、main 遠端 readback 與 Issue／Project 真正結案。checkpoint 記 commit、rollback、dirty classification、背景程序、run/artifact identity。沒有真正派工就不寫「正在等待 agent」。

後續開發只寫入使用者明示的 private repository `z411392/paper-radar`，並沿用既有 `Paper Radar` Project；暫行直接實作的候選交付與驗收界線依 Rule15。先核對精確 repository ID、main SHA 與既有內容。禁止改 Kaledoxa、建立替代 Project、提升公開可見性、覆蓋未經核對的既有工作、套用舊狀態到已開始的工作，或執行產品 live 工作。
