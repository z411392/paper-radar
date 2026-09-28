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
uv run --locked python -m apps.cli init --workspace "$HOME/paper-radar-data"
make ci-fast
make package-check
```

初始化位置必須是專用的新目錄、允許恢復的未完成初始化目錄，或本產品已辨識的工作區。陌生資料、symlink、遺失 DB 卻留有舊內容時，命令會拒絕；沒有 force 或自動清除選項。使用者原有資料不因參數錯誤被改寫。

`init` 未指定 `--with-profiles` 時只建立 0001 的工作區／物件登錄 schema，回傳 workspace_id、epoch、external_effects_enabled、schema_version。重跑保留原身分；預設外部副作用停用。它不匯入關注設定、不抓來源、不建立向量，也不寄信。

Migration 原文位於 root `migrations/`；只將明確啟用的 0001／0002 納入 wheel 與 sdist，執行時以 package resource 讀取。非 editable 安裝後不依賴目前所在目錄。具體測試與失敗修正見 Story progress。

## 關注設定 CLI（候選，尚未正式驗收）

先使用專用測試目錄。SQLite runtime 的正式使用核對仍見 [Issue #50](https://github.com/z411392/paper-radar/issues/50)，不要把功能測試通過當作引擎修補或正式資料使用證明。

```bash
uv sync --locked
uv run --locked python -m apps.cli init --workspace "$HOME/paper-radar-test-data" --with-runtime
uv run --locked python -m apps.cli domains import --workspace "$HOME/paper-radar-test-data" --file config/domain-seeds.json
uv run --locked python -m apps.cli profile publish --workspace "$HOME/paper-radar-test-data" --file config/watch-profile.mvp.json
uv run --locked python -m apps.cli profile show --workspace "$HOME/paper-radar-test-data" --id personal
uv run --locked python -m apps.cli profile pause --workspace "$HOME/paper-radar-test-data" --id personal
uv run --locked python -m apps.cli profile resume --workspace "$HOME/paper-radar-test-data" --id personal
```

`config/watch-profile.mvp.json` 是目前最短可用路徑，只選擇 arXiv 能直接覆蓋的四個領域，因此不會替未啟用的 PubMed／Crossref 建立工作。`config/watch-profile.example.json` 保留作多來源進階範例。

對單一 workspace 的 MVP，worker runtime 設定放在 owner-only `.env`；arXiv 節流狀態會自動使用 `<workspace>/state/arxiv-rate-limit.json`，不需要另外設定路徑。舊的 runtime flags 只保留相容與進階覆寫，不是正常 quickstart。

`--with-profiles` 明確啟用 0001＋0002，既有第 1 版工作區可以升級且保留身分。升級後再次初始化須帶相同選項；省略時不自動降版。`profile show`、發布與匯入不會順便建立工作區或跑 migration；舊 schema 會回報 `schema_upgrade_required`。

要修改關注內容，另存 JSON 範例、修改 scope／filters／domains，再使用 `profile publish ... --expected-revision N`，N 是目前讀回的 revision。第一次建立可以不指定；不同新內容不能省略版本檢查。已發布的相同舊內容重試回覆原 revision，但 `current_revision` 不倒退。查看舊版可用 `profile show ... --revision N`；lifecycle 與 current_revision 仍表示目前狀態。

JSON 是匯入格式，SQLite 才是發布後權威；重新匯入種子只補缺，不能用範例覆蓋新定義。空領域清單是沒有選取領域，不表示選全部。暫停後發布不會偷偷重啟；`resume` 不寄信、不清除歷史。當前沒有 domain 新修訂、active profile 列表、rename 或明示還原舊設定的命令。


## 每日 Email 設定

不需要後台，也不需要手工改 SQLite。runtime workspace 建好後，可直接建立每日 Email subscription：

```bash
uv run --locked python -m apps.cli digest subscribe-email \
  --workspace "$HOME/paper-radar-data" \
  --reader-id local \
  --recipient-ref recipient:primary \
  --timezone UTC \
  --local-time 08:00 \
  --max-items 5
```

同一組設定重跑是 idempotent；修改時間、收件人 reference 或篇數時會遞增
`policy_version`。SQLite 只保存 `recipient:primary` 這個 subscription reference；
實際 Email 地址與 SMTP/OpenRouter secrets 放在 owner-only worker `.env`。


## MVP worker：一份 .env 啟動

複製範例到私有位置後直接編輯，不需要把 source/model/mail 設定拆成十幾個 CLI flags：

```bash
mkdir -p "$HOME/.config/paper-radar"
cp config/worker.env.example "$HOME/.config/paper-radar/worker.env"
chmod 600 "$HOME/.config/paper-radar/worker.env"
# 編輯 worker.env：workspace、OpenRouter key、budget、收件 Email、SMTP
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
