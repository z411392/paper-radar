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
