# 共通需求與驗收標準

## 產品權威

本文件為本產品的共通規則 owner。最新使用者明示裁決在被取代範圍內優先；本文件／Story spec 為本次規劃基線，不代表使用者逐項核准所有可調預設，也不代表架構 freeze 或產品 ACCEPT。

2026-09-22 的早期規劃曾包含本機檔案系統、FAISS、SQLite 與較廣的研究管理能力。\n\n2026-09-30 最新明示裁決取代上述產品範圍：Paper Radar 是 GitHub Actions 排程的論文 Email bot。產品只需要定時抓新論文、篩選／去重、用 OpenRouter 產生繁中翻譯與白話整理、建立 daily digest 並以 SMTP 寄送；只保存完成這條流程所需的最小 durable state。FAISS／檢索、閱讀後台、閱讀歷史／收藏／回饋、localhost HTTP／RSS、修訂更正通知、產品化 backup/restore、health dashboard、本機 launchd 正式部署均不屬於產品 backlog。

2026-09-23 補充裁決：產品 LLM 使用 OpenRouter 的 Gemini 3.8 Flash。具體選型歸 R10／D01，決策與查證紀錄見 [Story #17](https://github.com/z411392/paper-radar/issues/17#issuecomment-5782002995)。這不改變工程 agents 的分工，也不代表 embedding 模型已決定或已授權執行付費推論。

`paper-radar`／私有 repository 為保守的初始化名稱與可見性選擇。worker 收進 CLI 是前輪解釋後沿用的第一版設計選擇，不是六邊形架構的硬性條件。

## 共通規則

<a id="r01"></a>
### R01 — GitHub Actions 與最小 durable state

正式部署是 GitHub Actions scheduled workflow。SQLite／artifact 只用來保存來源 checkpoint、去重 identity、模型結果、digest/outbox 與通知狀態，使下一次 ephemeral runner 能安全續跑；它不是使用者研究資料庫。不得要求 FAISS、向量庫、後台服務或本機 daemon 才能完成正式每日寄送。

共通驗收：正常重跑不重抓／重寄；durable artifact 遺失時 fail closed；secrets 不寫入 artifact。

<a id="r02"></a>
### R02 — 可增減的關注範圍

初始軟體工程、深度學習、機器學習、統計學、羽球；DomainDefinition 與 WatchProfile revision 分開。既有來源涵蓋的領域可以改資料；新來源能力仍需 adapter。

共通驗收：停用某領域不刪除已讀／已寄歷史；重新啟用不把歷史全部重寄；發布版本可追溯。

<a id="r03"></a>
### R03 — 可信來源與權限

官方學術來源優先。MVP 以來源提供的書目與 abstract 為主要輸入；不要求免費全文才可進 daily email，也不為了補全文自動繞過來源限制。

共通驗收：每份解說能回到實際 source observation；只有 abstract 時不得冒稱讀過全文。

<a id="r04"></a>
### R04 — 最新與版本

分開首次公開、來源更新、首次觀測及日期精度；catalog 可保留不同事件型別，但正式 daily Email 只消費 `new_work`。

共通驗收：舊文晚被索引不寄成今日新論文；同一研究的後續 revision 不重新寄成新論文。

<a id="r05"></a>
### R05 — 保守身份辨識

Work／Manifestation／Revision 分層。確定識別碼與可信明示關係優先；fuzzy／embedding 僅形成候選。更正關係不可當作同一內容直接 merge。

共通驗收：同 DOI 正規化後不重複；標題相近但不同研究維持分開；identity merge 保留 alias、證據與撤回方法。

<a id="r06"></a>
### R06 — 增量收集

每個 source binding／配置版本獨立保存進度；觀測 durable 後才前移 checkpoint。分頁、重疊窗、初次回填、有界補抓和週期核對分開。

共通驗收：注入 429、timeout、過期 cursor、格式漂移及半頁失敗；成功來源不掩蓋失敗來源 coverage gap。

<a id="r07"></a>
### R07 — 相關性

規則與必要 LLM assessment 分層；直接相關／相鄰／不確定／無關和執行失敗分開。不需要 embedding 或向量搜尋才能完成相關性判斷。

共通驗收：同研究多領域只一張卡；不可把未校準分數呈現成機率；來源失敗不被視為零合格論文。

<a id="r08"></a>
### R08 — 白話內容

保留原始摘要、忠實翻譯、繁體中文白話解說三份資料。卡片回答問題、方法、結果、限制、推薦理由，保留專名及數字限定條件。

共通驗收：摘要未交代不得寫成作者未做；預印本不得冒稱同行評審；比喻不得變成研究結果。

<a id="r09"></a>
### R09 — 可驗證的解說

每個重要 claim 連到同一版本 snapshot 的 quote／offset／section／table anchor。程式驗數字與主體、指標、對照組、單位；模型檢查支持性而不是替來源補證據。

共通驗收：對調 baseline 和 proposed 值、否定翻轉、杜撰樣本或因果要阻擋；abstract_only／selected_sections／full_text 由實際閱讀覆蓋決定。

<a id="r10"></a>
### R10 — 生成成本與適用性

快取綁 source revision、evidence hash、schema／prompt／model／glossary／output profile。變更關注範圍只重判推薦，無需重寫未變的論文內容。

依 2026-09-23 使用者裁決，產品內的 LLM 抽取、翻譯、白話解說及必要語意核對採 OpenRouter gateway，model ID 為 `google/gemini-3.8-flash`。不得自動換其他模型、加入 model fallback 清單、改成 `openrouter/auto` 或自行加日期／free／batch suffix。模型不可用時明示失敗或保留待重試工作，不用其他模型假裝完成。工程 agents 的 provider/model/effort 仍唯一依 Rule15，不由這項產品選型改動。

共通驗收：舊請求晚回不得發布為 current；額度不足保存 budget_blocked；不靜默換模型或追加付費。回傳 JSON、來源與數字驗證不能因已選模型而跳過。記錄 gateway、requested/returned model、實際 provider（可得時）、參數版本及用量；model slug 不當成 immutable weights 或輸出逐位可重現的證據。

<a id="r11"></a>
### R11 — Out of scope：檢索與向量

FTS、FAISS、embedding search、hybrid retrieval、搜尋 UI 不屬於 Paper Radar Email bot 的產品需求。既有歷史規格／migration 可保留相容性，但不得作為 release exit、active backlog 或 daily pipeline 前置。

<a id="r12"></a>
### R12 — 每日新論文與推送

email 為第一通道；固定 digest snapshot 與 transactional outbox。通知身份依讀者×研究事件×channel，不因重跑、跨來源或模型重寫產生新通知。

共通驗收：相同事件重播不增加 request；SMTP 可能已接受而本端逾時時保留 delivery_unknown，不無條件重送。

<a id="r13"></a>
### R13 — Out of scope：修訂／更正通知產品

正式產品只寄「新論文」daily digest。revision/correction/retraction 可留作內部 catalog metadata 或既有相容性，但不要求主動寄送更正通知，也不作 release exit。

<a id="r14"></a>
### R14 — GitHub Actions 排程

正式長期入口為 `.github/workflows/daily-paper-radar.yml`。workflow 由 GitHub Actions schedule 啟動，恢復 durable workspace，跑 bounded worker window，最後無論成功失敗都嘗試保存最新 workspace。CLI `run-worker` 是 workflow 的執行入口及本機開發／診斷工具，不是要交付給使用者的本機常駐產品。

共通驗收：schedule 重跑安全；runner 重建後可續跑；工作失敗使 Actions 明確失敗而不是偽裝成零篇論文。

<a id="r15"></a>
### R15 — Out of scope：使用者 backup / restore 產品

不提供獨立 backup/restore UI、CLI 產品或搬移研究資料庫的產品承諾。正式排程只需要 GitHub Actions artifact 的最小 durable state 恢復契約；artifact 缺失時 fail closed，是否人工 reset 由 workflow_dispatch 明示處理。

<a id="r16"></a>
### R16 — 安全與隱私

不使用 Kaledoxa session 或帳號。Secrets 只在本機安全配置。來源文字是資料而不是 agent 指令；限制 fetch URL／redirect／大小，避免私人網路讀取與輸出 HTML 注入。

共通驗收：惡意摘要無法讀密鑰或更換收件者；log 不含 secrets／完整私人註記；GET 不修改閱讀偏好。

<a id="r17"></a>
### R17 — 最小可觀測性

不做 health/cost dashboard。GitHub Actions run 本身是主要操作介面；失敗必須紅燈，log/step summary 能看出 source/model/mail 哪一段失敗，並能讀 durable delivery status。模型仍保留月費 budget gate，避免失控付費。

<a id="r18"></a>
### R18 — Out of scope：閱讀後台與回饋

localhost HTTP、RSS 閱讀產品、搜尋、收藏、閱讀歷史、reader feedback 均不屬於目前產品。Email 是正式使用者介面。

## 可調整的初始預設

這些是提案設定，不是來源 SLA，也不是當次收集／寄送授權：Asia/Taipei 每日排程；每個領域寄出該期間所有符合條件的新論文，不做 top-N／精選上限；初次回填有界；預印本允許並標記；daily Email 只寄新論文。abstract 足以進 MVP 解說流程，不要求免費全文入口。

預設 `delivery_enabled=false`，直到 live commissioning 設定完整。LLM gateway/model 已依 R10 選定；金額預算、收件地址與 SMTP 寄送方式仍需明確配置。LLM 憑證只留本機安全環境，範例預設 `enabled=false`；模型已選不等於已授權付費呼叫。可先完成 hermetic 路徑，不以 credentials 缺失阻擋純工程工作。

## 局部待決事項

D01（模型選型已決定）：2026-09-23 PO 選用 OpenRouter／`google/gemini-3.8-flash`。模型選型不再是待決事項；憑證、推論參數、預算與受控 live commissioning 仍需明確配置。產品不需要 embedding model。
D02：寄件服務與指定收件者。由 PO 在 S05 controlled delivery 前指定；不影響 outbox 與 preview。
D03：模型費用仍由明示 budget gate 保護，但不得用論文篇數 cap 或 top-N ranking 代替費用控制。若 budget 不足，workflow 應明確失敗／延後，而不是靜默漏寄部分論文。
D04：HTTP/RSS 已依 2026-09-30 產品裁決移出 scope，不再是待決事項。

工程缺件、adapter 實測、runtime session 尚未建立，留在 owning Task，不把它們偽裝成未決產品需求。

## 非目標

不做全學術宇宙完整收錄承諾、不驗證研究結論必然正確、不公開鏡像 PDF、不做多租戶 SaaS／社群收集／GraphRAG、不自動發文或投資建議，不先建立跨產品共用平台。也不做 FAISS／全文搜尋、閱讀後台、收藏回饋、localhost HTTP/RSS、修訂更正通知、使用者 backup/restore 或 health dashboard。未來擴充原則上只接受能直接改善「找到哪些新論文、如何篩選、如何翻譯整理、如何可靠寄信」的能力。
