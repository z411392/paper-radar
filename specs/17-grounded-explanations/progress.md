# 產生能回查原文的繁體中文白話解說：證據與決策

Story：https://github.com/z411392/paper-radar/issues/17

## 2026-09-22 規劃基線

已將使用者的本機儲存與 Kaledoxa 治理要求反映到 [spec](spec.md)、[plan](plan.md) 與 Issues 階層。本輪交付的是規劃，不是產品程式完成。

文件影響：需求見共通 R01–R18；成果順序見 Roadmap；事件語言見 Event Storming；owner／交接見 Context Map；工作分解見本 Story 原生 child Tasks。舊 PostgreSQL／獨立 worker／平行文件庫提案不作現行前提。

本 Story 的產品驗證命令尚未執行，exit code 為 N/A；真來源／真模型／真實寄送／獨立 ACCEPT 收據皆未提供。建庫資料包自己的靜態檢查與 SQL 測試，不構成本 Story 的產品證據。

後續重大決策與測試在此追加帶日期的證據，記 exact SHA、命令、結果與範圍；不在本檔維護 Status／Priority／Sprint，也不因 Issue 已建立而宣稱 Story accepted。

## 2026-09-23 PO 選定產品 LLM

使用者補充「llm 我會接 openrouter 的 gemini 3.8 flash」。[本 Story 決策收據](https://github.com/z411392/paper-radar/issues/17#issuecomment-5782002995) 與 [#19 接線研究](https://github.com/z411392/paper-radar/issues/19#issuecomment-5782008358) 已追加；本節不是獨立驗收或真實推論測試。

已讀 OpenRouter 官方模型頁、Quickstart、Structured Outputs、Provider Routing。核對 model ID `google/gemini-3.8-flash`、gateway base URL，以及schema能力按endpoint判斷、provider failover不等於model fallback。查詢官方文檔沒有呼叫付費推論；模型頁存在不等於此使用者帳號已開通或輸出品質已證明。

五類影響：共通R10及D01反映已決定的gateway/model；Roadmap順序不變；Event Storming不新增業務事件；BC/agents邊界不變，具體transport規劃在本plan；#19承接實作、#18/#20引用選型，未建立新一套模型派工矩陣。Embedding模型、effort/請求參數、預算、live測試等仍各有owner，不因模型已選就宣稱全數已決定。

獨立docs/config分支 `codex/19-openrouter-model-policy` 基於 #10 作者候選 `ed7980dd386d919e7e839a36f14ac5eecbe2b4d2`；不修改該query compiler或其他上游。修改限共通requirements、本plan/progress與新增無秘密的llm.example.toml，預設enabled=false。原Story AC、SQL、uv.lock、產品source/tests及Rule15均不改。最終commit/PR及驗證結果由Issue讀回追加；不預寫PASS，不合流main或關閉Task。
