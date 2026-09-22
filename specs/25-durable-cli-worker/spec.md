# 讓本機自動執行並在睡眠或中斷後有界續跑

Story：https://github.com/z411392/paper-radar/issues/25

## 使用者成果

由同一 CLI 啟動長駐排程；關機期間保留缺口，恢復後不灌入大量歷史郵件。

## Authority 與範圍

共通規則只引用：[R06](../../docs/delivery/requirements-specification.md#r06), [R12](../../docs/delivery/requirements-specification.md#r12), [R14](../../docs/delivery/requirements-specification.md#r14)。

事件流程：[Event Storming f06](../../docs/architecture/event-storming.md#f06)。Roadmap：P2／P2-E2；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：CLI run-worker 呼叫 libs 用例，排程與待辦執行分開；只有一個有效工作 owner。；驗收見 [AC01](#ac1)。
- SC02：不同 binding 的 due windows 保留；每 tick 有上限且可續跑。；驗收見 [AC02](#ac2)。
- SC03：停機後摘要依固定 catch-up policy 合併處理；停止程序可安全重啟。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：CLI run-worker 呼叫 libs 用例，排程與待辦執行分開；只有一個有效工作 owner。

反例：第二個程序或 lease 已失效的舊程序不得覆蓋新結果。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：不同 binding 的 due windows 保留；每 tick 有上限且可續跑。

反例：補抓不能把來源不支援的歷史宣稱完整，也不能為趕進度跳過失敗時間片。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：停機後摘要依固定 catch-up policy 合併處理；停止程序可安全重啟。

反例：睡眠期間不能聲稱準時執行；恢復不能逐日補寄已過時 digest。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/26 — 建立可恢復的工作租期與 fencing
- https://github.com/z411392/paper-radar/issues/27 — 規劃來源補抓與每日摘要工作
- https://github.com/z411392/paper-radar/issues/28 — 接上 CLI 長駐入口與本機啟停

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
