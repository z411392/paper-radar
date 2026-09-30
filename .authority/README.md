# 規格版本對照：Git 保留階段

manifests/git-baseline.json 從所查看 commit 的同一樹解析 Git 文件及hash，不包含自己的commit SHA。規格ID與路徑分開；原文件的提案/接受聲明保留，不由清單升格。

issue_authority_enabled=false，acceptance_receipts為空。這是Git引用檢查，不是完整Issue Receipt工具、核可人證明、Release證明、備份還原或遠端保護。沒有假的accepted_by；#60作者轉錄不冒作獨立核可。

正式切換先依Rule80及附件第5/10/11節驗收：完整必要內容、穩定ID、hash、scope、依賴/前版、可驗證核可來源、單一寫入/版本比對、合併前重查、最終commit解析、保護及匯出還原。Task必要限制不能漏；receipts新增不覆寫。

能力未驗收前Git規格有效，Issue只引用，原spec與歷史不刪。GitHub與Git之間無本次已證的原子切換，hash不證明誰接受或永久可取得。
