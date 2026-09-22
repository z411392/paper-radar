# 補齊羽球與跨出版社來源，分清每個領域的實際覆蓋

Story：https://github.com/z411392/paper-radar/issues/29

## 使用者成果

在 arXiv 之外接入 PubMed／PMC、Crossref，讓五領域都有可查證的來源策略，而不是假設來源一樣齊全。

## Authority 與範圍

共通規則只引用：[R02](../../docs/delivery/requirements-specification.md#r02), [R03](../../docs/delivery/requirements-specification.md#r03), [R06](../../docs/delivery/requirements-specification.md#r06), [R07](../../docs/delivery/requirements-specification.md#r07)。

事件流程：[Event Storming f02](../../docs/architecture/event-storming.md#f02)。Roadmap：P3／P3-E1；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：PubMed 書目與 PMC 全文能力分開記錄，按版本和用途取得允許資料。；驗收見 [AC01](#ac1)。
- SC02：Crossref 使用明確 bounded query，cursor 過期可重跑片段。；驗收見 [AC02](#ac2)。
- SC03：羽球直接證據與相鄰運動分開，五領域按來源窗口各自評估。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：PubMed 書目與 PMC 全文能力分開記錄，按版本和用途取得允許資料。

反例：在 PubMed 查到不代表 PMC 有全文；PMC 有全文不代表所有用途都允許。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：Crossref 使用明確 bounded query，cursor 過期可重跑片段。

反例：出版日期和被新索引日期不可混用；一領域不得無界掃全站。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：羽球直接證據與相鄰運動分開，五領域按來源窗口各自評估。

反例：網球／泛運動研究不得只因生物力學關鍵詞混進羽球主要精選。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/30 — 接入 PubMed 與合規 PMC 全文
- https://github.com/z411392/paper-radar/issues/31 — 接入 Crossref 的增量與 DOI 關係補充
- https://github.com/z411392/paper-radar/issues/32 — 校準五領域關注與覆蓋判讀

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
