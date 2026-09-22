# 預覽並可靠寄出每日精選，重跑不重建通知

Story：https://github.com/z411392/paper-radar/issues/21

## 使用者成果

每天只整理少量符合條件的研究，先固定內容與寄送身份，再受控寄給指定收件人。

## Authority 與範圍

共通規則只引用：[R12](../../docs/delivery/requirements-specification.md#r12), [R13](../../docs/delivery/requirements-specification.md#r13), [R16](../../docs/delivery/requirements-specification.md#r16)。

事件流程：[Event Storming f05](../../docs/architecture/event-storming.md#f05)。Roadmap：P2／P2-E1；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：同一研究命中多領域只呈現一次；不足目標篇數不湊數；零篇不寄空摘要。；驗收見 [AC01](#ac1)。
- SC02：digest、選定版本、ledger 與 outbox 原子保存；同事件重播不新增 business notification。；驗收見 [AC02](#ac2)。
- SC03：明示收件人與啟用設定後才真寄；逾時保留 delivery_unknown。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：同一研究命中多領域只呈現一次；不足目標篇數不湊數；零篇不寄空摘要。

反例：不合格／stale summary 不能進精選；preview 不得發送網路郵件。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：digest、選定版本、ledger 與 outbox 原子保存；同事件重播不新增 business notification。

反例：資料庫中途失敗不得留下有信可寄卻無去重帳本的狀態。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：明示收件人與啟用設定後才真寄；逾時保留 delivery_unknown。

反例：provider accepted 不等於讀者已讀；unknown 不得無條件重送。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/22 — 選取每日精選並渲染可閱讀預覽
- https://github.com/z411392/paper-radar/issues/23 — 持久化寄送 outbox 與通知帳本
- https://github.com/z411392/paper-radar/issues/24 — 接上郵件 adapter 與未知送達調節

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
