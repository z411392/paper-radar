# 協作與執行策略

依 Kaledoxa `6862a93f1a1cc133a5dedfa25ec414b460f3b4ed` 現行 Rule 15 移植。這是 Paper Radar 的唯一 execution routing authority；模型名稱是沿用的派工設定，不是本次對模型可用性作出的查證。不得默默換 provider／model／effort。

<a id="temporary-direct-implementation"></a>
## 2026-09-22 暫行直接實作

使用者在 [Epic #1](https://github.com/z411392/paper-radar/issues/1#issuecomment-5774317170) 明示暫由本對話實作。此節在明確範圍內優先於下方預設 runtime 矩陣；不是永久撤除 Kaledoxa 的角色治理。

本對話可以在既有產品範圍內整理必要 Task-local 技術契約、實作、以 uv 執行隔離測試、建立候選分支／Subtask commits／PR，以及維護 Issues。Git writer 與批次協調暫由本對話負責，不冒稱 Antigravity Commander 或 Codex agent 已啟動。

若尚無獨立設計 runtime，必要的工程測試由本對話先明列正反結果並保留 RED，再進行實作；這些是作者制定的測試，不標為獨立 Architect frozen receipt。現有 Story AC、產品規則、已凍結外部 oracle 不因本例外而放寬。後續調整測試須說明是修正缺陷或排版，不得修改期待結果換綠燈。

自測與 CI 通過只允許交付候選，不構成 independent ACCEPT。未取得另一位實際隔離且非作者的 Reviewer exact-candidate 收據前，不合流 main、不關閉 Task、不標 Project Done。S1／S2 的已實作和自測證據與待驗收分開記錄。技術依賴仍可使用已明示為未驗收的候選，不冒充已接受基線。

不取得付費模型、真來源、模型下載、真實郵件、正式資料變更、部署或永久刪除歷史的額外授權。Project 的寫入需要實際工具／權限與 readback；Issue 操作不代表 Project 欄位已同步。既有 roster 留 UNBOUND，不填造 runtime IDs。

本輪第一批為 Task #6 的 S1／S2 工程基礎，S3 實際初始化仍需要 #7 的公開契約與儲存實作。收束到已測、已推送的候選分支／PR即可結束本批；這不是整張 Task 或 Roadmap 已完成。使用者收回暫行委派或實際綁定獨立 runtime 時，後續派工回到下方預設拓撲。

<a id="execution-topology-and-dispatch"></a>
## 執行拓撲與控制面

| 角色 | runtime／模型政策 | 責任與界線 |
|---|---|---|
| Product Owner | 使用者 | 產品方向、scope、權威裁決；授權有限 GitHub 與產品文件 materialization |
| Product / Governance Coordinator | Issue-level control plane；不加入 runtime roster | 在明示授權內 materialize 產品決策、Issues hierarchy、產品導航與 scope transfer；不取得工程 ACCEPT、模型預算或 Commander execution ownership |
| Commander | Antigravity；Gemini 3.8 Flash | 本產品唯一 execution control plane、fresh-read/reconcile、readiness、dispatch、continuation、integration、預設唯一 Git writer、Project Status update＋readback |
| Architect | Codex；gpt-6-astra；Medium 預設 | 一個跨 BC 共用具名角色；analysis／planning／architecture／design／root cause／contract-oracle；不 mechanical implementation，不驗收自己設計的 candidate |
| Implementer | Antigravity；Gemini 3.8 Flash | 按 context-local writer ownership／worktree 有限實作；不發明產品決策、不改 frozen oracle、不 self-review |
| Reviewer | Codex；gpt-6-astra；一般檢查 Medium、final independent Accept High | 一個跨 BC 共用獨立具名角色；READ_ONLY、NON_INTERACTIVE、NO_WRITE_AUTHORITY；不修改受審 candidate 或 GitHub |

控制順序：PO decision → Product Design authority → durable GitHub product authority → Commander fresh-read/reconcile → 必要 Architect → Implementer → deterministic Verify → independent Reviewer Accept → Commander integration → Project Status readback。

不採每 BC 一個 Architect／Reviewer，也不採一個全能 agent 兼 Design→Implement→Accept。真正獨立產品可有自己的角色 sessions；禁止把 Kaledoxa 既有 session IDs 複製進本產品。`.agents/roster.json` 是 noncanonical projection，task IDs 未建立時用 null，不能偽造 ID 或宣稱 agent 已啟動。

BC 隔離靠當次 Task 的版本化引用、Affected BCs 與按需展開。共享角色以序列或實際可隔離的 runtime 使用；Implementers 可以按 owner 拆開，不能同時寫相同檔案。優先重用本產品既有適任 named task。只有角色衝突、review isolation、context 污染、session 失效、安全隔離或真正不同產品才新建，不因不同 BC 新建 Astra 角色。

## Effort

Medium 是 analysis、普通 design／review／schema 審查的預設。High 必須有縮小的候選和理由：CROSS_BC_CONTRACT_GATE、CONCURRENCY_CORRECTNESS_GATE、SECURITY_TENANCY_GATE、MIGRATION_GATE、FINAL_ARCHITECTURE_FREEZE、FINAL_INDEPENDENT_ACCEPT、MEDIUM_UNRESOLVED_CONFLICT，或具實質後果的產品權威歧義。不要因為叫 architecture 就一律 High。

禁止 Low、xhigh、max、ultra。Commander 決定 escalation，worker 不自行升級。真實 runtime 不支援指定模型時，回報路由缺件，不以替代模型假裝相同收據。

## Task 本文與最小派工信封

依 Rule80，Task 本文是施工資訊唯一維護處，不另存內容相同的完整 Task Pack。信封只引用 Task URL、適用規格版本/body checksum、程式 base/candidate、角色/effort、branch/cwd、必要操作授權及返回條件。缺件補 owning Task，不維護兩份各自演化的內容。

Task 需能找到成果、正式依據、修改範圍、Affected BCs、步驟、失敗處理、檢查與停止條件。獨立小 Task 可無 Parent Story；只展開必要契約、使用方和測試，安全仍必讀。hash 識別版本，不證明核可。

修改本文前 fresh-read 與版本比對；單一 writer 序列更新後讀回。沒有跨工具原子更新保證時明示限制。新規範不沿用舊核可，今天的 Issue 不覆蓋舊分支適用規格。

<a id="task-local-readiness"></a>
## Task-local readiness

七項分別填 PROVEN／MISSING／NOT_EXAMINED 並附直接證據：相關 Story SC／AC；Roadmap phase／Exit 的實際位置；直接必要依賴；沒有影響本 Task 的未決產品規則；接口、writable/frozen、失敗語意、oracle 固定；Implementer 不需替上游做產品／架構決策；writer、integration owner、操作權限及停止點明確。

七項皆 PROVEN 才是 READY_FOR_IMPLEMENTATION，不新增全案 Design 第八條門檻。Ready 不等於 dispatch 授權、Story acceptance 或 Phase complete。只重查需求變更所影響的必要項；live credentials 不阻擋不需要 live 的 leaf。

## Reviewer runtime isolation

Reviewer 只能讀 current authority、exact candidate、tests／logs／manifest；必要 deterministic verification 也不得污染受審 checkout。不得寫 source、tests、evidence、Git、Issue、PR 或 Project。

可用 runtime 必須實際 read-only、非互動，不能只在 prompt 寫唯讀。沿用的 CLI 形式為 `codex --sandbox read-only --ask-for-approval never exec ...`；具體 runtime flag 支援在執行前核對。禁止 yolo、danger-full-access、workspace-write 或同等 unrestricted mode。

隔離不可用時記 REVIEW_RUNTIME_ISOLATION_UNAVAILABLE，不靜默降低權限。收據記 exact base/candidate SHA、runtime mode、commands/results；head 改變就要對新 SHA 重新驗。Output capture 放 repo 外，不授權寫受審 worktree。未提供實際 Reviewer runtime 的本次規劃包沒有 independent ACCEPT。

<a id="commander-continuation-ownership"></a>
## 接續與合流

worker 的 STOP／HANDOFF／ACK 只結束該 invocation，continuation 仍由 Commander 持有。收到結果先 consume、保存證據、判 Ready／必要缺件、執行下一直接動作。

`REVIEW_REJECT → LOCAL_REVISION_REQUIRED → IMPLEMENTER_DISPATCH`；local engineering finding 不轉 USER_HANDOFF 或等待外部 ChatGPT。修復→Verify→獨立 Reviewer 反覆到 clean exact candidate ACCEPT。`REVIEW_ACCEPT → INTEGRATION_READY → COMMANDER_INTEGRATION → PORTFOLIO_RECONCILIATION`。

外部 ChatGPT 只在 PO 明示要求時是 optional audit，不是每個 candidate 的 mandatory gate。tests green 不等於 ACCEPT；候選 commit 存在不等於遠端已交付。

leaf queue 為空須重讀 Roadmap、OPEN Epic／Story、PO handoff 和 main；已有產品權威足以限定範圍時可建立下一 bounded Task，不發明新 feature。只要現有授權內仍有可執行工作或未消費結果，就不能以中間狀態作成功 terminal。兩次無淨進度後停止擴大修補，重新查證真正 blocker。

真正外部 blocker 必須有 exact scope、dependency、為何本機無法解決、owner 與 clear condition；局部阻塞不凍結全案。若 runtime 無法取得 route completion，明示 LOCAL_RUNTIME_CONTINUATION_UNAVAILABLE，不能虛構稍後會自動喚醒。

先前規劃／建庫批次的授權不自動等於整條 backlog 完成；後續逐批開發依最新 PO 授權與本檔暫行直接實作條款執行。

<a id="project-status-synchronization"></a>
## GitHub Project Status

Status 唯一選項：Backlog、Ready、In Progress、Review、Blocked、Done。只有 Commander 核對 execution truth 後更新並 readback。外部腳本只可作當次明示 Commander 操作的機械執行，不取得自己的狀態判定權；bootstrap 僅把新建 item 初始化 Backlog，續跑不得覆蓋已有人調整的欄位。

Ready 需七項收據；In Progress 需真實 active dispatch；Review 需候選＋自測已交付給獨立 Reviewer；Done 需整張 Task 所有必要 Subtasks、完整 gate、獨立收據、main 合流／遠端推送與結案證據。單一 slice 不代表整 Task Done。

內部測試失敗、可由 owner 補的契約／設計不標整 Task Blocked；只有直接必要外部限制且無可安全推進步驟才用 Blocked。沒有 active execution 不維持假的 In Progress。每次 field mutation 立即查詢 readback，不把 exit 0 當成功。

Project Status 不等於 Story acceptance／Roadmap Exit／Product GO。Priority、Sprint、Views 也只在 Project；MD 不保存即時投影。
