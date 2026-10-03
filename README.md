# Paper Radar

GitHub Actions 排程的新論文翻譯 Email bot。正式產品需求、架構與交付入口見 [docs/README.md](docs/README.md)；開發者先讀 [CLAUDE.md](CLAUDE.md)。

目前 MVP 已具備可執行的 durable workspace、arXiv／PubMed 採集、canonical paper/evidence 投影、OpenRouter 繁體中文解說、分領域 daily digest、SMTP Email 與 durable worker 主線，並已取得 recent-paper live E2E PASS。main 是可執行 source of truth；正式排程直接執行 main 的 workflow event SHA，不再依賴 detached runtime pin。

FAISS／hybrid retrieval、搜尋／閱讀後台、reading history／bookmark／feedback、localhost HTTP／RSS、修訂更正通知、產品化 backup/restore、health dashboard、macOS launchd 正式部署都已明確移出產品 scope，不是延後 backlog。未來擴充只接受能直接改善「找到哪些新論文、如何篩選、如何翻譯整理、如何可靠寄 Email」的能力。

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

正常 production 由 GitHub Actions 為每個領域建立獨立 workspace；每個 workspace
只追一個 domain，所以每個領域會形成獨立 Email 與獨立 durable state。正式追蹤的五個
domain 是：

- `deep_learning`：深度學習，來源 arXiv。
- `machine_learning`：機器學習，來源 arXiv。
- `statistics`：統計，來源 arXiv。
- `badminton`：羽球，來源 PubMed。
- `male_reproductive_urology`：男性生殖學／泌尿科醫學，來源 PubMed。

本機開發仍可在 owner-only `worker.env` 用 `PAPER_RADAR_PROFILE_DOMAINS` 與
`PAPER_RADAR_PROFILE_SCOPE` 指定單一或多個 domain。worker 啟動時會先驗證 runtime schema v24，
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
PAPER_RADAR_DIGEST_MAX_ITEMS=100
```

`run-worker --env-file ...` 每次啟動都會 idempotent 對齊本機
`local / recipient:primary` email subscription；設定沒變就不增加 `policy_version`，
設定有變才更新。舊的 `digest subscribe-email` CLI 保留作相容與手動管理，不是正常 quickstart。

SMTP transport 必須明示加密模式：implicit TLS（常見 port 465）使用
`PAPER_RADAR_SMTP_SECURITY=ssl`；需要 STARTTLS（常見 port 587）的 provider 改成
`PAPER_RADAR_SMTP_SECURITY=starttls`。不支援 plaintext SMTP。

每日 Email 只寄 `new_work`。正式排程會依領域分成不同郵件，subject 會帶領域名稱，例如 `Paper Radar｜統計｜每日新論文 2 篇`。`late_discovery`、`revision_available`、`newly_accessible`、`correction`、`retraction` 可留在內部 catalog/history，但不進正式 daily digest。

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
在最近 23 小時的 `cs.LG/stat.ML` 找一篇仍落在 production 24 小時 harvest window 內的真實論文，再用該篇
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
domain，再執行 3 次 real model-backed stage（claims、relevance、reading card），最後只寄一封 digest smoke。因此一次成功的手動 run 最多 4 次 real
OpenRouter request；第一個 provider smoke 失敗時不會繼續燒後續 3 次或寄信。

full-live stage 使用 $2 的 test budget admission。這是會產生實際 OpenRouter 費用與真實
email 的 `live_external` 驗證；只需要執行一次 `Live MVP paper email smoke`，不需要先
另外跑 `Live OpenRouter smoke`。

2026-09-30 已取得目前收斂版的完整 live PASS：GitHub Actions run `36723227240`，產品 source 對應 `main` SHA `89e4522fa528be11d1316501e08835a8697ee078`；one-shot branch 只增加觸發用 workflow 差異，產品 Python source 與該 main 相同。實際 receipt 為 `1 passed, 15 deselected in 38.17s`，並驗證：

- real recent arXiv target 被記為 `new_work`；
- `claim_extraction`、`relevance_assessment`、`abstract_reading_card` 三個 real OpenRouter stage 全部 succeeded，且各有非零 cost receipt；
- current summary 為 `zh-TW` 且 `qa_state=passed`；
- worker 依序完成 `prepare_digest`、`dispatch_digest`；
- `delivery_outbox.state=provider_accepted` 且只有 1 筆 delivery attempt；
- 再跑一輪 worker 為 `processed_jobs == 0`，證明同一份 state 不會自動重寄。

正式 `main` 的 live workflow 仍維持 manual-only `workflow_dispatch`；production daily workflow 不使用 push trigger。

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

`Daily Paper Radar` 是正式 daily worker，不是 smoke/CI。workflow 設定為每天 `Asia/Taipei 08:17` 排程，直接 checkout 該次 workflow event 的 `main` source；不再使用 detached runtime SHA pin。GitHub scheduled workflow 是 best-effort，實際 runner 開始時間可能因 GitHub 排程佇列而晚於 08:17；Email 在該次 run 完成後送出。

GitHub-hosted runner 每次都是新機器，因此 daily workflow 不把 workspace 當暫存資料。
正式 workflow 使用五個獨立 artifact：`paper-radar-daily-<domain_id>`。五個 domain job
以 `max-parallel: 1` 串行執行，避免同時打 arXiv／NCBI；每個 artifact 各自保存 harvest cursor、
paper/catalog history、`notification_ledger`、delivery outbox 與 attempts。SMTP/OpenRouter
secrets 與 recipient email 只寫到 runner 的 owner-only 暫存 `worker.env`，不包含在 artifact。

第一次從未有過 daily state 時，workflow 會初始化 runtime workspace 並明示 enable effects。
之後若已經有 daily run 歷史卻找不到 workspace artifact，workflow 會 fail closed，不會自動
建立新的空 workspace；這避免遺失「已寄過」紀錄後重複投遞。確認真的要重置時，才手動
`workflow_dispatch` 並指定 `bootstrap=true`。

每輪執行 4 個 finite `run-worker --once` cycle，不再啟動 daemon 後靠 shell timeout 強制殺掉。
每日 arXiv／PubMed harvest 會完整投影 metadata；符合該 domain 的新論文會全部排進 explanation queue，沒有 top-N 或「精選」上限。正式 daily job 會持續跑 finite `--once` cycles 直到當天 queue 清空後才建立該領域 Email，因此不會在還有論文待翻譯時先寄 partial digest。`PAPER_RADAR_DIGEST_MAX_ITEMS` 只保留給舊 subscription schema 相容，正式 selection 不再用它截斷內容。結束時會執行
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
reading card，也就是 `selected_domains + 2` 次 request。
domain-independent 的 claims／reading card 會走 durable generation cache，
不會因同一 paper 跨 domain 重複付費。

canonical 四-domain profile 因此最多需要 6 次 reservation，現在合計約 `$1.818624`；
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

### 本機 CLI 的定位

本機 `run-worker`、`worker.env` 與相容 flags 只供開發、診斷及手動 smoke；正式長期部署只有 GitHub Actions `Daily Paper Radar`。不維護 launchd 作為產品安裝方式。



設定檔須為普通 UTF-8 檔案，最多 1,000,000 bytes，不接受最終 symlink 或 FIFO。全部參數先解析，錯誤非零退出；成功才輸出 JSON。真正安裝 wheel 後也能在 repo 外執行，SQL 不依賴目前目錄；範例檔仍須用自己可存取的路徑指定。
