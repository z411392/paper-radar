# Application 與 Domain

Domain 擁有業務不變式、entity/value semantics 與純計算，不讀環境、不存取 SQL／FS／FAISS／HTTP／LLM。Application 擁有 use case 和流程，呼叫明確 ports、控制交易及失效／current 判定。

Application 的 commands 修改業務狀態，queries 回傳 read model；只有存在事件→行動的獨立規則才建 application policy，不為每個 if 增加 xPolicy。DTO 是邊界載體，不代替 domain 規則。

共同語言、事件與 Aggregate 歸 Event Storming；資料表不直接等於 Aggregate，app/lib 也不自動等於 BC。跨 feature 不 import 私有 domain model；必要公開 DTO 經 ports 傳遞。

外部副作用不能塞進 SQLite 長交易：外部讀取先取得、驗證／durable 物件後短交易提交；寄送採既定 outbox 契約。重試需業務身份與執行 attempt 分開。
