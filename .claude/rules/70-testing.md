# 測試與驗收

測試依 truth owner 與回饋成本兩維度安排，不設無 owner 的根 tests 或 tests/regression 庫。app 流程測試在 `src/apps/<app>/tests/{acceptance,contract,integration,unit,e2e}`；feature 在 `src/libs/<feature>/tests/{unit,contract,integration,fixtures}`；全域架構／治理在 `src/libs/kernel/tests/{architecture,governance}`。跨系統驗收須明確 owner，再選 placement，不能變成無主根 tests。

沿用 Tier 0 guards、Tier 1 pure domain、Tier 2 contracts/adapters、Tier 3 heavy compute、Tier 4 hermetic E2E、Tier 5 live 的區分。0–4 納入 `make ci-fast`，live 只在明示授權獨立 gate。回饋預算是工程目標，不以超時偷偷跳過測試。

## Frozen 與 Writable

Design 固定的 acceptance、contract、architecture、governance 外部 oracle 不在 Implementer writable。Implementer 只改指定 production 和相應 unit/integration/fixture。若 frozen oracle 有誤，回報 DESIGN_BLOCKED 交回 owner；不得改 oracle 換綠燈。

## BDD／TDD

每個 Task／Subtask 追到 Event Storming anchor → Story SC／AC → Given/When/Then → oracle／commands → revision／evidence。正例、反例、零結果、重複、失敗、中斷／重播逐項指定，無適用時說原因。

實作走 RED→GREEN→Refactor；文件／schema 設計只做有界內容與結構驗證，不虛構產品 RED。CLI/HTTP E2E 走正式 composition，不偷逐個呼叫 downstream 補接線缺口。

hermetic 不連外部來源、不下載模型、不寄信；合成 fixture 明示虛構且可重現。數字驗證需檢查主體／基準／單位，不只比字串；identity 誤合併、late response、FAISS rebuild、失去 lease、舊備份恢復都要反例。

## Gates

`make ci-fast` 最終包括 lint、typecheck、architecture-check、governance-check、contract、unit/integration、acceptance、e2e。尚未實作的 gate 不能以 exit 0 偽裝全綠。

deterministic checks 不等於 independent ACCEPT；live source／model、controlled delivery、data-backed UI、hermetic E2E 分帳。收據記 exact SHA、command、exit code、日期、input／artifact hash 與範圍；NOT_RUN 的 exit code 為 N/A。

規則測試要有故障注入及相近合法對照組，移除注入後再跑 gate。還原必須保存 exact working bytes 或用隔離 worktree，不能 git checkout 抹掉未提交正式變更。
