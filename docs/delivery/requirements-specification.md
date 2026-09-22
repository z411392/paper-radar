# 共通需求與驗收標準

## 產品權威

本文件為本產品的共通規則 owner。最新使用者明示裁決在被取代範圍內優先；本文件／Story spec 為本次規劃基線，不代表使用者逐項核准所有可調預設，也不代表架構 freeze 或產品 ACCEPT。

2026-09-22 明示：偵測新論文、白話解說、推送、關注領域可增減；本機檔案系統＋FAISS＋SQLite；文件、治理與 agents 分工跟 Kaledoxa 一致；本次可整理規格及建 GitHub repo／Project／工作分解。保留獨立產品，不修改 Kaledoxa。

`paper-radar`／私有 repository 為保守的初始化名稱與可見性選擇。worker 收進 CLI 是前輪解釋後沿用的第一版設計選擇，不是六邊形架構的硬性條件。

## 共通規則

<a id="r01"></a>
### R01 — 本機與儲存

單一讀者、本機執行；SQLite 保存狀態及關聯，檔案保存允許留存的原文／證據／原始向量，FAISS 是可重建的索引。沒有 PostgreSQL、pgvector、Redis、Kafka 或雲端物件庫前置。

共通驗收：重啟讀到相同設定與論文；刪除衍生 index 不遺失書目或原始向量；缺少原始物件明示完整性錯誤。

<a id="r02"></a>
### R02 — 可增減的關注範圍

初始軟體工程、深度學習、機器學習、統計學、羽球；DomainDefinition 與 WatchProfile revision 分開。既有來源涵蓋的領域可以改資料；新來源能力仍需 adapter。

共通驗收：停用某領域不刪除已讀／已寄歷史；重新啟用不把歷史全部重寄；發布版本可追溯。

<a id="r03"></a>
### R03 — 可信來源與權限

官方學術來源優先、aggregator 只補充。公開書目、免費全文入口、自動取得權限、模型處理／保存用途分別判斷；unknown 不當成允許或禁止的證據。

共通驗收：每份解說能回到 source observation／manifestation；只具摘要者不能冒稱有全文。未知存取權先保留待處理而不是偷偷抓取。

<a id="r04"></a>
### R04 — 最新與版本

分開首次公開、正式出版、來源更新、首次觀測及日期精度；new work、late discovery、revision、publication update、correction/retraction 分開事件。

共通驗收：舊文晚被索引只標補收錄；arXiv v2 與正式 DOI 不自動變成全新研究。

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

規則、語意相似度及必要 LLM assessment 分層；直接相關／相鄰／不確定／無關和執行失敗分開。羽球要求直接相關證據，泛運動另列相鄰。

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

共通驗收：舊請求晚回不得發布為 current；額度不足保存 budget_blocked；不靜默換模型或追加付費。

<a id="r11"></a>
### R11 — 檢索與向量

SQLite FTS5 與 FAISS 按明示模式運作；同一 embedding space 的模型版本、dimension、normalization、query/document prefix 固定。FAISS 只輸出穩定 embedding IDs，回查 SQLite 做資格過濾。

共通驗收：索引丟失可由原始向量重建；space 不符拒絕；已刪除／過期投影不出現在有效結果；degraded 模式需明示。

<a id="r12"></a>
### R12 — 每日精選與推送

email 為第一通道；固定 digest snapshot 與 transactional outbox。通知身份依讀者×研究事件×channel，不因重跑、跨來源或模型重寫產生新通知。

共通驗收：相同事件重播不增加 request；SMTP 可能已接受而本端逾時時保留 delivery_unknown，不無條件重送。

<a id="r13"></a>
### R13 — 取消與更正

停用設定、來源更正／撤稿、摘要失效在寄送前重查；更正通知可越過一般新文免費 gate，通知先前接收者。已寄版本不事後改寫。

共通驗收：取消後的 queued digest 不發送；更正能指出原訊息與受影響研究；不把更正誤記為新論文。

<a id="r14"></a>
### R14 — 本機排程與中斷

第一版長駐入口為 apps/cli 的 run-worker 子命令，呼叫 libs 用例；不是額外 BC 或 AI agent。排程與工作執行共一程序，持久 jobs 可續跑。

共通驗收：睡眠／關機期間不承諾執行；甦醒有界 catch-up，不連寄多天舊摘要；失去 lease 的舊程序不可提交。

<a id="r15"></a>
### R15 — 備份與恢復

一致 SQLite snapshot＋它引用的 immutable objects 才是可還原資料。FAISS 與 exports 可重建；原始向量不可只存 index。恢復到新 workspace，預設停用外部副作用。

共通驗收：恢復較舊備份可能缺少已寄紀錄，必須先 reconcile 再啟用寄送；不能聲稱舊備份本身能保證全歷史不重寄。

<a id="r16"></a>
### R16 — 安全與隱私

不使用 Kaledoxa session 或帳號。Secrets 只在本機安全配置。來源文字是資料而不是 agent 指令；限制 fetch URL／redirect／大小，避免私人網路讀取與輸出 HTML 注入。

共通驗收：惡意摘要無法讀密鑰或更換收件者；log 不含 secrets／完整私人註記；GET 不修改閱讀偏好。

<a id="r17"></a>
### R17 — 健康與品質

健康檢查顯示來源最後成功、最老 job、coverage、QA 拒絕、token 花費、index freshness 和 unknown delivery。結果數為零與失敗分開。

共通驗收：固定回放與跨五領域標註樣本分帳；deterministic PASS、live capability、data-backed consumer、independent ACCEPT 分開。

<a id="r18"></a>
### R18 — 閱讀與回饋

第一版可讀 email／Markdown export；後續 localhost HTTP 提供歷史、搜尋、收藏、偏好及來源健康；RSS／額外來源為後续切片。

共通驗收：回饋只改推薦，不改研究事實；重開可讀相同 evidence；外部郵件不放手機無法使用的 localhost 控制連結。

## 可調整的初始預設

這些是提案設定，不是來源 SLA，也不是當次收集／寄送授權：Asia/Taipei 每日 08:00；目標 5 篇、上限 10 篇、不湊數；初次回填 14 天；預印本允許並標記；新文推送要求已查證免費全文入口；即時推送關閉。機器讀過的證據範圍與使用者免費入口分開。

預設 `delivery_enabled=false`，直到 live commissioning 設定完整。LLM／embedding model、最大 token 預算、收件地址、SMTP／API 寄送方式均需明確配置；可先完成 hermetic 路徑，不以 credentials 缺失阻擋純工程工作。

## 局部待決事項

D01：真實模型與是否採本地 LLM。由 PO 在 S04 live commissioning 前指定；不影響 schema、fake port、grounding validators。
D02：寄件服務與指定收件者。由 PO 在 S05 controlled delivery 前指定；不影響 outbox 與 preview。
D03：每日閱讀量與費用上限。提案值可改；發信／付費前確認，不自動帶入前報告的舊模型價格。
D04：後續 HTTP/RSS 是否納入首個正式 release。S10 先列後續產品工作，不作第一封 email 的前置。

工程缺件、adapter 實測、runtime session 尚未建立，留在 owning Task，不把它們偽裝成未決產品需求。

## 非目標

不做全學術宇宙完整收錄承諾、不驗證研究結論必然正確、不公開鏡像 PDF、不做多租戶 SaaS／社群收集／GraphRAG、不自動發文或投資建議，不先建立跨產品共用平台。未來擴充需走相同 authority 和影響檢查。
