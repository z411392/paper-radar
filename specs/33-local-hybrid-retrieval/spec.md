# 以 SQLite 全文與 FAISS 語意搜尋找回論文，索引可重建

Story：https://github.com/z411392/paper-radar/issues/33

## 使用者成果

以文字與向量搜尋找回 current 的論文與解說，模型或索引更新不破壞既有身份。

## Authority 與範圍

共通規則只引用：[R01](../../docs/delivery/requirements-specification.md#r01), [R07](../../docs/delivery/requirements-specification.md#r07), [R11](../../docs/delivery/requirements-specification.md#r11)。

事件流程：[Event Storming f07](../../docs/architecture/event-storming.md#f07)。Roadmap：P3／P3-E2；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：搜尋文件與向量以內容 fingerprint 與 space identity 保存；同輸入不重算。；驗收見 [AC01](#ac1)。
- SC02：新 generation 完成 hash/count/membership 檢查後才由 SQLite CAS 啟用。；驗收見 [AC02](#ac2)。
- SC03：搜尋結果回 SQLite 驗證 current／filter；缺 index 可明示降級至 lexical。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：搜尋文件與向量以內容 fingerprint 與 space identity 保存；同輸入不重算。

反例：不同模型／維度／前處理的向量不得放入同一 index。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：新 generation 完成 hash/count/membership 檢查後才由 SQLite CAS 啟用。

反例：切換中斷、損壞 index 或 mismatched mapping 不可成為 active。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：搜尋結果回 SQLite 驗證 current／filter；缺 index 可明示降級至 lexical。

反例：FAISS top-k 後過濾不足不得假裝完整；近似相似不作自動 identity merge。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/34 — 建立可重建的 FTS 搜尋投影
- https://github.com/z411392/paper-radar/issues/35 — 保存原始向量並建置 FAISS generation
- https://github.com/z411392/paper-radar/issues/36 — 原子啟用索引並查詢混合結果

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
