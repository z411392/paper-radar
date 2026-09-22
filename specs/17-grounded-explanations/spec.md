# 產生能回查原文的繁體中文白話解說

Story：https://github.com/z411392/paper-radar/issues/17

## 使用者成果

把研究的問題、方法、結果與未知說清楚，讀者不必先懂論文術語，也不被模型多加的結果誤導。

## Authority 與範圍

共通規則只引用：[R07](../../docs/delivery/requirements-specification.md#r07), [R08](../../docs/delivery/requirements-specification.md#r08), [R09](../../docs/delivery/requirements-specification.md#r09), [R10](../../docs/delivery/requirements-specification.md#r10)。

事件流程：[Event Storming f04](../../docs/architecture/event-storming.md#f04)。Roadmap：P1／P1-E4；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：原始摘要、忠實翻譯、白話解說分開；每個關鍵 claim 都綁正確證據。；驗收見 [AC01](#ac1)。
- SC02：驗證數字與方法、baseline、指標、資料集、單位的對應，並檢查因果／否定。；驗收見 [AC02](#ac2)。
- SC03：unchanged input 命中生成快取；late response 不成為 current；無模型額度保留可重試。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：原始摘要、忠實翻譯、白話解說分開；每個關鍵 claim 都綁正確證據。

反例：摘要未說外部驗證不能被改寫為作者沒有做外部驗證。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：驗證數字與方法、baseline、指標、資料集、單位的對應，並檢查因果／否定。

反例：數字雖都出現但新舊方法對調時必須拒絕；不能靠第二模型補證據。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：unchanged input 命中生成快取；late response 不成為 current；無模型額度保留可重試。

反例：prompt 或 snapshot 變更時不能回用舊 current；failure 不當成 irrelevant。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/18 — 抽取研究主張並完成相關性評估契約
- https://github.com/z411392/paper-radar/issues/19 — 生成繁體中文翻譯與白話閱讀卡
- https://github.com/z411392/paper-radar/issues/20 — 驗證逐句證據與 current-input 發布

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
