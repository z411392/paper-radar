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

## 程式輪廓

```text
src/apps/cli/{__main__.py,entrypoints.py,module.py,adapters/driving,tests}
src/libs/{watch_profiles,discovery,scholarly_catalog,paper_explanations,
          delivery,retrieval,research_workflow,kernel}/
  application/{commands,queries,policies}
  domain/services
  ports
  dtos
  adapters/driven
  exceptions
  tests/{unit,integration,contract,fixtures}
```

只建立已用到的目錄，無 `__init__.py`。CLI 的 run-worker 是長駐入口：clock lifecycle 在 app，due jobs／lease／補抓政策在 workflow。未設獨立 apps/worker。未來有真實 HTTP contract 才加入 apps/http；不為展示目錄先建立假 endpoint。

Runtime objects／SQLite／vectors／index 與備份目錄唯一契約見 [data-model](../data-model.md)。技術資料模型和 migrations 有各 owner；不能用共用 DB 繞過邊界。

## Agents 與工程 ownership

角色拓撲唯一引用 [Rule15](../../.claude/rules/15-execution-strategy.md#execution-topology-and-dispatch)；本檔不複製模型矩陣。BC-local implementer 依 path ownership 組織；shared Architect／Reviewer 不因 BC 數量複製。並行時 migrations、composition、shared docs 必須指定唯一 writer 或序列合流。

## 與前一版輪廓差異

使用者的治理裁決取代先前 docs/product、docs/contexts、docs/operations 的方案。產品語言在 Event Storming；公開交接與 source navigation 在本檔；方案在 Story plan；操作規則在 Rule90／有界 Task。不另外保留平行總計畫。

## Task #6 已接線的工程入口

`apps/cli version` → `research_workflow.ports.ReadRuntimeVersionPort` → `ReadRuntimeVersion` → `RuntimeVersionProviderPort` → `PythonRuntimeVersionAdapter`。DTO 位於該 owner 的 dtos；只有 apps/cli/module.py 引用具體用例與 adapter。此查詢只用於驗證套件安裝／接線，不讀 workspace，也不是產品健康檢查。工作區初始化、排程與其他命令依其 Task 後續接線。
