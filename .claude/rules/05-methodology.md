# 方法與有限工作

每輪先確認目標、current authority、實際基線、要解的問題與可觀察成果。產品決策回到 Product Owner；工程可查證事項由適任 owner 查證，不反覆讓使用者代做除錯。

先依 [Rule 80](80-documentation.md) 完成五類影響檢查。計畫不是授權；目前完整規劃不表示所有 leaf 已可施工。開工一律依 [Task-local readiness](15-execution-strategy.md#task-local-readiness)。

## 有限執行階段

Analysis 確認差距及直接證據；Plan 確認可觀察成果和依賴；Design 固定契約與外部 oracle；Implement 只改明示 writable；Verify 保存實際 deterministic 證據；Accept 由獨立 Reviewer；Integration 由 Commander。這些不是更多層級的 Agile issues。

完整 dispatch 寫在當次 Task issue，包含人類可理解目的、真實 Task／Subtask ID、必要時的 parent Story、適用成果、必要 required readset、base/candidate SHA、branch/cwd、inputs、outputs、writable/frozen、正反 oracle、實際命令、預期結果、writer/integration owner、權限與停止點。

缺件只停止受影響範圍，回報 exact blocker、owner、clear condition；不要用「全案還沒設計」代替局部推理。規格已足夠時，不重複派設計增加成本。worker 停止代表本次 route 返回，不代表 Commander 整批結束，接續依 Rule 15。

每份回報分開：本次實際改變、已執行驗證、未執行與缺件。不能用「已推進」「已完成設定」代替可核對的內容。明示為文件／建庫範圍的批次不擅自擴展成產品實作、live 或寄信。
