# Event Storming：共同語言與業務流程

## 探索範圍

這是依使用者敘述與研究報告整理的 Big Picture／Process Model 草案，尚非已舉行完整共同走查的證據。資料流圖不是完成驗收；未定預設見共通 requirements。學科是 DomainDefinition 資料，BC 是業務責任，不以 apps/lib 名字硬切。

## Big Picture

```text
發布關注 → 建立 due 收集 → 保存來源 → 辨識研究與版本
→ 核對可用證據 → 判斷相關性 → 生成與驗證白話卡
→ 固定每日精選 → 預留通知 → 受控寄出 → 閱讀回饋
來源更正 → 更新研究狀態 → 找到先前收件人 → 更正通知
本機中斷 → 保留 lease／checkpoint → 恢復並有界補抓
備份／restore → 驗證引用物件 → 關閉外部副作用 → 受控重新啟用
```

## 共同語言與 Aggregate

Work 為一項研究；Manifestation 為預印本／出版等呈現；Revision 為該呈現的內容版本；Observation 為來源當時回應；EvidenceSnapshot 為實際讀取內容；SummaryRevision 是基於精確證據的生成物，不是新的研究。

WatchProfile 定義讀者關注；RelevanceAssessment 是該設定與內容的適用性，不等於科學品質。HarvestUnit 是邏輯收集工作，Attempt 是執行次數。WorkflowJob 是跨步驟待辦，與某來源的分頁 checkpoint 不同。

EmbeddingSpace 定義可比較向量；IndexGeneration 是可重建投影，不是論文資料庫。Digest 是固定閱讀集合；Outbox 是持久寄送請求；NotificationLedger 記業務事件是否已預留／處理，不宣稱 exactly-once SMTP。

## BC 定義

watch_profiles 擁有偏好與相關性；discovery 擁有來源採集；scholarly_catalog 擁有研究身份與證據；paper_explanations 擁有生成與驗證；delivery 擁有閱讀推送。技術交付線與 import ownership 見 [Context Map](context-map.md)。

## Process Model：八元素與例外

<a id="f01"></a>
### F01 — 關注範圍發布

| 元素 | 本流程 |
|---|---|
| Actor | 讀者 |
| UI／觸發 read model | CLI 設定／published profile |
| Command | PublishWatchProfile |
| Aggregate／system owner | WatchProfile |
| Domain Event | WatchProfilePublished |
| Policy | 有效來源與版本完整才發布 |
| External service | source capability registry |
| Read Model | PublishedProfile |

成功終點：WatchProfilePublished 並可從 PublishedProfile 回查。零結果／拒絕／失敗／重複／中斷分支：無效拒絕不半寫；重播相同 fingerprint 冪等；停用保留歷史。

共通產品規則：[R02](../delivery/requirements-specification.md#r02)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f02"></a>
### F02 — 來源收集與補抓

| 元素 | 本流程 |
|---|---|
| Actor | 時鐘／讀者手動 |
| UI／觸發 read model | due source binding／coverage gap |
| Command | CollectSourceWindow |
| Aggregate／system owner | HarvestUnit |
| Domain Event | SourceObservationStored |
| Policy | durable 後才移動 checkpoint；429 有界退避 |
| External service | arXiv／PubMed／Crossref |
| Read Model | CoverageReadModel |

成功終點：SourceObservationStored 並可從 CoverageReadModel 回查。零結果／拒絕／失敗／重複／中斷分支：空結果須成功解析；失敗保留 gap；不同 binding 不互相掩蓋。

共通產品規則：[R06](../delivery/requirements-specification.md#r06)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f03"></a>
### F03 — 研究身份與證據

| 元素 | 本流程 |
|---|---|
| Actor | workflow |
| UI／觸發 read model | saved source observation |
| Command | RegisterSourceObservation／PrepareEvidence |
| Aggregate／system owner | Work／Manifestation／Revision |
| Domain Event | RevisionRecorded／EvidencePrepared |
| Policy | 確定 identity 優先，權限與內容核對 |
| External service | official repository／OA resolver／parser |
| Read Model | PaperDetail |

成功終點：RevisionRecorded／EvidencePrepared 並可從 PaperDetail 回查。零結果／拒絕／失敗／重複／中斷分支：相似不合併；unknown 不取得；parser 部分失敗明示 coverage。

共通產品規則：[R05](../delivery/requirements-specification.md#r05)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f04"></a>
### F04 — 相關性與白話解說

| 元素 | 本流程 |
|---|---|
| Actor | workflow |
| UI／觸發 read model | ProfileSnapshot＋EvidenceSnapshot |
| Command | AssessRelevance／GenerateExplanation |
| Aggregate／system owner | RelevanceAssessment／SummaryRevision |
| Domain Event | ExplanationVerified |
| Policy | 證據支持且 input current 才發布 |
| External service | embedding／LLM provider |
| Read Model | ReadingCard |

成功終點：ExplanationVerified 並可從 ReadingCard 回查。零結果／拒絕／失敗／重複／中斷分支：失敗不等於 irrelevant；數值對調拒絕；late response 歷史保留不發布。

共通產品規則：[R08](../delivery/requirements-specification.md#r08)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f05"></a>
### F05 — 每日精選與寄送

| 元素 | 本流程 |
|---|---|
| Actor | 時鐘／讀者 |
| UI／觸發 read model | eligible reading cards／subscription |
| Command | PrepareDigest／DispatchDigest |
| Aggregate／system owner | Digest／Outbox |
| Domain Event | DeliveryRequested／ProviderAccepted |
| Policy | ledger 原子預留、寄前重新核對 |
| External service | SMTP／email API |
| Read Model | DigestHistory |

成功終點：DeliveryRequested／ProviderAccepted 並可從 DigestHistory 回查。零結果／拒絕／失敗／重複／中斷分支：取消不寄；timeout unknown；不把 provider接受當讀者閱讀。

共通產品規則：[R12](../delivery/requirements-specification.md#r12)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f06"></a>
### F06 — 工作重啟與租期

| 元素 | 本流程 |
|---|---|
| Actor | 本機程序生命週期 |
| UI／觸發 read model | due jobs／expired leases |
| Command | ProcessPendingJobs |
| Aggregate／system owner | WorkflowJob |
| Domain Event | JobClaimed／JobCompleted |
| Policy | 短交易取 fencing token、舊 owner 拒寫 |
| External service | 本機 clock／程序 |
| Read Model | JobHealth |

成功終點：JobClaimed／JobCompleted 並可從 JobHealth 回查。零結果／拒絕／失敗／重複／中斷分支：sleep 後有界補抓；外部來源不支援歷史時明示 gap。

共通產品規則：[R14](../delivery/requirements-specification.md#r14)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f07"></a>
### F07 — 本機詞彙及語意搜尋

| 元素 | 本流程 |
|---|---|
| Actor | 讀者／相關性用例 |
| UI／觸發 read model | query＋filters |
| Command | SearchPapers |
| Aggregate／system owner | SearchDocument／IndexGeneration |
| Domain Event | IndexGenerationActivated |
| Policy | 只有同 space 的 verified generation 可 CAS active |
| External service | 本機 embedding model／FAISS |
| Read Model | RankedPapers |

成功終點：IndexGenerationActivated 並可從 RankedPapers 回查。零結果／拒絕／失敗／重複／中斷分支：候選回 SQLite 過濾；損毀降級明示；原始向量可供重建。

共通產品規則：[R11](../delivery/requirements-specification.md#r11)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f08"></a>
### F08 — 重要更正或版本通知

| 元素 | 本流程 |
|---|---|
| Actor | 來源更新／workflow |
| UI／觸發 read model | typed research event＋prior recipients |
| Command | ProcessRevisionNotice |
| Aggregate／system owner | ResearchEvent／StatusNotice |
| Domain Event | StatusNoticePrepared |
| Policy | 更正與初次通知 identity 分開 |
| External service | 來源更正 metadata |
| Read Model | PaperStatusHistory |

成功終點：StatusNoticePrepared 並可從 PaperStatusHistory 回查。零結果／拒絕／失敗／重複／中斷分支：citation update 不重寄；可靠更正不受普通免費全文 gate 隱藏。

共通產品規則：[R13](../delivery/requirements-specification.md#r13)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f09"></a>
### F09 — 閱讀回饋與偏好

| 元素 | 本流程 |
|---|---|
| Actor | 讀者 |
| UI／觸發 read model | exact paper readback |
| Command | RecordFeedback |
| Aggregate／system owner | ReaderPaperState |
| Domain Event | ReaderFeedbackRecorded |
| Policy | 只改偏好，不改研究事實 |
| External service | 本機 CLI／後續 localhost |
| Read Model | SavedAndReadPapers |

成功終點：ReaderFeedbackRecorded 並可從 SavedAndReadPapers 回查。零結果／拒絕／失敗／重複／中斷分支：GET 不改狀態；error 不假裝 empty；未接 HTTP 不畫假入口。

共通產品規則：[R18](../delivery/requirements-specification.md#r18)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f10"></a>
### F10 — 一致備份與復原

| 元素 | 本流程 |
|---|---|
| Actor | 讀者／受控操作 |
| UI／觸發 read model | workspace snapshot request |
| Command | BackupWorkspace／RestoreWorkspace |
| Aggregate／system owner | BackupManifest／WorkspaceEpoch |
| Domain Event | BackupVerified／WorkspaceRestored |
| Policy | 引用閉包＋hash；restore external_effects=false |
| External service | 本機檔案系統／SQLite backup API |
| Read Model | RestoreIntegrity |

成功終點：BackupVerified／WorkspaceRestored 並可從 RestoreIntegrity 回查。零結果／拒絕／失敗／重複／中斷分支：拒絕非空目標；缺 object 失敗；較舊 ledger 需核對後才恢復寄送。

共通產品規則：[R15](../delivery/requirements-specification.md#r15)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


<a id="f11"></a>
### F11 — 健康與品質驗證

| 元素 | 本流程 |
|---|---|
| Actor | 讀者／reviewer |
| UI／觸發 read model | 真實執行證據 |
| Command | InspectHealth／RunAcceptance |
| Aggregate／system owner | ExecutionEvidence |
| Domain Event | EvidenceRecorded |
| Policy | hermetic／live／independent 分帳 |
| External service | 核准來源／模型／郵件 provider |
| Read Model | HealthReadModel |

成功終點：EvidenceRecorded 並可從 HealthReadModel 回查。零結果／拒絕／失敗／重複／中斷分支：未執行不填成功；未知不填零；結果不得反向發明產品規則。

共通產品規則：[R17](../delivery/requirements-specification.md#r17)。個別 Story SC／AC 由 specs 擁有，不在此重複定義。


## Hot spots

模型／寄送服務、收件人、預算與後續本機 HTTP 的待決範圍，只引用 requirements 對應條款；不在此另建 decision log。可調預設不等於使用者已逐項同意。對直接需要該決策的 Task 記 blocker，不把它升成全案不可開工。
