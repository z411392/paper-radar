# 配置與資料

本產品 workspace 根目錄由配置指定，可在 repository 外。SQLite、raw/fulltext/evidence、原始向量、FAISS、exports、logs、backup 均不進 Git。這是對 Kaledoxa assets 資料隔離原則的本機產品路徑適配。

`config/` 只保存範例與種子；發布的 WatchProfile revision 是 SQLite 內唯一正式設定。匯入不是雙向 YAML/DB 同步。Secrets 放本機安全儲存或環境，不進 source、Task、schema、model input 或 log；本機不等於 LLM 必須本地，實際 endpoint 必須明示。

FS 物件用 workspace 相對路徑、hash 及格式 metadata 引用；不寫死開發者絕對路徑。模型下載、向量模型授權與容量要在 live/local commissioning 有證據，不以 package import 成功推定模型可用。

所有測試明示隔離 workspace／env／clock，不受使用者 .env 影響。正式資料不作 hermetic fixture。自動清理只針對可重建且未被引用的已知目標；保存／備份原則見資料模型，具體操作放 owner Task。
