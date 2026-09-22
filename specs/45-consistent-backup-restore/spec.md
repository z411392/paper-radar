# 備份與恢復本機研究資料，復原後不誤寄歷史內容

Story：https://github.com/z411392/paper-radar/issues/45

## 使用者成果

取得包含 SQLite 與其引用物件的一致快照，搬到乾淨工作區後可查證資料，而且外部副作用預設關閉。

## Authority 與範圍

共通規則只引用：[R01](../../docs/delivery/requirements-specification.md#r01), [R14](../../docs/delivery/requirements-specification.md#r14), [R15](../../docs/delivery/requirements-specification.md#r15), [R16](../../docs/delivery/requirements-specification.md#r16)。

事件流程：[Event Storming f10](../../docs/architecture/event-storming.md#f10)。Roadmap：P2／P2-E3；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：備份使用一致 DB snapshot 並保存其引用物件清單、hash 與完整性結果。；驗收見 [AC01](#ac1)。
- SC02：恢復到新路徑後引用可解析，原工作區不被覆蓋；損毀物件被拒絕。；驗收見 [AC02](#ac2)。
- SC03：restore 增加 workspace epoch 且外部副作用關閉；舊 outbox 不可自行寄出。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：備份使用一致 DB snapshot 並保存其引用物件清單、hash 與完整性結果。

反例：直接複製活躍 SQLite 主檔或只備份 FAISS 不算完整備份。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：恢復到新路徑後引用可解析，原工作區不被覆蓋；損毀物件被拒絕。

反例：遺失原始向量不能假裝只需無成本重建 index；缺物件不能以空資料代替。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：restore 增加 workspace epoch 且外部副作用關閉；舊 outbox 不可自行寄出。

反例：較舊備份可能缺之後寄送紀錄，未核對 provider／操作證據前不得宣稱不重寄。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/46 — 製作具引用閉包的一致備份
- https://github.com/z411392/paper-radar/issues/47 — 在乾淨目錄恢復並檢查物件
- https://github.com/z411392/paper-radar/issues/48 — 隔離復原後的外部副作用

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
