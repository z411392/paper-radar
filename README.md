# Paper Radar

本機論文雷達。正式產品需求、架構與交付入口見 [docs/README.md](docs/README.md)；開發者先讀 [CLAUDE.md](CLAUDE.md)。

目前候選包含 uv 套件與工程防線、SQLite／不可變檔案儲存，以及真正的 CLI 工作區初始化。尚未取得獨立 Reviewer ACCEPT，不能把本分支的測試通過當成整個產品完成。關注設定、論文來源、FAISS、模型與郵件仍需後續 Task 實作。

## 入口

- [產品需求](docs/delivery/requirements-specification.md)
- [交付順序](docs/delivery/mvp-phases.md)
- [事件與領域模型](docs/architecture/event-storming.md)
- [Context Map 與程式目錄](docs/architecture/context-map.md)
- [資料模型與儲存契約](docs/data-model.md)
- [Story 規格導覽](specs/README.md)
- [本機工作區的直接證據](specs/5-local-workspace/progress.md)
- Paper Radar（既有 Project；尚未取得欄位同步證據）

GitHub Issues／Project 是工作與執行狀態來源。本庫不保存另一份 Task YAML、Markdown 看板或 backlog JSON。

## Python 與可執行命令

Python 使用 uv；正式規則見 [.claude/rules/90-operations.md](.claude/rules/90-operations.md#python-與-uv)。

```bash
uv sync --locked
uv run --locked python -m apps.cli version
uv run --locked python -m apps.cli init --help
uv run --locked python -m apps.cli init --workspace "$HOME/paper-radar-data"
make ci-fast
make package-check
```

初始化位置必須是專用的新目錄、允許恢復的未完成初始化目錄，或本產品已辨識的工作區。陌生資料、symlink、遺失 DB 卻留有舊內容時，命令會拒絕；沒有 force 或自動清除選項。使用者原有資料不因參數錯誤被改寫。

`init` 只建立 0001 的工作區／物件登錄 schema，回傳 workspace_id、epoch、external_effects_enabled、schema_version。重跑保留原身分；預設外部副作用停用。它不匯入關注設定、不抓來源、不建立向量，也不寄信。

Migration 原文只有 root `migrations/0001-object-registry.sql`；建置時納入 wheel 與 sdist，執行時以 package resource 讀取。非 editable 安裝後不依賴目前所在目錄。具體測試與失敗修正見 Story progress。
