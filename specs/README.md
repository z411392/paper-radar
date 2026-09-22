# Story 規格與交付入口

歸屬依 [Rule80](../.claude/rules/80-documentation.md)，角色及安全依 [Rule15](../.claude/rules/15-execution-strategy.md)。

## 目前採 GIT_RETAINED

既有 spec.md 保留 SC/AC，必要 plan.md 保留版本化設計，不能因治理更新先刪除。Issue 引用適用版本，不把最新本文當已接受規格。新 Story 不強制建立三檔；新正式規格先指定一個 Git owner，版本保存/核可/恢復與切換能力驗收後才可改由 Issue 承擔。

progress.md 不再追加手動進度或重複收據；原檔與 Git 歷史保留。研究、失敗、驗證寫 comments；Issue 關閉不使仍適用的規格自動失效。必要長期設計按責任回 BC/合作規格，不在 Story 形成另一套永久規則。

Task 本文即施工資訊，派工引用具體版本。小 Task 可無 Parent Story；工作階層與 BC 分開，子任務關閉不代替整合驗收。

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
