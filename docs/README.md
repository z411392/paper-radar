# Paper Radar 文件入口

本庫依使用者 2026-09-22 的要求，採本機檔案系統＋FAISS＋SQLite，並對齊 Kaledoxa 的文件與 agent 治理。先前 PostgreSQL／pgvector 提案已被本機儲存要求取代；不採 `docs/product`、`docs/contexts`、`docs/operations` 的平行文件庫。

| 問題 | 唯一入口 |
|---|---|
| 做什麼、哪些是已知需求或可調預設？ | [共通需求與 AC](delivery/requirements-specification.md) |
| 先交付什麼、什麼算一個產品成果成立？ | [路線圖](delivery/mvp-phases.md) |
| 業務如何發生、各 BC 的詞義與責任？ | [Event Storming](architecture/event-storming.md) |
| 程式 ownership、上下游與公開交接？ | [Context Map](architecture/context-map.md) |
| SQLite、物件與向量如何保持一致？ | [資料模型](data-model.md) |
| 某 Story 的情境、方案和證據？ | [specs 入口](../specs/README.md) |
| 目前工程執行狀態？ | Paper Radar（既有 Project；尚未取得存取與欄位同步證據） |
| GitHub 歷史如何讀？ | [人類可讀歷史導覽](delivery/github-human-readable-history.md) |

Root README 與本頁只導覽；共通產品規則不在此重抄。BC 的閱讀路徑由 Context Map 提供，不為每個 BC 建新文件樹。schema 與 OpenAPI 屬技術契約，不是另一份產品 authority。

建庫基線尚未取得產品 E2E 或獨立 ACCEPT。前導資料中的暫時 S01 等代號不是 GitHub issue number；正式發布後僅使用實際 issue number 對應的 Story 目錄。
