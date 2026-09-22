# GitHub 人類可讀記錄

上層用繁體中文說明，下層保留 exact formal contract。白話導讀不是第二份 authority；衝突時修正導讀，不以摘要推翻來源。Companion 導覽在 `docs/delivery/github-human-readable-history.md`。

## 新 Issue 必備

依序為：白話說明、完成後代表什麼、不代表什麼、為什麼現在做、Formal Contract。人類層解釋當前問題、影響、方案理由、依賴與成果邊界，不能只是把 title 重寫。專屬 AC 只引用 Story spec 的 anchor，不在 Issue 複製第二版本。

Formal Contract 記 authority、scope/non-scope、parent、owner、輸入／輸出、dependencies、SC／AC／Roadmap effect、exact writable/frozen、GWT、所需命令與 stop。Status／Priority／Sprint 只連到 Project，不在 body 維護影子值。

## 歷史及 comment

Comment 採 append-only，若記錄錯誤另加更正說明。重大轉折用 HUMAN_CHECKPOINT 保存原因與前後差異；回顧 HUMAN_HISTORY_SUMMARY 必有 As of、Last substantive event、CURRENT／HISTORICAL／SUPERSEDED 與關鍵 issue／SHA／comment 引用。

不能重寫舊 summary 假裝當時已知道後續結果；不能把 human-readable migration 冒充產品進展。交付先說實際改變，再列 SHA／commands／result；REJECT 解釋哪個具體行為不成立、修復方向與不受影響部分。

沒有工程歷史的新建 Issue 不偽造反轉或曾經通過；直接說規劃已記錄、實作尚未開始、何者不能由本卡推導。繁體中文敘述保留必要識別字與命令。
