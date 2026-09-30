# 按需查找導航

本目錄只導航，不是規格、核可、看板或授權。歸屬依 [Rule80](../.claude/rules/80-documentation.md)，接受狀態回正式來源。

讀指定 commit 的 navigation.json，先安全底線，再由 Task→必要 Story→Affected BCs→必要供應方→程式/使用方/測試展開。路徑不存在、版本變更、公開介面/schema/安全衝突時擴大查找，不跳過存取權。

只維護路徑、長期責任及存取分類，不放規格全文、目前實作者、PR、Status/Priority/Sprint。CI檢查缺件與越界不代表找全全部間接使用方，更不是查找成效benchmark。
