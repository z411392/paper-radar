# 產品路線與穩定 Exit

本文件只定義成果順序、必要條件與證據導覽，不記即時 Task Status／Priority／Sprint。產品尚未經完整驗收；規劃存在不等於 Exit 成立。

先用第一條垂直切片驗證閱讀價值，再擴來源／檢索。備份最小契約從 P1 設計，P2 提供實際恢復。P4 的 HTTP/RSS 子範圍須先固定本機 transport 設計，不回頭阻擋 CLI 主流程。

## P1 — 第一份可回查的白話閱讀成果

- P1-E1：在乾淨本機建立持久工作區，匯入五領域種子，發布一份可版本化的關注設定。 Owner Story：https://github.com/z411392/paper-radar/issues/5；[驗收](../../specs/5-local-workspace/spec.md)；[證據](../../specs/5-local-workspace/progress.md)。
- P1-E2：把來源查詢、原始觀測、分頁與來源缺口保存下來，使重跑不造成重複收錄或跳過未完成頁面。 Owner Story：https://github.com/z411392/paper-radar/issues/9；[驗收](../../specs/9-arxiv-discovery/spec.md)；[證據](../../specs/9-arxiv-discovery/progress.md)。
- P1-E3：讀者看到一項研究，系統能分辨預印本、正式出版版和實際用來改寫的精確內容。 Owner Story：https://github.com/z411392/paper-radar/issues/13；[驗收](../../specs/13-versioned-evidence/spec.md)；[證據](../../specs/13-versioned-evidence/progress.md)。
- P1-E4：把研究的問題、方法、結果與未知說清楚，讀者不必先懂論文術語，也不被模型多加的結果誤導。 Owner Story：https://github.com/z411392/paper-radar/issues/17；[驗收](../../specs/17-grounded-explanations/spec.md)；[證據](../../specs/17-grounded-explanations/progress.md)。

## P2 — 本機自動摘要與安全恢復

- P2-E1：每天只整理少量符合條件的研究，先固定內容與寄送身份，再受控寄給指定收件人。 Owner Story：https://github.com/z411392/paper-radar/issues/21；[驗收](../../specs/21-daily-email-digest/spec.md)；[證據](../../specs/21-daily-email-digest/progress.md)。
- P2-E2：由同一 CLI 啟動長駐排程；關機期間保留缺口，恢復後不灌入大量歷史郵件。 Owner Story：https://github.com/z411392/paper-radar/issues/25；[驗收](../../specs/25-durable-cli-worker/spec.md)；[證據](../../specs/25-durable-cli-worker/progress.md)。
- P2-E3：取得包含 SQLite 與其引用物件的一致快照，搬到乾淨工作區後可查證資料，而且外部副作用預設關閉。 Owner Story：https://github.com/z411392/paper-radar/issues/45；[驗收](../../specs/45-consistent-backup-restore/spec.md)；[證據](../../specs/45-consistent-backup-restore/progress.md)。

## P3 — 五領域覆蓋與本機混合檢索

- P3-E1：在 arXiv 之外接入 PubMed／PMC、Crossref，讓五領域都有可查證的來源策略，而不是假設來源一樣齊全。 Owner Story：https://github.com/z411392/paper-radar/issues/29；[驗收](../../specs/29-five-domain-coverage/spec.md)；[證據](../../specs/29-five-domain-coverage/progress.md)。
- P3-E2：以文字與向量搜尋找回 current 的論文與解說，模型或索引更新不破壞既有身份。 Owner Story：https://github.com/z411392/paper-radar/issues/33；[驗收](../../specs/33-local-hybrid-retrieval/spec.md)；[證據](../../specs/33-local-hybrid-retrieval/progress.md)。

## P4 — 重要變更通知及本機閱讀管理

- P4-E1：把新研究、修訂、出版狀態、更正／撤稿分開，既不重複打擾，也不漏掉已寄論文的重要更正。 Owner Story：https://github.com/z411392/paper-radar/issues/37；[驗收](../../specs/37-revision-correction-notices/spec.md)；[證據](../../specs/37-revision-correction-notices/progress.md)。
- P4-E2：不只收信，還能從本機查看來源、摘要版本、已讀／收藏，並安全修改個人關注。 Owner Story：https://github.com/z411392/paper-radar/issues/41；[驗收](../../specs/41-local-reading-feedback/spec.md)；[證據](../../specs/41-local-reading-feedback/progress.md)。

## P5 — 可靠性、成本與整合證據

- P5-E1：用可重播的測試與真實能力收據回答哪些環節可靠、哪些仍有缺口，不以全綠單元測試當作整個產品完成。 Owner Story：https://github.com/z411392/paper-radar/issues/49；[驗收](../../specs/49-operational-evidence/spec.md)；[證據](../../specs/49-operational-evidence/progress.md)。

## 證據分帳

Hermetic 行為、真實來源／模型 capability、受控寄送、資料 readback／restore、獨立 exact-candidate ACCEPT 分別保存到 owning Story progress。單一 source PASS 不外推其他來源；SQL syntax／constraints PASS 不證明 application wiring。

每個 Exit 有必要 AC 與直接依賴；只有所有必要條件的實際收據成立才提升產品判定。局部工程缺件不等於全案阻塞；依 Rule15 保留 task-local readiness。
