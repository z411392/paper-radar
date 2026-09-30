# Story 規格與交付入口

歸屬依 [Rule80](../.claude/rules/80-documentation.md)，但產品 scope 以 [共通需求](../docs/delivery/requirements-specification.md) 與 [路線圖](../docs/delivery/mvp-phases.md) 為最高優先。

## 目前產品

Paper Radar 是 GitHub Actions 排程的新論文翻譯 Email bot：

`schedule -> 抓新論文 -> 篩選/去重 -> OpenRouter 繁中整理 -> digest -> SMTP Email`

既有 spec 檔案保留 Git 歷史，不代表仍是產品 backlog。對應 Issue 若已用 `not planned` 關閉，該 spec 只供歷史查閱，後續 agent 不得因檔案存在而重新施工。

## Active / in-scope Story 導覽

- [durable workspace 與關注範圍](5-local-workspace/spec.md) — [Issue #5](https://github.com/z411392/paper-radar/issues/5)
- [增量取得 arXiv 論文並正確續跑](9-arxiv-discovery/spec.md) — [Issue #9](https://github.com/z411392/paper-radar/issues/9)
- [來源 identity / evidence 與去重基礎](13-versioned-evidence/spec.md) — [Issue #13](https://github.com/z411392/paper-radar/issues/13)
- [繁體中文白話解說](17-grounded-explanations/spec.md) — [Issue #17](https://github.com/z411392/paper-radar/issues/17)
- [每日 Email digest 與防重寄](21-daily-email-digest/spec.md) — [Issue #21](https://github.com/z411392/paper-radar/issues/21)
- [可選：擴充新論文來源／領域 coverage](29-five-domain-coverage/spec.md) — [Issue #29](https://github.com/z411392/paper-radar/issues/29)

## Historical / out-of-scope specs

以下檔案保留作歷史紀錄，但對應工作已 `not planned`，不是後續 roadmap：

- `25-durable-cli-worker/`：本機 daemon / launchd 不是正式部署；GitHub Actions 才是 production scheduler。
- `33-local-hybrid-retrieval/`：FTS / FAISS / embedding / hybrid retrieval。
- `37-revision-correction-notices/`：revision / correction / retraction 主動通知。
- `41-local-reading-feedback/`：閱讀歷史、收藏、feedback、localhost UI/RSS。
- `45-consistent-backup-restore/`：使用者 backup / restore 產品。
- `49-operational-evidence/`：health/cost dashboard 與較廣 operational productization。

安全底線（secrets、不可信 abstract、bounded fetch、HTML escaping）仍屬核心，但不因此恢復上述 operational-dashboard scope。
