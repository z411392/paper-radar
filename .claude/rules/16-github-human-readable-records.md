# GitHub 人類可讀記錄

文件歸屬依 [Rule80](80-documentation.md)，角色依 [Rule15](15-execution-strategy.md)。用繁體中文說明目的、原因與成果，再保留精確契約，不堆同內容的第二份摘要。

## 本文

常用 Epic/Story/Task/Spike，依需要使用。Story 列成果、Affected BCs、Delivery Owner、適用規格及整合驗收；Task 列成果、依據、範圍、步驟、失敗處理、驗證及停止條件。小 Task 不強制有 Story。

分開已接受規格、目前計畫與未接受提案。GIT_RETAINED 階段引用 Git 的 Story SC/AC，不複製第二份規格。修改本文不等於核可；缺少可驗證接受來源時明寫未接受。指派讀指定 Issue/PR 欄位，Project Status/Priority/Sprint/Views 不抄入本文或 Markdown。

## 留言與證據

研究、探索、原型、失敗、修正和審查追加 comments；局部研究不強制另開 Spike。長期規則接受具體版本後回最近 owner，保留來歷，不整篇變成全局規範。

append-only 是團隊政策，GitHub 留言可修改/刪除，不是不可變事件庫。錯誤用新留言更正；重要核可與必要內容依 Rule80 保存，只有URL/hash或自己填accepted_by不算核可證明。

HUMAN_CHECKPOINT/HUMAN_HISTORY_SUMMARY 可作歷史導航，標日期、版本、真實SHA與證據範圍，不把舊回報當即時狀態。先說具體變更，再列驗證與未做事項；CI不等於獨立驗收。
