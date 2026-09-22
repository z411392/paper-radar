# Paper Radar

本機論文雷達。正式產品需求、架構與交付入口見 [docs/README.md](docs/README.md)；開發者先讀 [CLAUDE.md](CLAUDE.md)。

目前分支提供 Task #6 的套件／CLI 工程基礎與架構防線候選；論文收集、SQLite 工作區、FAISS、模型改寫與寄信尚未接線。沒有獨立 Reviewer ACCEPT，不是可用的完整論文系統。

## 入口

- [產品需求](docs/delivery/requirements-specification.md)
- [交付順序](docs/delivery/mvp-phases.md)
- [事件與領域模型](docs/architecture/event-storming.md)
- [Context Map 與程式目錄](docs/architecture/context-map.md)
- [資料模型與儲存契約](docs/data-model.md)
- [Story 規格導覽](specs/README.md)
- Paper Radar（既有 Project；尚未取得存取與欄位同步證據）

GitHub Issues／Project 是發布後的工作與狀態來源。本庫不保存另一份 Task YAML、Markdown 看板或 backlog JSON。

## Python 環境

Python 使用 uv；正式規則見 [.claude/rules/90-operations.md](.claude/rules/90-operations.md#python-與-uv)。

```bash
uv sync --locked
uv run --locked python -m apps.cli version
make ci-fast
```

`version` 只回報真實套件與 Python 版本，不建立資料目錄、不連網、不假裝完成 workspace 初始化。`make ci-fast` 驗證目前已實作的工程範圍；通過不等於 Story AC、來源能力或獨立驗收成立。先執行 sync 準備依賴，後續 gate 使用離線 uv 執行。打包及非 editable wheel 的隔離檢查見 `make package-check`。
