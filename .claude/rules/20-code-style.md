# 程式風格

Python 3.12 為起始設計基線；工具及依賴版本在環境 Task 實際解析後鎖定，不虛構 uv.lock 或宣稱已驗 Apple Silicon。

Identifier、file name、commit message 用英文；人類文件與 GitHub 文字用繁體中文。Python 模組 snake_case，類別 PascalCase；檔案與主要職責具名對應。型別明確、錯誤具 code、不吞錯成空資料。

只用必要 abstraction，不保留註解掉的程式、相容 shim、無依據 fallback、動態猜測 method 的反射。不用 import side effect 啟動 network、DB 或 model。禁止 `__init__.py`，不建立無 owner 的 utils/shared。

格式、unused imports、型別檢查由實際選定工具固定在 pyproject／lock；不得為過 gate 擴大 ignore。修改聚焦 Task scope，不夾帶全庫格式化。
