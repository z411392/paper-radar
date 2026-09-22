# 把重要修訂與更正通知給曾經收到研究的人

Story：https://github.com/z411392/paper-radar/issues/37

## 使用者成果

把新研究、修訂、出版狀態、更正／撤稿分開，既不重複打擾，也不漏掉已寄論文的重要更正。

## Authority 與範圍

共通規則只引用：[R04](../../docs/delivery/requirements-specification.md#r04), [R05](../../docs/delivery/requirements-specification.md#r05), [R10](../../docs/delivery/requirements-specification.md#r10), [R12](../../docs/delivery/requirements-specification.md#r12), [R13](../../docs/delivery/requirements-specification.md#r13)。

事件流程：[Event Storming f08](../../docs/architecture/event-storming.md#f08)。Roadmap：P4／P4-E1；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：source metadata 變化按 typed event 分類；正文無變化不重生成。；驗收見 [AC01](#ac1)。
- SC02：更正／撤稿只通知符合策略的 prior recipients，保留證據。；驗收見 [AC02](#ac2)。
- SC03：寄送前 current-input 與事件資格重新核對；未寄 digest 可取消／重建。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：source metadata 變化按 typed event 分類；正文無變化不重生成。

反例：citation count 改變不能當新研究，模型升級不能當新論文事件。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：更正／撤稿只通知符合策略的 prior recipients，保留證據。

反例：找不到免費全文不能把可靠更正通知擋掉；不能把撤稿標成研究已證實錯誤的額外結論。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：寄送前 current-input 與事件資格重新核對；未寄 digest 可取消／重建。

反例：已寄內容不可靜默修改；兩份摘要不得混用不同 revision 的數值與引文。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/38 — 建立 typed revision events 與精準失效
- https://github.com/z411392/paper-radar/issues/39 — 查詢 prior recipients 與版本通知策略
- https://github.com/z411392/paper-radar/issues/40 — 接通更正流程及寄前重新核對

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
