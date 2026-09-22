# 文件、正式依據與版本保存

本檔是文件歸屬唯一 owner；角色與審查依 [Rule15](15-execution-strategy.md)。2026-09-23 使用者明示依附件修正本專案；採用及盤點在 [Task #60](https://github.com/z411392/paper-radar/issues/60)。

## 採用範圍

來源：使用者本輪提供《分層式正式依據管理方法論(2).md》，3.0-draft／2026-09-23，SHA-256 ad1d4a126b4648c241b9f133cd2dbcacd8db225a240e84bd07e87096bf701059。原附件仍是 REFERENCE_METHODOLOGY；本次指示授權文件歸屬、工作分類及按需查找的專案化，不自行擴張操作權或構成獨立驗收。先前2.0採用與差異來歷保留在 Task #60 及 PR #61 的歷史；不以新附件倒改舊程式適用規格。

本專案採 GIT_RETAINED：正式 Story spec 與必要 plan 留 Git，Issue 引用適用版本。此方式可長期沿用，不是必須淘汰的過渡方案。先分清規則歸屬、停止重抄、按需查找並保留歷史。取消強制 Story 三檔、禁止 BC 文件及每批重寫 progress 的舊限制；不先刪 spec，不啟用 Issue 作唯一規格。

保留 Kaledoxa 式獨立審查、單一 Git writer、uv、安全與操作限制。不重切 BC、不更換 agents、不改檔案系統/SQLite/FAISS 或 OpenRouter Gemini 選型。新方法不擴大 live、付費、寄信、資料存取或刪除授權。

## 正式歸屬

同一規則在同一適用範圍與版本只有一個正式來源。跨 BC 不等於全局；公開規格優先由提供方維護。多方長期共同且無單一提供方時，才建立有明確 owner 與適用範圍的合作規格。

| 資訊 | 正式位置 |
|---|---|
| 產品、共通 requirements/NFR | docs/delivery/requirements-specification.md |
| 路線圖、穩定成果 | docs/delivery/mvp-phases.md |
| 全局事件流程、既有業務模型 | docs/architecture/event-storming.md |
| BC 上下游、合作及 import 邊界 | docs/architecture/context-map.md |
| BC 長期規則、公開合作契約 | 既有 BC 章節或正式 ports；需要獨立維護時 docs/domains/<bc>.md |
| 儲存一致性、精確資料結構 | docs/data-model.md；精確 schema 由 migrations/ 擁有，不手抄欄位 |
| Story 目標、SC/AC、必要設計 | 本專案指定的 specs/<number>-<slug>/spec.md 與必要 plan.md；Issue 引用，可長期沿用 |
| Task 本次要求、步驟、範圍及驗證 | Task Issue 本文，不另維護相同 Task Pack |
| 研究、提案、實作與審查回報 | Issue/PR comments 及必要版本化附件 |
| 即時實作者、審查指派與工作關係 | 指定 Issue/PR 欄位，使用時讀取 |
| Status/Priority/Sprint/Views | 使用者既有 Paper Radar Project，不以 Markdown 替代 |
| 程式、測試及規格的查找位置 | .context/，只導航，不存規格全文或即時狀態 |
| 接受的 Issue 內容與版本選用 | 完成切換前提後的 .authority/receipts/ 與 manifests/ |

Canonical Home、Authority Owner、Approver、Delivery Owner 分開識別。負責交付不等於能改全部 BC 規則。Git 版本以讀取的 commit 表達；來源文件原本的提案/接受狀態保留，不由索引或清單升格。

## 工作分類與按需查找

Epic 只在多張需求共同交付一個大成果時建立；Story 是可整體驗收的成果，不按資料庫、後端或資料夾機械拆單；Task 是授權範圍內的具體修改；Spike 必須有研究問題、資料/工具限制、時間或嘗試上限、交付物、停止條件及決策去向。證據不足或不建議採用可以是研究結論，不等於產品規格已接受。範本供填寫必要內容，不要求保留所有空章節；既有機器欄位與類型不為翻譯強制改名。

常用 Epic、Story、Task、Spike。Bug/Docs/Refactor 可作分類，不為套模板改寫既有歷史；小 Task 可沒有 Parent Story。Subtask 是關係或 Task 內有限步驟，不另設格式。BC 不是資料夾、微服務或工作單大小；Story/Epic 可跨 BC，子任務結案不代替整合驗收。

先確認共同安全底線與指定程式版本，再讀 Task 的適用規格、必要 Story、Affected BCs、供應方契約、目標程式/使用方/測試。公開介面、schema、安全、新使用方或衝突要求擴大閱讀；導航過期時回正式來源，不用相似度猜規則。已載入且仍適用的共同規則不必每次全文重讀。

Task 本文完整時，派工只引用來源、具體內容版本、角色、程式版本和操作範圍的短信封，不維護第二份完整 Task Pack。減少資訊不得省略安全底線。

## 提案、接受與歷史

本文分開已接受規格、目前執行計畫與未接受提案。修改本文不自動代表接受；變更要求不能沿用舊核可。研究預設是證據，接受具體版本後才回最近 owner，不整篇變全局規則。程式和 CI 是行為證據，不自動推翻規格。

停止向 progress.md 追加目前進度或重複實作收據；原檔與 Git 歷史保留。新研究、失敗和修正寫 comments；重要已接受內容仍需版本保存，不能只靠留言。GitHub 留言可改刪，append-only 是團隊寫入政策，不是平台不可變保證。規格接受、實作、自測、獨立驗收與工作結案分開。

## 版本保存與切換門檻

目前 .authority/manifests/git-baseline.json 只是同一 commit 的 Git 文件對照準備。從 commit C 讀 C 的清單與文件，不把最終 C SHA 寫回自身。ID 使用 repo 身分與規則語意，不以路徑作永久身分；文件改名不應改規格 ID。

Issue authority 尚未啟用；acceptance_receipts 為空，不造 accepted_by 或宣稱完整 Receipt 工具已驗收。正式移轉前需固定規範章節/格式，保存完整必要內容、hash、scope、依賴和前版關係，以及可驗證、綁具體版本的核可來源。必要 Task 限制同樣保存。

若選擇改由 Issue 保存正式規格，另需驗收單一寫入/版本比對、合併前適用規格重查、最終 commit 解析、匯出備份與還原、適當權限/保護。只因別的分支或尚未接受的提案更新，不自動推翻本次仍有效的規格版本。讀取、寫入、再讀回不等於防止並行覆寫；目前由單一寫入者序列修改本文，不宣稱任何 API 自帶 CAS 或跨工具交易。Receipt 新增不覆寫；hash 不證明核可人、不阻止刪除，也不保證永久取得。沒有跨工具原子性保證時明示限制。

按盤點→分類→草稿→接受與切換→驗證/恢復→清理推進。切換前舊來源有效，新位置只能是草稿；切換中暫停受影響工作。失敗回到明確舊來源並通知使用方，不先刪檔再補歷史。一般已授權工作不因全套工具未完成而停擺。

## 變更影響與格式

依內容檢查產品/路線圖、BC與全局流程、合作契約、Story/Task及使用方/測試，不要求每次重寫五份文件。ADR只記值得保存理由的重要決定。重大來源/查找方式變更才跑相應成效評估；結構測試不能冒稱 authority recall 或備份還原已達標。

本階段未完成 Issue authority 切換、獨立工具驗收或查找 benchmark。人類文件與 Issues 用繁體中文，英文術語附中文；識別與命令保留英文。文件不以粗體/斜體/底線強調；原始附件與歷史引文保留原貌。
