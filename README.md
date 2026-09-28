# Paper Radar

本機論文雷達。正式產品需求、架構與交付入口見 [docs/README.md](docs/README.md)；開發者先讀 [CLAUDE.md](CLAUDE.md)。

目前 MVP 候選已具備本機工作區、arXiv 採集、繁體中文摘要、daily digest 與 Email worker 主線；FAISS、完整 PubMed／Crossref 覆蓋與管理後台都不是目前 MVP 的前置條件。

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
uv run --locked python -m apps.cli init --workspace "$HOME/paper-radar-data" --with-runtime
make ci-fast
make package-check
```

初始化位置必須是專用的新目錄、允許恢復的未完成初始化目錄，或本產品已辨識的工作區。陌生資料、symlink、遺失 DB 卻留有舊內容時，命令會拒絕；沒有 force 或自動清除選項。使用者原有資料不因參數錯誤被改寫。

`init` 未指定 `--with-profiles` 時只建立 0001 的工作區／物件登錄 schema，回傳 workspace_id、epoch、external_effects_enabled、schema_version。重跑保留原身分；預設外部副作用停用。它不匯入關注設定、不抓來源、不建立向量，也不寄信。

Migration 原文位於 root `migrations/`；只將明確啟用的 0001／0002 納入 wheel 與 sdist，執行時以 package resource 讀取。非 editable 安裝後不依賴目前所在目錄。具體測試與失敗修正見 Story progress。

## 關注設定

正常 MVP 不需要先跑 `domains import` / `profile publish`。研究範圍也放在同一份 owner-only
`worker.env`：

```dotenv
PAPER_RADAR_PROFILE_DOMAINS=software_engineering,deep_learning,machine_learning,statistics
PAPER_RADAR_PROFILE_SCOPE=關注軟體工程、深度學習、機器學習與統計學的新 arXiv 論文。
```

目前 arXiv-only MVP 可選的 domain 是 `software_engineering`、`deep_learning`、
`machine_learning`、`statistics`。worker 啟動時會先驗證 runtime schema v24，
從 wheel 內建的 canonical `domain-seeds.json` idempotent 匯入 domain，再建立或更新
`personal` profile；相同設定重跑不新增 revision。

舊的 `domains` / `profile` CLI 與 JSON 範例保留作手動管理、歷史 revision 與未來多來源設定，
不是正常 MVP quickstart：

```bash
uv run --locked python -m apps.cli profile show --workspace "$HOME/paper-radar-data" --id personal
uv run --locked python -m apps.cli profile pause --workspace "$HOME/paper-radar-data" --id personal
uv run --locked python -m apps.cli profile resume --workspace "$HOME/paper-radar-data" --id personal
```

對單一 workspace 的 MVP，arXiv 節流狀態會自動使用
`<workspace>/state/arxiv-rate-limit.json`。舊 runtime flags 只保留相容與進階覆寫。


## 每日 Email 設定

正常 MVP 不需要先跑額外的 digest 設定指令。daily digest 的 timezone、寄送時間與篇數都放在
owner-only worker `.env`：

```dotenv
PAPER_RADAR_DIGEST_TIMEZONE=Asia/Taipei
PAPER_RADAR_DIGEST_LOCAL_TIME=08:00
PAPER_RADAR_DIGEST_MAX_ITEMS=5
```

`run-worker --env-file ...` 每次啟動都會 idempotent 對齊本機
`local / recipient:primary` email subscription；設定沒變就不增加 `policy_version`，
設定有變才更新。舊的 `digest subscribe-email` CLI 保留作相容與手動管理，不是正常 quickstart。


## MVP worker：一份 .env 啟動

複製範例到私有位置後直接編輯，不需要把 source/model/mail 設定拆成十幾個 CLI flags：

```bash
mkdir -p "$HOME/.config/paper-radar"
cp config/worker.env.example "$HOME/.config/paper-radar/worker.env"
chmod 600 "$HOME/.config/paper-radar/worker.env"
# 編輯 worker.env：workspace、研究範圍、OpenRouter、SMTP、digest 時間
```

`.env` 內容是 literal `KEY=VALUE`，不執行 shell、不支援 `export`、quotes 或
`$HOME` 展開；workspace 必須直接寫實際路徑。檔案必須是目前使用者擁有的 regular
file，且 group/other 不可讀寫。

第一次上線前明示開啟外部副作用，之後 worker 只需要設定檔：

```bash
uv run --locked python -m apps.cli effects enable \
  --workspace "$HOME/paper-radar-data"

# smoke / 手動跑一輪
uv run --locked python -m apps.cli run-worker \
  --env-file "$HOME/.config/paper-radar/worker.env" \
  --once

# 長駐
uv run --locked python -m apps.cli run-worker \
  --env-file "$HOME/.config/paper-radar/worker.env"
```

`--env-file` 模式不允許再混用 workspace/source/model/mail runtime flags，避免兩套設定互相覆蓋。
舊 flags 暫時保留相容；MVP 正常操作以 `config/worker.env.example` 為準。


設定檔須為普通 UTF-8 檔案，最多 1,000,000 bytes，不接受最終 symlink 或 FIFO。全部參數先解析，錯誤非零退出；成功才輸出 JSON。真正安裝 wheel 後也能在 repo 外執行，SQL 不依賴目前目錄；範例檔仍須用自己可存取的路徑指定。
