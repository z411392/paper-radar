# Paper Radar

本機論文雷達。正式產品需求、架構與交付入口見 [docs/README.md](docs/README.md)；開發者先讀 [CLAUDE.md](CLAUDE.md)。

目前 60 分 MVP 已具備可執行的本機工作區、arXiv 採集、canonical paper/evidence 投影、OpenRouter 繁體中文解說、daily digest、SMTP Email 與 durable worker 主線。main 是可執行 source of truth；正式排程直接執行 main 的 workflow event SHA，不再依賴 detached runtime pin。\n\n本輪刻意不把 FAISS／hybrid retrieval、完整五領域覆蓋、reading feedback、backup/restore 產品化、health dashboard、localhost HTTP/RSS 當作可用 MVP 的前置條件；這些保留為後續工作，不阻擋每天找新論文、生成解說與寄送。

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

每日信不把所有 paper event 都當「新論文」顯示：
- `new_work`：一般每日精選；
- `revision_available`：標成 `[更新]`／「論文更新」；
- `newly_accessible`：標成 `[新可讀]`／「新增可取得」；
- `late_discovery`：保留在 catalog history，不進正常 daily paper candidates。

是否真的寄過，不靠 subject 或畫面猜測；用
`paper-radar digest status --env-file ...` 讀 durable delivery history。
`notification_ledger=accepted` 才代表 provider 已明確接受該通知；`reserved` 是尚未完成，
`unknown` 則因可能已送達而禁止自動重寄。


正常 arXiv 路徑不會把多年以前的 paper 當成今日新論文寄出：若一筆 arXiv revision
在 Paper Radar 第一次觀測時，距離其 published/updated occurrence 已超過 14 天，
catalog 會將事件記成 `late_discovery`。這筆歷史仍保留，但 daily digest event reader
不把 `late_discovery` 列入候選。

要直接查看「已寄／未寄／失敗／狀態不明」，使用同一份 worker env：

```bash
uv run --locked python -m apps.cli digest status \
  --env-file "$HOME/.config/paper-radar/worker.env"
```

預設顯示 `local` reader 最近 10 期；可用 `--limit 1..100` 調整。每期輸出
`delivery_state`，每篇輸出 `notification_state` 與 `send_status`：
`accepted/sent` 才表示 SMTP provider 已明確接受；`reserved/pending` 表示尚未寄成、
但已先佔住 notification identity 以避免下一輪重複排入；`unknown` 代表無法確認 provider
是否已接受，因此不自動重寄；`failed` 與 `cancelled` 也會保留在歷史。



### GitHub Actions full live MVP smoke

SMTP 與 public arXiv 各自已有獨立 live smoke；要驗證完整產品主線時，使用
`Live MVP paper email smoke`。這個 workflow 只有手動 `workflow_dispatch`，不會在
push/PR 自動執行。

它會先透過 production arXiv query compiler、rate limiter、HTTPS transport 與 parser，
在最近 7 天的 `cs.LG/stat.ML` 找一篇仍落在 freshness window 內的真實論文，再用該篇
exact title 跑完整 production path：

`real recent arXiv -> real OpenRouter -> verified zh-TW summary -> real Gmail SMTP`

digest 與寄送不再由測試直接呼叫 use case；live smoke 會透過實際 worker scheduler
依序跑 `harvest_window/explain_snapshot -> prepare_digest -> dispatch_digest`，最後再跑一輪
replay 確認不會重寄。probe 與 worker 共用 production arXiv rate-limit state；worker 的
24 小時 submittedDate window 會定位在 probe 找到的實際 submission，而不是歷史固定 cursor。

除了既有 7 個 SMTP Repository secrets，還需要：

- `PAPER_RADAR_OPENROUTER_API_KEY`

每次手動 run 先做 1 次 bounded OpenRouter structured-generation smoke；只有這個
provider boundary PASS 才繼續完整 MVP job。完整路徑使用 canonical `machine_learning`
domain，再執行 4 次 real model-backed stage（claims、relevance、reading card、support
verification），最後只寄一封 digest smoke。因此一次成功的手動 run 最多 5 次 real
OpenRouter request；第一個 provider smoke 失敗時不會繼續燒後續 4 次或寄信。

full-live stage 使用 $2 的 test budget admission。這是會產生實際 OpenRouter 費用與真實
email 的 `live_external` 驗證；只需要執行一次 `Live MVP paper email smoke`，不需要先
另外跑 `Live OpenRouter smoke`。

2026-09-29 已取得一次完整 live PASS：GitHub Actions run `36524033966`（underlying
MVP head `5a65bff2a43ff0183d615791074666bcdfa99f83`）先通過 paid OpenRouter gate，再通過
`real arXiv -> 4 real model stages -> verified zh-TW summary -> prepare_digest ->
dispatch_digest -> Gmail SMTP`；full-live pytest receipt 為 `1 passed, 15 deselected in
52.73s`。當時的舊 smoke head 尚未套用 late-discovery freshness 修正，固定 target 是
2017 年的 `Attention Is All You Need`，因此 Gmail 收到的那封信只作為歷史 transport/E2E
PASS 證據，不代表今日論文選擇。現行 smoke 已改成 recent-paper discovery，不再把 Attention
當真實每日精選 target。isolated one-shot branch 在 run 建立後已恢復為 manual-only head，
`main` 與 MVP branch 沒有 push-trigger live workflow。

### 手動 GitHub Action：live SMTP smoke（非 CI）

這不是 CI，也不是 push／PR check。它只是用 GitHub-hosted runner 執行一次明示觸發的
真實 SMTP smoke；若要從 GitHub-hosted runner 驗證真實 SMTP，可在 repository
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


### GitHub Actions 每日正式排程（非 CI）

`Daily Paper Radar` 是正式 daily worker，不是 smoke/CI。workflow 每天在
`Asia/Taipei 08:15` 執行，runtime pin 在已取得 full-live receipt 的 exact SHA
`89f6caa6aeebbe0579ac769e43d969d6e2f80f8b`；後續開發 branch 繼續 commit 不會自動改變
每天寄信的 production code。

GitHub-hosted runner 每次都是新機器，因此 daily workflow 不把 workspace 當暫存資料。
每輪會從 `paper-radar-daily-workspace` artifact 還原完整 workspace，包含 harvest cursor、
paper/catalog history、`notification_ledger`、delivery outbox 與 attempts；跑完後即使 worker
失敗也會再保存 workspace。SMTP/OpenRouter secrets 與 recipient email 只寫到 runner 的
owner-only 暫存 `worker.env`，不包含在 workspace artifact。

第一次從未有過 daily state 時，workflow 會初始化 runtime workspace 並明示 enable effects。
之後若已經有 daily run 歷史卻找不到 workspace artifact，workflow 會 fail closed，不會自動
建立新的空 workspace；這避免遺失「已寄過」紀錄後重複投遞。確認真的要重置時，才手動
`workflow_dispatch` 並指定 `bootstrap=true`。

每輪用正式 daemon 跑 bounded 8 分鐘，poll 10 秒，讓 arXiv rate-limit、explanation、
digest、SMTP 4xx 的 5 分鐘 retry 都能沿原 workflow state machine 推進。結束時會執行
`digest status --limit 10`，Actions log 可直接看到 durable `sent/pending/unknown/failed`
狀態。workspace artifact 保留 30 天；正常每天執行會持續產生新的最新 state。

這個 scheduled workflow 使用與 live MVP smoke 相同的 Repository secrets：
`PAPER_RADAR_OPENROUTER_API_KEY` 加上 7 個 `PAPER_RADAR_SMTP_*` secrets。它不使用
push/pull_request trigger，也不執行 `make ci-fast`。

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

canonical 四-domain profile 因此最多需要 7 次 reservation，現在合計約 `$1.978368`；
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
