# Paper Radar — coding agent 共用入口

`AGENTS.md`、`GEMINI.md` 軟連結至本檔。`.agents/rules` 軟連結至 `.claude/rules`；不同工具不應假設彼此會自動載入規則。

## 實際必讀

1. [docs 入口](docs/README.md) 與 [完整規則索引](.claude/rules/00-index.md)。
2. [路線圖](docs/delivery/mvp-phases.md)、[需求](docs/delivery/requirements-specification.md)。
3. [Event Storming](docs/architecture/event-storming.md)、[Context Map](docs/architecture/context-map.md)。
4. 真實 Task issue、原生 parent Story／Epic，以及 `specs/<真實-story-number>-<slug>/{spec,plan,progress}.md`。
5. 本次 exact source、technical contract、鄰近 tests 與直接失敗證據。

同一 session 已讀且 hash 未變的內容可沿用。新使用者裁決先做 Rule 80 的五類文件影響檢查；不能只在對話答應。

## 權威及操作邊界

工程方法以 Kaledoxa `6862a93f1a1cc133a5dedfa25ec414b460f3b4ed` 的現行規則為移植基準。本庫只適配產品名稱、論文領域、路徑與尚未綁定的 runtime identities；不沿用 Kaledoxa 的私有資料、舊 Task ID、既有 session ID 或歷史 ACCEPT。

文件 ownership 唯一依 [Rule 80](.claude/rules/80-documentation.md)，角色、provider/effort、readiness、continuation 和 Project Status 唯一依 [Rule 15](.claude/rules/15-execution-strategy.md#execution-topology-and-dispatch)。本入口不再建立完整角色矩陣。

目前只有規劃與建庫操作授權。取得完整 fresh Task Pack 前，不開啟產品 live 收集、付費模型呼叫、寄信、資料重設或實際 agent 派工。建庫資料包與 deterministic checks 不等於 independent ACCEPT。

同一模組只有一位當次 writer；Commander 是預設唯一 Git writer，保留使用者 dirty work。不同 Task 使用專屬 worktree 與 `codex/<task-id>-<slug>` 分支；每個 S1／S2／S3 Subtask 個別提交，合流不 squash 掉追溯歷史。
