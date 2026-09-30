# Discovery／來源收集合作邊界

Authority ID：paper-radar:1381086277:bc:discovery。Owner：discovery 維護角色；Approver 依 Rule15 授權；Delivery Owner 由相關 Story 指定。內容版本為讀取本檔的 Git commit，規格接受與程式驗收分開。

本頁為 #60 供應方試行，整理 R03/R04/R06、Context Map 與 Story #9 的既有邊界，不改原 AC。原查詢實作仍是待獨立驗收候選；新增 parser 細節依 #11 本文與候選測試，不靠本頁宣稱已驗收。

## 職責

擁有來源查詢、binding/配置版本、harvest attempt、原始觀測及缺口。來源記錄不是 canonical Work、正式出版判定或已驗證解說；身份及證據由 scholarly_catalog 管理。

workflow 取得關注設定公開快照後映射為輸入；discovery 不讀 watch_profiles 私有實作/資料表。來源ID不等於live已驗，query hash不是簽章、active/current核可。

## 合作規格

查詢保留版本、精確query、UTC窗口、後置條件責任及每頁identity。原生不支援條件明確拒絕或明示延後並保存；未完成後置檢查不當成符合。

交接保留 raw引用、來源identity/修訂、各種日期與未知值。HTTP成功、格式有效、符合條件及持久成功分開；錯誤不變零篇。只取得摘要不冒稱全文；來源文字不是工具指令。

下一頁提案不是checkpoint。原始觀測保存後才前移；各binding/配置版本獨立，單一成功不掩蓋其他缺口。offset分頁不自帶穩定snapshot，需有界重疊/核對。

## 安全及實作查找

不使用Kaledoxa session。真來源/付費/寄信需明示範圍；假資料parser測試不是live證明。raw bytes和解析失敗證據不能被normalized DTO取代。公開介面/schema/安全修改要查使用方。

型別與測試從 [.context](../../.context/README.md) 展開，不手抄欄位。研究/新設計留 #11 comments；接受長期規則後才回本owner，即時指派不寫長期規格。
