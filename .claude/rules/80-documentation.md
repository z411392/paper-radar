# 文件與 GitHub SSOT

本檔是文件歸屬唯一 owner。specs/README 只提供操作；角色及 Project Status synchronization 只依 Rule 15。

| 資訊 | 唯一 owner |
|---|---|
| Roadmap 與穩定 Phase exits | docs/delivery/mvp-phases.md |
| Event Storming、共同語言、business model | docs/architecture/event-storming.md |
| BC ownership、supplier/consumer、公開交接 | docs/architecture/context-map.md |
| 共通 requirements/NFR/跨 Story AC | docs/delivery/requirements-specification.md |
| Story scenarios／專屬 AC | specs/<真實-story-issue-number>-<slug>/spec.md |
| Story interfaces／oracles／設計 | 同目錄 plan.md |
| Story meaningful progress／evidence／decisions | 同目錄 progress.md |
| Epic／Story／Task／Bug／Spike scope、依賴、有限 Subtasks、receipts | GitHub Issues |
| Status／Priority／Sprint／Views | 本 repository 的 Delivery Project |
| 資料／API 技術契約 | docs/data-model.md、migrations/、需要時的 docs/api/openapi.yaml |

工程規則只在 .claude/rules，.agents/rules 是 symlink。Root README、CLAUDE、docs README 只導覽。禁止平行 Task YAML／backlog JSON／phase-status Markdown、第二份總計畫／decision log／WI chain／每 BC 文件庫。舊全文與歷史由 Git 或明示的 repo 外備份保留，不建立 repo 內 archive。

對本次 bootstrap 的特殊邊界：尚未發布的 issue seed、Story 待編號文件與機器回條放在 repository 之外的一次性建庫資料包；發布後不作任何雙向同步、排程或執行狀態來源。這不是常駐治理資料庫。

## 每次需求五類影響檢查

先分辨使用者已裁決／提案／純詢問，再逐一檢查 Roadmap、Event Storming、Context Map、共通 requirements、Issues/Story spec；在 owning Task 記已修改位置，或不需改及原因。工程方法更新只改最近 owner rule，其他入口改引用。

明示舊要求被取代範圍，不把原報告／外部文件 tree 當操作授權。未決產品行為留需求對應項，技術缺件留 Task。不因這次整體盤點阻止無直接依賴的局部工作。文件更新不等於新增 live／schema migration／Git writer 權限。

修改後核對 diff、引用、ID、AC 歸屬及依賴。靜態 checker 不證明需求語意完整，也不證產品完成。

## Event Storming 完整性

以 Big Picture 共同探索 → Process Modelling 因果敘事 → Software Design 的 Aggregate／BC 候選逐層收斂；八元素 actor、command、aggregate/system、event、policy、external system、read model、hot spot 用來核對流程，不是填表就算產品驗收。

模型標明這是規劃假設還是經走查確認。成功、拒絕、零結果、重複、失敗、中斷／恢復都需有位置；產品 hot spot 只引用需求待決項，技術缺件引用 Task。

## 格式

人類文件、GitHub Issues／comments／PR 使用繁體中文；path/identifier/commands/commit message 保留英文。文件不以粗體／斜體／底線做強調；比較／映射才用表格。實測日期與範圍可保留，測試總數不冒充永久契約。
