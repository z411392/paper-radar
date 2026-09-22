# 在本機回查閱讀紀錄並調整關注與回饋

Story：https://github.com/z411392/paper-radar/issues/41

## 使用者成果

不只收信，還能從本機查看來源、摘要版本、已讀／收藏，並安全修改個人關注。

## Authority 與範圍

共通規則只引用：[R02](../../docs/delivery/requirements-specification.md#r02), [R16](../../docs/delivery/requirements-specification.md#r16), [R18](../../docs/delivery/requirements-specification.md#r18)。

事件流程：[Event Storming f09](../../docs/architecture/event-storming.md#f09)。Roadmap：P4／P4-E2；本 Story 服務該成果，不因文件存在就證明 Exit。

本檔是規劃基線，尚未由獨立 Architect 凍結或 Reviewer 驗收。第一個 Task Ready 僅檢查直接必要前提，不以整份 Story 已設計作全域門檻。

## 情境

- SC01：CLI 可依精確 work ID 回查版本、免費入口與已寄紀錄；重開一致。；驗收見 [AC01](#ac1)。
- SC02：回饋只改讀者偏好，不改論文事實、出版狀態或品質標籤。；驗收見 [AC02](#ac2)。
- SC03：後續本機 HTTP／RSS 只在另行固定 transport 與 security contract 後接入。；驗收見 [AC03](#ac3)。

## 專屬驗收

<a id="ac1"></a>
### AC01

Given：依 SC01 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：CLI 可依精確 work ID 回查版本、免費入口與已寄紀錄；重開一致。

反例：無資料、缺失物件、錯誤不可都顯示空清單。


<a id="ac2"></a>
### AC02

Given：依 SC02 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：回饋只改讀者偏好，不改論文事實、出版狀態或品質標籤。

反例：重複回饋具可預期語義，不因 email 預覽的 GET 變更設定。


<a id="ac3"></a>
### AC03

Given：依 SC03 的固定資料與前置版本。

When：執行本情境的一次正式 use case。

Then：後續本機 HTTP／RSS 只在另行固定 transport 與 security contract 後接入。

反例：未核准的公開伺服器、登入系統及雲端部署不納入此 Story。


## 不包含

不修改 Kaledoxa；不把規劃當產品 ACCEPT；不在未明示 commissioning 下抓取實際論文、使用付費模型或寄信。後續 HTTP/RSS 需限定本機 transport 與必要授權；目前不擴為 SaaS。

## 任務與證據導覽

- https://github.com/z411392/paper-radar/issues/42 — 提供 CLI 閱讀歷史與 exact-ID 回查
- https://github.com/z411392/paper-radar/issues/43 — 保存閱讀回饋與重新評估偏好
- https://github.com/z411392/paper-radar/issues/44 — 凍結後續 localhost HTTP 與 RSS 邊界

方案見 [plan](plan.md)；直接測試與獨立驗收證據見 [progress](progress.md)。Status／Priority／Sprint 唯一查 Project。
