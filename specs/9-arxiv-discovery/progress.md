# 增量取得 arXiv 論文並在失敗後從正確位置續跑：證據與決策

Story：https://github.com/z411392/paper-radar/issues/9

## 2026-09-22 規劃基線

已將使用者的本機儲存與 Kaledoxa 治理要求反映到 [spec](spec.md)、[plan](plan.md) 與 Issues 階層。本輪交付的是規劃，不是產品程式完成。

文件影響：需求見共通 R01–R18；成果順序見 Roadmap；事件語言見 Event Storming；owner／交接見 Context Map；工作分解見本 Story 原生 child Tasks。舊 PostgreSQL／獨立 worker／平行文件庫提案不作現行前提。

本 Story 的產品驗證命令尚未執行，exit code 為 N/A；真來源／真模型／真實寄送／獨立 ACCEPT 收據皆未提供。建庫資料包自己的靜態檢查與 SQL 測試，不構成本 Story 的產品證據。

後續重大決策與測試在此追加帶日期的證據，記 exact SHA、命令、結果與範圍；不在本檔維護 Status／Priority／Sprint，也不因 Issue 已建立而宣稱 Story accepted。


## 2026-09-23 Task #10：查詢編譯與分頁的作者候選

直接基線 #57 `2ffd067a79ec8dcb3a87b424eecacdc694c6dc14`；S1 `9a0c75b6a18beeef24b387b984c8061e16ef11fa`、S2 `5c1f6cdde53ff77dd7d7906cf3106fcffef6c2df`、S3 `9c42218d4a660e4cea9d55b490ae60530ba53f5c`。只新增 discovery 與本 Story／Context Map 的技術紀錄，不重做 #8 或改上游分支。

研究／探索與必要契約在 [#10開工紀錄](https://github.com/z411392/paper-radar/issues/10#issuecomment-5781773921)；實際反例、修正及限制在 [#10局部驗證](https://github.com/z411392/paper-radar/issues/10#issuecomment-5781839032)。區分報告要求、官方來源能力與本案工程推論，不把模型記憶當source contract。

容器隔離命令：uv run --no-project --python /opt/pyvenv/bin/python python -m pytest libs/discovery/tests -q --tb=short。只有tests時因module不存在而collection RED（exit2、1錯誤），不冒稱74個行為RED。初版67 passed；補7個邊界後2 failed／72 passed，再修正為74 passed。strftime對四位年數的跨平台補零與list-valued deferred_mode漏出TypeError皆先有失敗反例。這是既存interpreter局部證據，不是正式locked／全庫／跨平台驗證。

74項含精確查詢、相同設定／時區重播、版本變更fingerprint、native不支援条件、query literal與URL參數邊界、日期／型別、每頁識別、合成同時間跨頁與重播、異常empty／partial／failed／total drift。沒有將synthetic identities當成真實論文，也不以Python set去重fixture宣稱真來源已durable去重。

GitHub收尾流程先核對15個新檔的Git blob identity，只格式化新Python且逐檔AST相等，再跑locked make ci-fast。最終run／head／tree與完整數量由本卡comments及PR讀回追加；未取得時不先填PASS。所有既有tests照跑，格式／失敗與修正不隱藏。

尚未提供真來源、Atom解析、provider限速、工作排程、raw保存與checkpoint、CLI查詢入口、模型或推送。#50 SQLite build修補證據缺口仍保留，本批無正式資料庫操作。作者和CI不是獨立ACCEPT，不合流main、不結案Task／Story、不標Project Done；Project欄位未同步。
