"""One bounded author operation for #60; removed from the final candidate."""

import hashlib
import json
import subprocess
from pathlib import Path

BASE = "3a0902bf0d729945ecff77b3e96011ac25fbc7c0"


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    assert text.count(old) == 1, path
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


rule80 = ".claude/rules/80-documentation.md"
replace(rule80,
    "來源：Fractal Authority Hierarchy 2.0-draft，原檔 fractal-authority-hierarchy-methodology-integrated.zh-TW(5).md，SHA-256 a599a2e49c9908cba710e54a8917ddeb033fcf5bcb93f0652b0a3d8ce45dd532。原附件仍是 REFERENCE_METHODOLOGY，不自行授權操作；本次使用者指示授權適用原則的專案化，作者轉錄不是獨立核可來源。",
    "來源：使用者本輪提供《分層式正式依據管理方法論(2).md》，3.0-draft／2026-09-23，SHA-256 ad1d4a126b4648c241b9f133cd2dbcacd8db225a240e84bd07e87096bf701059。原附件仍是 REFERENCE_METHODOLOGY；本次指示授權文件歸屬、工作分類及按需查找的專案化，不自行擴張操作權或構成獨立驗收。先前2.0採用與差異來歷保留在 Task #60 及 PR #61 的歷史；不以新附件倒改舊程式適用規格。")
replace(rule80,
    "本階段為 GIT_RETAINED：先分清規則歸屬、停止重抄、按需查找並保留歷史。正式 Story spec 與必要 plan 暫留 Git，Issue 引用。",
    "本專案採 GIT_RETAINED：正式 Story spec 與必要 plan 留 Git，Issue 引用適用版本。此方式可長期沿用，不是必須淘汰的過渡方案。先分清規則歸屬、停止重抄、按需查找並保留歷史。")
replace(rule80,
    "| Story 目標、SC/AC、必要設計 | 切換前 specs/<number>-<slug>/spec.md 與必要 plan.md；Issue 引用 |",
    "| Story 目標、SC/AC、必要設計 | 本專案指定的 specs/<number>-<slug>/spec.md 與必要 plan.md；Issue 引用，可長期沿用 |")
replace(rule80,
    "另需驗收單一寫入/版本比對、合併前原文重查、最終 commit 解析、匯出備份與還原、適當權限/保護。",
    "若選擇改由 Issue 保存正式規格，另需驗收單一寫入/版本比對、合併前適用規格重查、最終 commit 解析、匯出備份與還原、適當權限/保護。只因別的分支或尚未接受的提案更新，不自動推翻本次仍有效的規格版本。讀取、寫入、再讀回不等於防止並行覆寫；目前由單一寫入者序列修改本文，不宣稱任何 API 自帶 CAS 或跨工具交易。")
replace(rule80,
    "## 工作分類與按需查找\n",
    "## 工作分類與按需查找\n\nEpic 只在多張需求共同交付一個大成果時建立；Story 是可整體驗收的成果，不按資料庫、後端或資料夾機械拆單；Task 是授權範圍內的具體修改；Spike 必須有研究問題、資料/工具限制、時間或嘗試上限、交付物、停止條件及決策去向。證據不足或不建議採用可以是研究結論，不等於產品規格已接受。範本供填寫必要內容，不要求保留所有空章節；既有機器欄位與類型不為翻譯強制改名。\n")
replace(".claude/rules/16-github-human-readable-records.md",
    "## 本文\n",
    "## 本文\n\n開單先看成果與不確定性，不先湊層級。Epic 寫共同目標與整體驗收；Story 指定正式規格位置、適用版本、涉及 BC、協調責任及整合驗收；Task 明列輸入成果、可寫/保持不變/禁止範圍、使用方、步驟和正反結果；Spike 明列問題、嘗試上限、證據、停止點及誰決定/接受後回寫哪裡。既有 [Issue Forms](../../.github/ISSUE_TEMPLATE/) 是填寫入口，非另一份已接受規格；保留 human/contract 與既有 type 標籤。Bug 入口沿用既有工具類型，但責任和完成條件按有界修正，不強迫補造 Story。\n")
replace("specs/README.md", "## 目前採 GIT_RETAINED\n",
    "## 目前採 GIT_RETAINED\n\n規格留 Git 是可長期使用的正式選擇，不必為形式一致搬進 Issue。開單使用既有 [Issue Forms](../.github/ISSUE_TEMPLATE/)，按成果或研究問題選型，不為每件小事補造 Epic/Story。\n")

forms = {
    "epic": ("Epic", "共同交付成果與整體驗收，不建立永久領域工作單", "這批需求要一起完成什麼？為何需要共同追蹤？", """交付協調責任：<角色；目前指派與進度只讀指定欄位>
涉及領域：<實際 BC，可多個>

## 整體成果
<整批完成後帶來的價值>
## 範圍與不做事項
<只寫本次交付邊界，不重抄領域規則>
## 相關需求與依賴
<引用真實 Stories，不複製其本文>
## 整體驗收
<不是只數子工作單結案；如何驗證整體成果>
## 正式依據與版本
<產品/領域/合作規格的適用 Git 版本>
未接受提案與研究留 comments；Epic 不接管長期 BC 規則。
"""),
    "story": ("Story", "可驗收的成果、適用 Git 規格與跨領域合作", "誰在什麼情境需要什麼成果？", """規格識別：<專案與工作單的穩定身分>
正式規格位置：<Git 路徑及適用版本；不另抄 SC/AC>
接受狀態：<未接受，或可查驗的具體接受來源；不得預填通過>
所屬 Epic：<需要時才填>
交付協調責任：<角色；目前指派與進度只讀指定欄位>
涉及領域：<實際 BC；可跨領域>

## 目標與範圍
<可觀察成果與不做事項>
## 情境與整合驗收
<引用指定 Git 規格；成功、失敗、邊界與合作結果>
## 合作責任與正式依據
<本次涉及方、提供方規格及精確適用版本>
## 設計與執行計畫
<已接受設計引用；目前工作順序不得默默改需求>
## 子任務與驗收來源
<真實 Task/Spike；測試/審查/整合證據引用>
未接受提案留 comments；Git 規格可以長期沿用，並非一定要搬到 Issue。
"""),
    "task": ("Task", "具體修改的授權範圍、使用方及正反驗證", "已知輸入是什麼？完成後會有哪個可觀察差異？", """所屬需求（可省略）：<獨立小任務不補造 Story>
目標版本：<程式基線及適用規格版本>
涉及領域：<實際 BC/技術責任>
指派來源：<目前指派與進度只讀指定欄位>

## 正式依據
<必要需求/領域/介面/共同限制及適用版本>
## 修改範圍
允許修改：<有界檔案或模組>
保持不變：<不得放寬的條件與測試>
禁止修改：<未授權資料、操作或範圍>
受影響使用方：<直接與必要間接使用者>
## 執行步驟與失敗處理
<有限步驟；小 Subtasks 留本卡，不另做相同 Task Pack>
## 驗證
正常結果：<應成功什麼>
反例結果：<應拒絕什麼，不得有何副作用>
檢查命令：<可執行命令；未執行不填通過>
未驗證事項：<缺件與原因>
## 完成與升級處理
<送審/完成門檻，未知問題交誰決定>
未接受提案、研究、失敗與驗證結果追加 comments。
"""),
    "spike": ("Spike", "有上限的探索研究，證據與方案接受分開", "研究問題是什麼？查清楚後支持哪個決定？", """關聯工作：<有需要才引用>
影響範圍：<涉及領域或合作規格>
指派來源：<目前指派與進度只讀指定欄位>

## 研究問題
<需要回答的具體不確定性>
## 資料與工具限制
<允許/禁止資料、工具、外部操作與預算>
## 時間或嘗試上限
<明確限制，不做無界探索>
## 研究方式與交付物
<比較方案、可比性、證據、建議、限制與未解問題>
## 停止條件
<何時足夠；何時證據不足並停止擴大>
## 決策去向
<谁有權接受具體版本；接受後回寫哪個正式位置>
未接受提案與研究過程留 comments；不建議採用也可結束研究，不假稱已核可方案。
"""),
    "bug": ("Bug", "沿用既有缺陷類型，以有界 Task 要求修正", "錯在哪裡、影響誰？不要附上金鑰或私人資料。", """關聯需求：<獨立修正不強制補造 Story>
目標版本：<出錯程式及適用規格版本>
指派來源：<目前指派與進度只讀指定欄位>

## 預期與實際
<規格要求與觀察行為分開；引用正式版本>
## 重現步驟
<安全的合成輸入、步驟與可驗證結果>
## 修改範圍與使用方
<允許/保持不變/禁止修改，直接與必要間接影響>
## 驗證與失敗處理
正常結果：<修正後應成功的行為>
反例結果：<固定重現缺陷且不得放寬既有期待>
未驗證事項：<未跑的檢查與原因>
## 完成與升級處理
<必要審查與整合；需求或公開介面修改另經接受>
未接受提案、研究、失敗與修正追加 comments；沿用 type:bug，不新增另一套流程。
"""),
}
for kind, (name, description, human, value) in forms.items():
    target = Path(f".github/ISSUE_TEMPLATE/{kind}.yml")
    old = target.read_text(encoding="utf-8")
    assert f'labels: ["type:{kind}"]' in old and "id: human" in old and "id: contract" in old
    value = value.replace("谁", "誰")
    lines = [f"name: {name}", "description: " + json.dumps(description, ensure_ascii=False),
             f'labels: ["type:{kind}"]', "body:", "  - type: textarea", "    id: human",
             "    attributes:", "      label: 白話說明", "      description: " + json.dumps(human, ensure_ascii=False),
             "    validations:", "      required: true", "  - type: textarea", "    id: contract",
             "    attributes:", "      label: Formal Contract／正式依據與工作內容",
             "      description: 只填本次必要內容；不要貼上秘密或複製另一份現行規格。", "      value: |"]
    lines += ["        " + line if line else "" for line in value.splitlines()]
    lines += ["    validations:", "      required: true"]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

manifest = Path(".authority/manifests/git-baseline.json")
data = json.loads(manifest.read_text(encoding="utf-8"))
changed_rules = {rule80, ".claude/rules/16-github-human-readable-records.md"}
for source in data["git_sources"]:
    if source["path"] in changed_rules:
        source["sha256"] = hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest()
manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
frozen = ["specs/*/spec.md", "specs/*/plan.md", "specs/*/progress.md", "migrations", "uv.lock", ".agents/roster.json"]
assert not subprocess.check_output(["git", "diff", BASE, "--", *frozen])
print("FROZEN_STORY_SQL_LOCK_ROSTER_UNCHANGED=1; ISSUE_AUTHORITY_DISABLED=1")
