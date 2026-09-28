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
`personal` profile；相同設定重跑不新增 revision。若 `.env` 改回先前已發布過的設定，
worker 會明確把那個歷史 revision 設回 current，而不是新增一份重複 revision 或繼續沿用
較新的設定。

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

SMTP transport 必須明示加密模式：implicit TLS（常見 port 465）使用
`PAPER_RADAR_SMTP_SECURITY=ssl`；需要 STARTTLS（常見 port 587）的 provider 改成
`PAPER_RADAR_SMTP_SECURITY=starttls`。不支援 plaintext SMTP。


### GitHub Actions full live MVP smoke

SMTP 與 public arXiv 各自已有獨立 live smoke；要驗證完整產品主線時，使用
`Live MVP paper email smoke`。這個 workflow 只有手動 `workflow_dispatch`，不會在
push/PR 自動執行。

它會用固定的 public arXiv 論文 `Attention Is All You Need` 跑完整 production path：

`real arXiv -> real OpenRouter -> verified zh-TW summary -> real Gmail SMTP`

digest 與寄送不再由測試直接呼叫 use case；live smoke 會透過實際 worker scheduler
依序跑 `harvest_window/explain_snapshot -> prepare_digest -> dispatch_digest`，最後再跑一輪
replay 確認不會重寄。為了避免 smoke 從 2017 cursor 繼續歷史 catch-up，測試只注入可控
workflow clock 與一筆已 catch-up 的 harvest history marker；production 預設 clock 不變。

除了既有 7 個 SMTP Repository secrets，還需要：

- `PAPER_RADAR_OPENROUTER_API_KEY`

每次手動 run 先做 1 次 bounded OpenRouter structured-generation smoke；只有這個
provider boundary PASS 才繼續完整 MVP job。完整路徑使用 canonical `machine_learning`
domain，再執行 4 次 real model-backed stage（claims、relevance、reading card、support
verification），最後只寄一封 digest smoke。因此一次成功的手動 run 最多 5 次 real
OpenRouter request；第一個 provider smoke 失敗時不會繼續燒後續 4 次或寄信。

full-live stage 使用 $2 的 test budget admission。這是會產生實際 OpenRouter 費用與真實
email 的 `live_external` 驗證；只需要執行一次 `Live MVP paper email smoke`，不需要先
另外跑 `Live OpenRouter smoke`。未手動執行前不代表 full-live PASS。

### GitHub Actions live SMTP smoke

若要從 GitHub-hosted runner 驗證真實 SMTP，可在 repository
`Settings → Secrets and variables → Actions` 建立以下 Repository secrets：

- `PAPER_RADAR_SMTP_HOST`
- `PAPER_RADAR_SMTP_PORT`
- `PAPER_RADAR_SMTP_SECURITY`（`ssl` 或 `starttls`）
- `PAPER_RADAR_SMTP_SENDER`
- `PAPER_RADAR_SMTP_USERNAME`
- `PAPER_RADAR_SMTP_PASSWORD`
- `PAPER_RADAR_SMTP_RECIPIENT`

GitHub 的 `workflow_dispatch` 第一次要能手動觸發，workflow 檔必須已存在 default
branch；因此新 workflow 尚未合併前不會出現可用的第一次 `Run workflow`。合併到 default
branch 後，在 Actions 選 `Live SMTP smoke`、選擇要測的 branch，按一次 `Run workflow`
就會送出一封 bounded smoke email；manual dispatch 本身就是明示確認，不再另外要求 checkbox。

這個 workflow 只有手動 `workflow_dispatch`，不會在 push/PR 自動寄信；每次 run 只寄一封
固定 smoke mail，內容不含論文或模型資料。workflow 只檢查 secret 是否存在，不輸出 secret 值。

SMTP 明確回覆 4xx transient rejection 時，Paper Radar 會保留同一 outbox／idempotency identity，
把本次 delivery attempt 記為 failed，並由 workflow 在 5 分鐘後安全重試；5xx permanent rejection
則維持 terminal failed。timeout、socket 中斷或其他無法確定 provider 是否已接受郵件的情況仍標成
`unknown`，不自動重寄，以避免重複郵件。

### GitHub Actions live OpenRouter smoke

若要驗證真實模型 provider，在 repository Actions secrets 建立：

- `PAPER_RADAR_OPENROUTER_API_KEY`

之後在 Actions 選 `Live OpenRouter smoke`，選擇要測的 branch 並按 `Run workflow`。
workflow 只有手動 `workflow_dispatch`，不會在 push/PR 自動產生付費模型請求；
每次 run 只執行一個 bounded structured-generation request。

live smoke 使用 production HTTPS transport、固定 `google/gemini-3.8-flash` 與 commissioned
provider policy，並驗證 strict JSON 回應、returned model、finish reason、token usage 與非零
cost receipt。API key 只由 GitHub secret 注入，不寫入 repo 或 log。


## MVP worker：一份 .env 啟動

複製範例到私有位置後直接編輯，不需要把 workspace/profile/digest/source/model/mail
設定拆成 CLI flags：

```bash
mkdir -p "$HOME/.config/paper-radar"
cp config/worker.env.example "$HOME/.config/paper-radar/worker.env"
chmod 600 "$HOME/.config/paper-radar/worker.env"
# 編輯 worker.env：workspace、研究範圍、OpenRouter、SMTP、digest 時間

uv run --locked python -m apps.cli init \
  --env-file "$HOME/.config/paper-radar/worker.env"
```

`init --env-file` 只從設定檔取 `PAPER_RADAR_WORKSPACE`，固定建立 current runtime schema；
不需要另外加 `--with-runtime`。Legacy `init --workspace ... --with-*` 保留給手動／進階用途。

`.env` 內容是 literal `KEY=VALUE`，不執行 shell、不支援 `export`、quotes 或
`$HOME` 展開；workspace 必須直接寫實際路徑。檔案必須是目前使用者擁有的 regular
file，且 group/other 不可讀寫。

範例中的 `replace-with-openrouter-api-key` 與 `replace-with-smtp-password` 是刻意保留的
無效 placeholder；未替換時 worker 會在任何 workspace/provider 操作前回
`example_secret_not_replaced`，不會等到第一次模型或寄信請求才失敗。

模型預算只需要設定 `PAPER_RADAR_MODEL_MONTHLY_BUDGET_USD`。單次 request 的 budget
reservation 由固定 OpenRouter request/token/price policy 自動推導，不是使用者設定。
一篇 paper 的保守 admission 是 1 次 claims、每個 selected domain 各 1 次 relevance、1 次
reading card、1 次 support verification，也就是 `selected_domains + 3` 次 request。
domain-independent 的 claims／reading card／support verification 會走 durable generation cache，
不會因同一 paper 跨 domain 重複付費。

canonical 四-domain profile 因此最多需要 7 次 reservation，現在合計約 `$1.861888`；
月預算低於依所選 domain 數動態計算的門檻時，worker 會在啟動時直接拒絕。
canonical example 使用 `$2.00/月`，至少能保守 admission 一篇 paper 完成四個 domain 的
relevance coverage 與 verified summary。

第一次上線前明示開啟外部副作用，之後 worker 只需要設定檔。若 `.env` 開啟 live mail
但 workspace 尚未 enable effects，worker 會在啟動時直接回
`external_effects_disabled`，不會等到真正寄信時才默默卡住：

```bash
uv run --locked python -m apps.cli effects enable \
  --env-file "$HOME/.config/paper-radar/worker.env"

# smoke / 手動跑一輪
uv run --locked python -m apps.cli run-worker \
  --env-file "$HOME/.config/paper-radar/worker.env" \
  --once

# 長駐
uv run --locked python -m apps.cli run-worker \
  --env-file "$HOME/.config/paper-radar/worker.env"
```

### macOS launchd

長期每天自動跑時，launchd 也使用同一份 `worker.env`，不另外保存 workspace、polling、
provider flags 或 secrets。複製 `deploy/macos/com.paper-radar.worker.plist.example` 到
`~/Library/LaunchAgents/com.paper-radar.worker.plist`，只把兩個 placeholder 換成絕對路徑：

- `__PYTHON__`：已安裝 Paper Radar 的 Python，例如 repo 的 `.venv/bin/python`。
- `__ENV_FILE__`：owner-only `worker.env` 的絕對路徑。

載入／重載：

```bash
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.paper-radar.worker.plist" 2>/dev/null || true

launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.paper-radar.worker.plist"

launchctl kickstart -k "gui/$(id -u)/com.paper-radar.worker"
```

plist 本身不應放 OpenRouter key、SMTP password 或其他 runtime 設定；修改日常設定只改
`worker.env`，再重啟 launchd worker。

`--env-file` 模式不允許再混用 workspace/source/model/mail runtime flags，避免兩套設定互相覆蓋。
舊 flags 暫時保留相容；MVP 正常操作以 `config/worker.env.example` 為準。


設定檔須為普通 UTF-8 檔案，最多 1,000,000 bytes，不接受最終 symlink 或 FIFO。全部參數先解析，錯誤非零退出；成功才輸出 JSON。真正安裝 wheel 後也能在 repo 外執行，SQL 不依賴目前目錄；範例檔仍須用自己可存取的路徑指定。
