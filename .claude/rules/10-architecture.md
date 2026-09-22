# 程式碼架構

薄 app、厚 lib；Python modular monolith，Ports & Adapters，production feature import graph 為 DAG。

`src/apps/cli` 是第一版 driving surface，包含長駐 `run-worker` 的程序啟動。`src/apps/http` 是後續 localhost HTTP 入口；第一版不獨立 `apps/worker`。程序生命週期放 app，業務排程和持久工作放 `libs/research_workflow`。

```text
libs/<feature>/
  application/{commands,queries,policies}
  domain/services
  ports
  dtos
  exceptions
  adapters/driven
  constants
  helpers
  tests/{unit,integration,contract,fixtures}
apps/<app>/
  __main__.py
  entrypoints.py
  module.py
  adapters/driving
  dtos
  exceptions
  constants
  helpers
  tests
```

只建立實際使用的目錄。禁止 `__init__.py`；不新增無 owner 的 `contracts/`、`types/`、`public/`、`shared/` 或 `utils.py`。程式可用 namespace packages。Prompt／template 是 owner 模組的版本化程式資源，不是新的產品文件庫。

apps 不能有 `service.py`、`ports.py`、`ports/`、`composition.py`、`errors.py`、`adapters/driven`。DI root 固定 `module.py`。業務 application／domain／port／driven adapter 都在 libs。

| import 來源 → 目的 | 規則 |
|---|---|
| libs.A → libs.B.ports／dtos／明示核准純算法 | 允許 |
| libs.A → libs.B.application／adapters／domain | 禁止 |
| 任一 lib → apps；kernel → feature | 禁止 |
| driving handler → inbound port／DTO／exception | 允許 |
| driving handler → application concrete class／outbound port／adapter | 禁止 |
| apps/<app>/module.py → ports／application／driven adapters | 組裝例外 |
| 任一 lib → app transport DTO；app → 另一 app | 禁止 |

SQLite 共庫不代表可以跨 owner 任意讀寫資料表。跨 BC 交易必須由 orchestration 使用公開 port 與同一 unit-of-work 協調，不 import 別人的 private adapter。kernel 僅承載 bytes／hash／clock／交易基礎，不認識 Work、Digest 或 WatchProfile。

任何 layout 改動先修改 Context Map 的實作導航；不因程式搬家重切 BC 或重抄產品文件。
