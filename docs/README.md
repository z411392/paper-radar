# Paper Radar 文件入口

2026-09-30 產品邊界以 [需求](delivery/requirements-specification.md) 與 [路線圖](delivery/mvp-phases.md) 為準：Paper Radar 是 GitHub Actions 排程的新論文翻譯 Email bot。舊文件若描述 FAISS、閱讀後台、backup/restore、修訂通知或本機 daemon，僅視為歷史設計，不是 active backlog。

| 要找什麼 | 正式入口 |
|---|---|
| 產品與共同要求 | [需求](delivery/requirements-specification.md) |
| 穩定交付成果 | [路線圖](delivery/mvp-phases.md) |
| 全局流程與既有模型 | [Event Storming](architecture/event-storming.md)（含歷史模型；scope 以需求/路線圖優先） |
| BC 合作、上下游與依賴 | [Context Map](architecture/context-map.md) |
| 文件、核可與規格切換 | [Rule80](../.claude/rules/80-documentation.md) |
| 儲存一致性 | [資料模型](data-model.md)，精確 schema 見 migrations/ |
| Story 的適用 Git 規格 | [specs](../specs/README.md) |
| 程式、測試、規格查找 | [.context](../.context/README.md) |
| 版本保存能力邊界 | [.authority](../.authority/README.md) |

新證據在 Issue/PR comments，進度及指派讀指定欄位。舊 progress 留歷史、不再重抄；既有 spec 不因治理調整而刪除。本頁只是導航。
