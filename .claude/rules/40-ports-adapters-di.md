# Ports、Adapters 與 DI

Inbound port 由擁有 use case 的 feature 定義，application 實作；driving handler 呼叫 inbound port，不直接依賴 application concrete class。Outbound port 由需要該外部能力的 owner 定義，driven adapter 實作。

ports 可依 use case 一個檔案；DTO 留 feature dtos；不把 feature DTO 搬進 kernel 隱藏依賴。跨 BC 調用只走公開 ports／DTO，不 import supplier 私有 store 或 SQL。

每個 app 的 `module.py` 明確 binding。SQLite 連線、FS 位置、FAISS instance、LLM／SMTP client 的生命週期由 adapter/composition 持有，domain/application 不接收第三方 browser／connection 等物件。

不能用 broad Exception、hasattr 或多組 alternate kwargs 假裝 adapter 契約相容。缺少 binding 必須顯式 configuration error。第一版 schema 就算同庫，也不得由某個無主 global repository 操作所有表。

Callable inbound port 用 `__call__` 表達用例，outbound port 使用具名能力方法；Task #6 的 AST 防線依這個明確約定區分 driving handler 的引用。這是靜態工程防線，不保證辨識所有 Python 動態行為，不能取代獨立審查。
