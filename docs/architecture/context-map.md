# Context Map 與實作 ownership

## 邊界

業務 BC 為 watch_profiles、discovery、scholarly_catalog、paper_explanations、delivery。retrieval 是檢索技術 owner；research_workflow 為協調／工程交付線；kernel 僅限共用 bytes、clock、connection／transaction 原語。論文學科是資料，不是 BC。

| owner | 擁有的資料／行為 | 不擁有 |
|---|---|---|
| watch_profiles | Domain、Profile revisions、relevance assessment | 公共研究的科學真偽 |
| discovery | source bindings、harvest units／attempts、raw observations | canonical Work／Summary |
| scholarly_catalog | Work／Manifestation／Revision、access、evidence／anchors、typed events | 寄送及讀者偏好 |
| paper_explanations | claims、Summary revisions、QA、模型使用與 current guards | 來源搜索、工作流排程 |
| delivery | digest、outbox、ledger、subscription、feedback | Canonical identity、模型 verdict |
| retrieval | search projection、embedding space／vectors、FAISS generations | 研究身份合併、是否科學正確 |
| research_workflow | durable jobs／leases、跨 owner 用例協調、backup barrier | 任意寫其他 owner 私有資料表 |
| kernel | ObjectRef、ObjectStore、Clock、SQLite connection／UnitOfWork | Paper／Digest／Profile 專屬規則 |

## Supplier／consumer 公開契約

| supplier → consumer | 交接 | 失敗界線 |
|---|---|---|
| watch_profiles → discovery | published ProfileSnapshot → compiled source query | 無有效發布版本不猜範圍 |
| discovery → scholarly_catalog | SourceObservationRef 與 SourceRecord DTO | raw 保存成功後才承接，parser failure 可重播 |
| scholarly_catalog → paper_explanations | EvidenceSnapshotRef／RevisionIdentity | 缺失／權限未知先停止受影響篇 |
| scholarly_catalog → retrieval | current SearchDocumentInput 與 revision fingerprint | 索引失效不修改書目 |
| paper_explanations → retrieval | current verified SummaryProjection | 不合格 summary 不成為目前正文 |
| scholarly_catalog／paper_explanations → delivery | EligiblePaper／VerifiedExplanation DTO | 寄前重新判 eligibility |
| retrieval → watch_profiles | RankedCandidate DTO，僅提供相關性線索 | 缺 index 明示 degraded，不補假向量 |
| workflow → 各 owner inbound ports | scan／process／digest／restore commands | step 成功不等於全流程成功 |

## Import DAG

所有 feature 可依賴 kernel。除此以外允許的有向依賴：

```text
research_workflow → delivery, discovery, watch_profiles,
                    scholarly_catalog, paper_explanations, retrieval
 delivery → scholarly_catalog, paper_explanations, watch_profiles
 watch_profiles → retrieval
 retrieval → scholarly_catalog, paper_explanations
 paper_explanations → scholarly_catalog
 scholarly_catalog → discovery.dtos（純交接型別，不回呼其 application）
 discovery → kernel
```

為維持 DAG，discovery 不 import watch_profiles：workflow 取得 ProfileSnapshot 後轉成 discovery 自己的 query input。paper_explanations 不 import watch_profiles，相關性判斷由 watch_profiles 自己的用例擁有。型別引用只走 supplier `ports`／`dtos`，不能跨 lib import domain／application／adapter。受許可的交接不代表全模組任意 import。

Root composition 在 apps/cli/module.py 注入 adapters。學術來源只用明示 source adapters；不借用 Kaledoxa browser session／私有資料。

## 導航與供應方規格

程式/測試路徑由 [.context](../../.context/README.md) 維護，不在本圖保存目前 Task/PR 或每個函式。來源合作邊界見 [discovery](../domains/discovery.md)；其他 BC 沿用既有模型/儲存/正式介面，不按每個 lib 造 BC 文件。

## Agents 與工程 ownership

角色拓撲唯一引用 [Rule15](../../.claude/rules/15-execution-strategy.md#execution-topology-and-dispatch)；本檔不複製模型矩陣。BC-local implementer 依 path ownership 組織；shared Architect／Reviewer 不因 BC 數量複製。並行時 migrations、composition、shared docs 必須指定唯一 writer 或序列合流。

## 治理採用

2026-09-23 依 Rule80 分階段採用。本圖保留模型/上下游/import邊界，程式導航與候選歷史不再追加。原 Task #6/#8/#10 的方案仍可從原 Git 版本、相應 Story plan 與 Issues 取回；不由舊圖上文字推論現況，未驗收內容不因搬移而升格。
