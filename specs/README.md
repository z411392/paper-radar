# Story 規格與交付入口

文件歸屬依 [Rule 80](../.claude/rules/80-documentation.md)，角色、派工與執行接續依 [Rule 15](../.claude/rules/15-execution-strategy.md)。本頁只提供操作模板與導航。

## 唯一層級

`Epic → Story → Task／Bug／Spike` 使用 GitHub 原生 parent/sub-issue；Task 內有限步驟以 S1、S2、S3 等 checklist 表示，不另建第四層 issue。每張 issue 恰好一個 type label。

Story 取得真實 GitHub issue number 後才建立 `specs/<number>-<slug>/`，只放 `spec.md`、`plan.md`、`progress.md`。建庫前的 seed 不進 repository，也不成為日後的同步資料源。

## 操作

從 Event Storming 取 actor／command／event／例外／可觀察終點，形成能交付使用者價值的 Story。spec 擁有專屬 SC／AC；plan 定介面、依賴、writable/frozen、正反 oracle 與整合；progress 記 dated evidence，不能複製 Project Status／Priority／Sprint。

Task body 記 Task Pack、有限 Subtasks、直接依賴及停止點。每個 Subtask 的 Given／When／Then、exact scope、expected output、驗證命令與 Story AC 都要可追溯。Analysis／Design／Verify 是 leaf 內階段，不再建 Agile child。

完整控制流程：需求影響檢查 → 模型與契約 → 真實 Issues → Story 三檔 → Task-local Ready → 明示派工 → deterministic Verify → 獨立 Review → Commander 合流 → Project field readback。沒有 agent runtime 或 live credential 的 leaf 留明確缺件，不影響無關工作。

## Story 導覽

- [初始化本機工作區並發布可增減的關注範圍](5-local-workspace/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/5)
- [增量取得 arXiv 論文並在失敗後從正確位置續跑](9-arxiv-discovery/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/9)
- [把來源記錄整理成研究身份、版本與可引用證據](13-versioned-evidence/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/13)
- [產生能回查原文的繁體中文白話解說](17-grounded-explanations/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/17)
- [預覽並可靠寄出每日精選，重跑不重建通知](21-daily-email-digest/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/21)
- [讓本機自動執行並在睡眠或中斷後有界續跑](25-durable-cli-worker/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/25)
- [補齊羽球與跨出版社來源，分清每個領域的實際覆蓋](29-five-domain-coverage/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/29)
- [以 SQLite 全文與 FAISS 語意搜尋找回論文，索引可重建](33-local-hybrid-retrieval/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/33)
- [把重要修訂與更正通知給曾經收到研究的人](37-revision-correction-notices/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/37)
- [在本機回查閱讀紀錄並調整關注與回饋](41-local-reading-feedback/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/41)
- [備份與恢復本機研究資料，復原後不誤寄歷史內容](45-consistent-backup-restore/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/45)
- [看見來源健康、解說品質與成本，完成有證據的整合驗收](49-operational-evidence/spec.md) — [Issue](https://github.com/z411392/paper-radar/issues/49)
