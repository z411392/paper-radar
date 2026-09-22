# Paper Radar

本機論文雷達。正式產品需求、架構與交付入口見 [docs/README.md](docs/README.md)；開發者先讀 [CLAUDE.md](CLAUDE.md)。

本次建庫交付為需求與設計基線、資料 schema 草案、工程治理及工作分解，尚不是可執行產品。沒有取得真來源、模型、郵件或本機獨立 Reviewer 的驗收收據。

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

Python 使用 uv；正式規則見 [.claude/rules/90-operations.md](.claude/rules/90-operations.md#python-與-uv)。規劃期可執行 `uv sync --locked` 與 `make env-check`。目前沒有產品 CLI 或產品 `ci-fast`；不能以環境同步成功視為 T01 完成。
