# 看見來源健康、解說品質與成本，完成有證據的整合驗收

Story：https://github.com/z411392/paper-radar/issues/49

## 使用者成果

用可重播的測試與真實能力收據回答哪些環節可靠、哪些仍有缺口，不以全綠單元測試當作整個產品完成。

## Authority 與範圍

共通規則只引用：[R03](../../docs/delivery/requirements-specification.md#r03), [R09](../../docs/delivery/requirements-specification.md#r09), [R10](../../docs/delivery/requirements-specification.md#r10), [R16](../../docs/delivery/requirements-specification.md#r16), [R17](../../docs/delivery/requirements-specification.md#r17)。

事件流程：[Event Storming f11](../../docs/architecture/event-storming.md#f11)。Roadmap：P5／P5-E1；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：健康查詢顯示每來源成功時間、gap、pending age、QA reject、unknown delivery 與模型成本。；驗收見 [AC01](#ac1)。
- SC02：模型呼叫具預算保留與實際用量對帳；外部內容不能改設定、讀 secrets 或指定新收件人。；驗收見 [AC02](#ac2)。
- SC03：固定回溯窗跨來源重播、故障注入與真實能力逐項分帳，獨立 Reviewer 讀 exact SHA。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：健康查詢顯示每來源成功時間、gap、pending age、QA reject、unknown delivery 與模型成本。

反例：沒有新論文與來源失敗須分清；metrics 未接不得回傳固定零值。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：模型呼叫具預算保留與實際用量對帳；外部內容不能改設定、讀 secrets 或指定新收件人。

反例：budget 耗盡不 silent fallback；prompt injection 不取得工具權限。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：固定回溯窗跨來源重播、故障注入與真實能力逐項分帳，獨立 Reviewer 讀 exact SHA。

反例：未執行 live、尚未審查或只有 schema 測試時，產品 Exit 不得寫 PROVEN。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/50 — 建立真實健康查詢及費用核對
- https://github.com/z411392/paper-radar/issues/51 — 驗證非可信內容與本機資料安全邊界
- https://github.com/z411392/paper-radar/issues/52 — 完成可重播整合與獨立驗收收據

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
