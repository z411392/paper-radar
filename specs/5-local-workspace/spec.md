# 初始化本機工作區並發布可增減的關注範圍

Story：https://github.com/z411392/paper-radar/issues/5

## 使用者成果

在乾淨本機建立持久工作區，匯入五領域種子，發布一份可版本化的關注設定。

## Authority 與範圍

共通規則只引用：[R01](../../docs/delivery/requirements-specification.md#r01), [R02](../../docs/delivery/requirements-specification.md#r02), [R16](../../docs/delivery/requirements-specification.md#r16)。

事件流程：[Event Storming f01](../../docs/architecture/event-storming.md#f01)。Roadmap：P1／P1-E1；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：重開同一 workspace 後，關注版本與 workspace identity 不變；匯入相同內容不產生新修訂。；驗收見 [AC01](#ac1)。
- SC02：停用一個領域只影響未來選取，不刪已讀／已寄歷史；種子不是第二套設定權威。；驗收見 [AC02](#ac2)。
- SC03：物件發布與資料列失敗可恢復；檔案缺失會被偵測。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：重開同一 workspace 後，關注版本與 workspace identity 不變；匯入相同內容不產生新修訂。

反例：兩個不同設定不得被當成同一版本；無效來源設定不得部分提交。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：停用一個領域只影響未來選取，不刪已讀／已寄歷史；種子不是第二套設定權威。

反例：重新啟用不清除 notification ledger，不能以 YAML 覆蓋新 SQLite 設定。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：物件發布與資料列失敗可恢復；檔案缺失會被偵測。

反例：路徑逃逸、損毀 hash 與 SQLite 交易中斷不能被回報為成功。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/6 — 建立可驗證的本機專案與工程防線
- https://github.com/z411392/paper-radar/issues/7 — 持久化工作區與不可變物件
- https://github.com/z411392/paper-radar/issues/8 — 發布領域與關注設定修訂

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
