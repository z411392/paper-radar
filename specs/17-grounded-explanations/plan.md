# 產生能回查原文的繁體中文白話解說：技術方案

Story：https://github.com/z411392/paper-radar/issues/17。方案基線，非已凍結的 implementation contract。

## 依賴與 ownership

Context 及程式邊界：[Context Map](../../docs/architecture/context-map.md)。SQL／檔案／index 契約：[data model](../../docs/data-model.md)。

直接上游情境：https://github.com/z411392/paper-radar/issues/13。這是資料／能力相依，不要求無關 Task 完成。

| Task | owner | 預定實作位置 | SC／AC |
|---|---|---|---|
| https://github.com/z411392/paper-radar/issues/18 | paper_explanations | `src/libs/paper_explanations/application/commands/extract_paper_claims.py`, `src/libs/paper_explanations/prompts/`, `src/libs/watch_profiles/application/commands/assess_paper_relevance.py` | AC01 |
| https://github.com/z411392/paper-radar/issues/19 | paper_explanations | `src/libs/paper_explanations/application/commands/generate_explanation.py`, `src/libs/paper_explanations/adapters/driven/`, `src/libs/paper_explanations/prompts/`, `src/libs/paper_explanations/resources/` | AC01 |
| https://github.com/z411392/paper-radar/issues/20 | paper_explanations | `src/libs/paper_explanations/domain/`, `src/libs/paper_explanations/application/commands/verify_explanation.py`, `migrations/0005-paper-explanations.sql` | AC02 |

## 接線與交易

Apps 只呼叫 feature inbound port；libs 不讀 apps transport DTO。跨模組交接由 research_workflow 呼叫公開 port，不直接寫另一 owner 的 SQLite 表。共用檔案物件以 ObjectRef 交接，不傳未檢查路徑。所有 outbound I/O 在短交易之外。

本 Story 的 transaction／freshness 以資料模型和對應 Task 的 exact frozen oracle 為準。更動 shared schema 前，由 migration 的唯一 writer 協調，不把整個 migrations 目錄授予多名實作者平行覆寫。

## 正反外部 oracle

- https://github.com/z411392/paper-radar/issues/18：推薦原因不回寫成研究結論；relevance 執行錯誤不算無關。 預定 oracle 位於 `src/libs/paper_explanations/tests/contract/test_t10_grounded_explanations.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/19：僅讀摘要仍可解說，但不能把比喻寫成真實實驗或宣稱已同行評審。 預定 oracle 位於 `src/libs/paper_explanations/tests/contract/test_t11_grounded_explanations.py`；正式 dispatch 前由 Architect 凍結。
- https://github.com/z411392/paper-radar/issues/20：舊 revision 晚回不覆蓋新版；任何已知 unsupported 關鍵結論不得進 digest。 預定 oracle 位於 `src/libs/paper_explanations/tests/contract/test_t12_grounded_explanations.py`；正式 dispatch 前由 Architect 凍結。

## 驗證方法

各 Task body 的每個 S1／S2／S3 都提供 Given／When／Then 與反例。命令是目標接口，尚未執行。測試檔須先建立並證明因缺少目標行為而失敗；不得將不存在的命令寫成 PASS。

Focused unit／integration → contract／acceptance → `make ci-fast` → 獨立 Reviewer exact-candidate review → Commander 保留 Subtask commits 合流。真來源、真模型、受控郵件及復原能力各自另記收據。

## 開工前必填

真實 Task branch、base SHA、candidate SHA、cwd、write owner、frozen paths、provider／live authority 依 Rule15 Fresh Task Pack 補齊。未補齊不宣稱 READY。模型、收件者或後續 HTTP 未定只阻擋直接需要它的操作，不阻擋 hermetic 契約工作。

## 2026-09-23 OpenRouter 接線方向（PO 已選模型，尚未實作）

產品選型唯一引用 [共通 R10／D01](../../docs/delivery/requirements-specification.md#r10)。本節限定該選型的技術影響，不改工程 agents 的 routing authority。#19 的 provider adapter 透過現有 owner LLM port 接上 OpenRouter；application 不知道 HTTP DTO、API key 或供應商錯誤格式。無跨 owner 私有 adapter import。

Gateway base URL 為 `https://openrouter.ai/api/v1`，Chat Completions 路徑為 `/chat/completions`，單一 model 使用已核對的 `google/gemini-3.8-flash`。API key 透過本機 `OPENROUTER_API_KEY` 提供，不寫入SQLite、config範例、log或Issue。初始配置範例見 [config/llm.example.toml](../../config/llm.example.toml)；目前沒有讀取此範例的runtime loader或LLM adapter，不能將檔案存在當成已接通。

Structured output 預定使用 `response_format.type=json_schema`、明示schema與strict，以及 `provider.require_parameters=true`。官方文件提醒支援性按endpoint而異；即使結構化輸出成功，本地schema、anchor、數字及版本適用性驗證仍必須執行。截斷、invalid JSON、拒答或無支援endpoint是失敗，不降為無schema重試以通過驗收。模型語意核對也不代替確定性驗證或独立工程Reviewer。

同模型的provider failover與跨模型fallback是不同維度：本產品只送一個model，不送其他models清單或auto router；不自動改model suffix。使用者未指定AI Studio／Vertex等上游provider，因此本次不假造provider pin。正式呼叫前固定符合privacy、參數與預算的provider routing policy；官方 `provider.allow_fallbacks` 不是本案 `allow_model_fallback` 的直接API映射。記錄可得的實際provider，無欄位時明示unknown。

快取及執行收據遵守R10：generation identity綁gateway、requested model、版本化請求／routing參數、prompt/schema/glossary、evidence及output profile；attempt另記request ID、returned model/provider、token與實際費用。公開model slug不當作immutable weights；需要alias映射時僅接受來源可驗證的映射，不根據猜測串接日期尾碼。價格、上限與effort須以實際採用的服務條件核對，不沿用研究報告舊模型報價。

先以假的HTTP/LLM port覆蓋401/403、429、timeout、quota不足、不支援schema、invalid/truncated JSON、回傳模型不符、提示注入及晚回覆。未啟用source search、tools、PDF處理或response-healing外掛；資料證據只來自已建立的evidence package。Budget、憑證和受控live驗證未齊前，`enabled=false`；此決策沒有發出任何付費推論。

官方參考（2026-09-23核對）：[模型](https://openrouter.ai/google/gemini-3.8-flash)、[Quickstart](https://openrouter.ai/docs/quickstart)、[Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)、[Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection)。詳細研究與判斷留在 [#19 comments](https://github.com/z411392/paper-radar/issues/19#issuecomment-5782008358)。
