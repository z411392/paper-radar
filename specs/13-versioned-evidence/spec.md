# 把來源記錄整理成研究身份、版本與可引用證據

Story：https://github.com/z411392/paper-radar/issues/13

## 使用者成果

讀者看到一項研究，系統能分辨預印本、正式出版版和實際用來改寫的精確內容。

## Authority 與範圍

共通規則只引用：[R03](../../docs/delivery/requirements-specification.md#r03), [R04](../../docs/delivery/requirements-specification.md#r04), [R05](../../docs/delivery/requirements-specification.md#r05), [R09](../../docs/delivery/requirements-specification.md#r09)。

事件流程：[Event Storming f03](../../docs/architecture/event-storming.md#f03)。Roadmap：P1／P1-E3；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：DOI 正規化與同一 arXiv base ID 不重建研究身份；新 revision 可回查。；驗收見 [AC01](#ac1)。
- SC02：免費入口與自動取得／模型處理權限分別保存來源和時間。；驗收見 [AC02](#ac2)。
- SC03：evidence snapshot 帶 parser、hash、覆蓋範圍及精確 quote anchor。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：DOI 正規化與同一 arXiv base ID 不重建研究身份；新 revision 可回查。

反例：標題相似的不同研究不自動合併，更正關係不等於同一內容。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：免費入口與自動取得／模型處理權限分別保存來源和時間。

反例：unknown 不算許可；只有免費摘要不能被標成免費全文。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：evidence snapshot 帶 parser、hash、覆蓋範圍及精確 quote anchor。

反例：跨 snapshot 的 quote 不能合法綁到同一 claim；只讀摘要不能宣稱完整全文。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/14 — 建立保守的研究身份與版本歸屬
- https://github.com/z411392/paper-radar/issues/15 — 查證免費閱讀入口與取得資格
- https://github.com/z411392/paper-radar/issues/16 — 生成帶版本與字元錨點的證據快照

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
