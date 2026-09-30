# 產品路線與穩定 Exit

## 2026-09-30 產品邊界

Paper Radar 是一個 GitHub Actions 排程的論文 Email bot，不是本機研究知識庫。

正式產品主線只有：

`GitHub Actions schedule -> 抓新論文 -> 篩選 / 去重 -> OpenRouter 繁中翻譯與白話整理 -> daily digest -> SMTP Email`

系統只保存讓排程可靠運作所需的最小 durable state，例如來源 checkpoint、論文/事件 identity、生成結果、digest/outbox 與已寄通知 identity。這些狀態是寄信流程的內部實作，不代表要做閱讀後台或個人研究資料庫。

## MVP — 可每天可靠寄出新論文

- E1：排程可從來源增量取得新論文；重跑、429、timeout 或短暫 provider deferral 不應造成漏抓或重複。
  Owner Story：https://github.com/z411392/paper-radar/issues/9
- E2：只對符合關注範圍的新論文產生繁體中文翻譯／白話整理；模型失敗明確失敗，不用其他模型偷偷替代。
  Owner Story：https://github.com/z411392/paper-radar/issues/17
- E3：每天建立固定上限的 digest 並可靠寄到指定 Email；相同事件重跑不重寄，unknown delivery 不自動重送。
  Owner Story：https://github.com/z411392/paper-radar/issues/21
- E4：GitHub Actions 保存並恢復最小 durable workspace；state 遺失時 fail closed，不自動建立空狀態造成歷史重寄。

目前 main 已作為可執行 source of truth；正式 Daily Paper Radar 直接執行 main 的 workflow event SHA。

## 可選的後續擴充

只有「增加可寄送的新論文來源／領域覆蓋」仍屬同一產品方向，例如 PubMed／PMC、Crossref 或新的領域 source adapter。這些擴充必須直接改善「能找到並寄出哪些新論文」，不能藉機擴成研究管理產品。

Owner Story：https://github.com/z411392/paper-radar/issues/29

## 明確非目標

下列項目不是延期功能，而是目前產品定義下的 out of scope：

- SQLite FTS / FAISS / embedding search / hybrid retrieval。
- 論文搜尋 UI、管理後台、localhost HTTP。
- 閱讀歷史、已讀、收藏、bookmark、reader feedback。
- RSS 閱讀產品。
- 修訂／更正／撤稿的主動通知產品。
- 本機 launchd 長駐服務作為正式部署方式。
- 使用者可操作的 backup / restore 產品。
- health dashboard、成本 dashboard 或額外營運後台。
- 為研究資料庫設計的 secondary-identifier enrichment。
- 任何不直接服務「定時寄新論文 Email」的知識管理能力。

已關閉的相關 Issues／PRs 保留為歷史紀錄，不應被後續 agent 當成待完成 backlog 重新啟動。

## 驗收原則

60 分 MVP 的 hard gate 只驗證可執行主線：CLI/工作區可啟動、增量 arXiv harvest、必要 projection/explanation scheduling、digest 行為、package 安裝。格式化、全庫型別債與已退出產品範圍的舊 fixtures 可獨立整理，但不得阻擋 daily email bot 上線。
