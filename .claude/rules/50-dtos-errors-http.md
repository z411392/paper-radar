# DTO、錯誤與 HTTP

Feature DTO 是 use-case input/output，transport DTO 屬 app。HTTP status 和 CLI 格式／exit code 在 driving adapter 映射，不滲入 domain。必要 code、enum、schema 先定義再做行為；未知值不得靜默正規化成成功。

錯誤至少區別 invalid_input、configuration_missing、not_found、permission_denied、operation_conflict、source_unavailable、partial_coverage、parse_failed、evidence_mismatch、stale_input、budget_blocked、index_unavailable、delivery_unknown。具體 owner 的 exact enum 與 JSON Schema 由其技術 Task 固定，不把這份例子當完成的 API。

新增 HTTP endpoint 時同步 canonical `docs/api/openapi.yaml` 和 contract test。沒有 HTTP 功能前不生成假空 API 或假健康結果。回應不得洩漏 secrets、內部絕對路徑或受限原文。
