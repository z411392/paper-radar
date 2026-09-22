# 增量取得 arXiv 論文並在失敗後從正確位置續跑

Story：https://github.com/z411392/paper-radar/issues/9

## 使用者成果

把來源查詢、原始觀測、分頁與來源缺口保存下來，使重跑不造成重複收錄或跳過未完成頁面。

## Authority 與範圍

共通規則只引用：[R03](../../docs/delivery/requirements-specification.md#r03), [R04](../../docs/delivery/requirements-specification.md#r04), [R06](../../docs/delivery/requirements-specification.md#r06)。

事件流程：[Event Storming f02](../../docs/architecture/event-storming.md#f02)。Roadmap：P1／P1-E2；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：固定可枚舉的來源切片經分頁與重播後，每個來源 identity 恰有可追溯觀測；進度只在保存後更新。；驗收見 [AC01](#ac1)。
- SC02：429、timeout、schema error、cursor 過期分開處理；不同 binding 的缺口獨立。；驗收見 [AC02](#ac2)。
- SC03：只有成功且格式有效的空頁才是 verified-empty；原始證據可重播。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：固定可枚舉的來源切片經分頁與重播後，每個來源 identity 恰有可追溯觀測；進度只在保存後更新。

反例：在保存前中斷不能讓 checkpoint 前移。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：429、timeout、schema error、cursor 過期分開處理；不同 binding 的缺口獨立。

反例：一個成功 binding 不得使另一個失敗 binding 被標為完整。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：只有成功且格式有效的空頁才是 verified-empty；原始證據可重播。

反例：HTTP 錯誤或解析失敗不得轉成零結果，也不得補造論文。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/10 — 建立來源契約與可重現查詢編譯
- https://github.com/z411392/paper-radar/issues/11 — 實作 arXiv 收集與共用節流
- https://github.com/z411392/paper-radar/issues/12 — 保存觀測並恢復未完成採集

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
