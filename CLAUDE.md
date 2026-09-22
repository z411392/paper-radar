# Paper Radar — coding agent 共用入口

AGENTS.md、GEMINI.md 仍連至本檔，.agents/rules 仍連至 .claude/rules；工具不得假設會自動讀另一工具的規則。

先確認共同安全/操作 [Rule90](.claude/rules/90-operations.md) 與角色授權 [Rule15](.claude/rules/15-execution-strategy.md)。2026-09-23 文件歸屬及查找方式依 [Rule80](.claude/rules/80-documentation.md) 分階段採用附件方法，不撤除獨立 Reviewer 或擴大 live 權限。

接著確認 branch/commit 與 Task 本文，按需要讀 Parent Story 的適用 Git 規格、Affected BCs 的正式契約、目標程式、使用方與測試。從 [.context](.context/README.md) 查位置，失效就回正式來源。全部規則見 [索引](.claude/rules/00-index.md)，不是每個 Task 都要重讀全庫的命令。

GIT_RETAINED 階段正式規格留 Git；Task 本文就是施工資訊，不平行維護相同 Task Pack。原 specs 歷史保留，新研究/失敗/修正寫 Issues comments，不往 progress.md 重抄。接受狀態、目前計畫、提案、實作與驗收分開。

本對話暫行實作仍適用；未有真正獨立驗收不合流 main。單一 Git writer只更新精確 scope，保留 dirty work與 Subtask commits。不得使用 Kaledoxa 私有資料/session，不執行未授權真來源、付費、寄信、部署或歷史刪除。
