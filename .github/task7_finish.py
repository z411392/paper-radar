"""One-shot mechanical preparation for Task 7; not a product or agent controller."""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

BASE = '69249a59a7d0dca49b84ace6a6ed8fa37c06a05e'
REPOSITORY = 'z411392/paper-radar'
TEMPORARY = ('.github/task7_finish.py', '.github/workflows/task7-finish.yml')
APPENDS = {
'specs/5-local-workspace/plan.md': '''

## 2026-09-23 Task #7 本機儲存切片

延續使用者的暫行直接實作委派；作者自行建立正反測試，沒有獨立 Architect freeze 或 Reviewer ACCEPT。#7 基於 #53 的未驗收候選 `69249a59a7d0dca49b84ace6a6ed8fa37c06a05e`，分支 `codex/7-durable-local-storage`，不移動 #53 或 main。

S1：InitializeWorkspace inbound port → InitializeWorkspace command → WorkspaceBootstrapPort → SqliteWorkspaceBootstrapAdapter。組裝時明示 Migration 序列，本批只驗證 0001，沒有掃描並自動套用全部 migration。新 DB 在同磁碟暫存檔完成 schema 與身份後，以不覆蓋既有目標的發布方式成為正式 DB；競爭初始化者讀回勝出身份，掃描期間出現的 DB 也必須驗證 application_id 與 schema。一般連線不自動建立遺失 DB。WAL、FULL、foreign_keys、busy_timeout 由連線 factory 明確設定。Migration runner 擁有短交易，逐 statement 執行、檢查已套用檔案雜湊；script 不得自行 COMMIT／ATTACH／PRAGMA。

S2：PublishObject 先呼叫 ObjectBytesPort 寫入及校驗完整 bytes，再由 ObjectUnitOfWorkPort 的短交易登錄 ObjectRegistryPort。SQL 連線不穿出 adapter。相同 kind/hash 使用穩定 ObjectRef；重複回傳原紀錄，metadata 衝突失敗。ReadObject 只按 registry identity 取引用，離開 SQL transaction 後才讀檔及驗證 hash／長度。

S3：InspectStorage 讀取 registry 快照，再檢查內容；回報 missing、corrupt、unsafe_path、unregistered、temporary 等診斷，不修改 registry、不修復、不刪除。真程序以 os._exit 中斷在檔案發布後，後續重試接回既有完整檔案，跨程序讀回相同 bytes。診斷屬觀測結果，不是跨 DB／FS 原子快照，也不是刪除許可。

測試位置：`src/libs/kernel/tests/integration/test_sqlite_workspace.py`、`test_local_storage.py`、`test_bootstrap_race.py` 及 `src/libs/kernel/tests/unit/test_object_ref.py`；包含並行初始化／重複發布、唯讀 transaction、rollback、失敗 migration、陌生 DB、遺失 DB 但保留內容、symlink、fsync 失敗與錯誤 metadata。正式完整驗證仍是 `make ci-fast`。

本批可寫 kernel 新增檔案與本 plan／progress／data-model 的必要技術補充；原 migrations、共通需求、Story spec、Roadmap、Event Storming、架構／治理防線和 #53 程式不變。#6 S3 CLI handler、#8 profile、FAISS／來源／模型／郵件不在此切片，不以 library 測試冒稱那些能力完成。
''',
'docs/data-model.md': '''

## Task #7：本機儲存 adapter 契約

本段補足 schema 草案的執行契約，不改 0001 SQL 內容。SQLite application_id 為 `0x50524452`；現有非本產品 DB 拒絕採用。缺 DB 且留有物件或不明 state 檔案時拒絕重新初始化，以免用新 identity 隱藏遺失的帳本。

ObjectRef 以 `kind:sha256` 為 object_id，relative_path 為 `objects/<kind>/<sha256[:2]>/<sha256>`；副檔名不參與身份，媒體型別存 registry。相同 kind/hash 若媒體或保留政策不同，回 metadata_conflict；內容變更必須得到不同 hash，不覆寫既有物件。檔案採同檔案系統暫存、完整 fsync、不覆蓋式 hard-link 發布與父目錄 fsync；之後才提交 registry。SQL 失敗留下完整未登錄內容，重跑可以接回。缺失／損毀不自動回填或改狀態，保留診斷及復原決策邊界。

本批目標為單一使用者管理的本機 POSIX filesystem（Linux／macOS），要求 hard links、目錄 fsync 與 SQLite WAL 所需能力；平台證據以 exact candidate CI 為準。不宣稱支援 Windows、網路檔案系統、雲端同步工作區或不可靠 fsync 硬體。不將 SQLite／FS 描述為跨資源單一交易。靜態 root／managed path／sidecar symlink 被拒絕；可信 OS 祖先如 macOS /var 可解析。這不是對可同時修改 workspace 的惡意同 UID 程序提供完整 TOCTOU 隔離，工作區必須由同一使用者受控管理。

InspectStorage 為唯讀觀測：registry snapshot 與 FS 掃描並非同一時刻，並行發布可能暫時顯示 unregistered；禁止據此直接 GC。ReadObject 依預期長度限制讀取，bytes 與 SHA256 不符即拒絕；不把缺資料當空內容。應用只透過 registry／bytes／unit-of-work ports 使用能力，不任意跨 BC 寫表。備份／正式復原及 ledger 保護仍由後續工作驗證。
''',
'specs/5-local-workspace/progress.md': '''

## 2026-09-23 Task #7 SQLite／檔案候選

延續 #7 既有開工紀錄與使用者「繼續」要求。本批實作 workspace identity、短 SQLite transaction、explicit migration bundle、hash 內容發布、登錄去重、跨程序讀取與唯讀完整性診斷。未合流 main，沒有獨立 Reviewer、Task Done 或 Project 欄位操作收據。

直接基線為 #53 的未驗收候選 `69249a59a7d0dca49b84ace6a6ed8fa37c06a05e`。本次隔離施工目錄 `/mnt/data/paper-radar-next/checkout` 僅記錄當次執行，不是使用者機器的路徑依賴。

本機透過 uv 的 subsystem RED 首次因未實作 adapter 無法 import；實作後修正測試自己的 repo-root 定位錯誤，再有 27 passed。補強的反例得到 4 failed／42 passed（DB 遺失後誤採用舊內容、陌生 state 檔、未診斷暫存檔與 metadata 控制字元）；修正 production 後 46 passed。另用固定時序重現初始化者在掃描期間完成 DB 發布，使另一個初始化者誤報 foreign_workspace，該反例先失敗；修正重新核對後 subsystem 為 47 passed。這些是作者測試，不是獨立 oracle。

S1 初始提交 `d654e9ff30e10300b6bf74ecc76ce496d59e4227`；S2 提交 `2aefa49ae892eb228e185619cde7669faf074562`；S3 提交 `b947c1b66a32fcc69ae6fbec1c50a1dc0ea69e9e`；初始化競爭修正 `ad622cdf01f9bee890983e32e6988ed85fa120dc`。後續格式／文件提交保留這些歷史，不 squash。

本機套件站 DNS 不可用，subsystem 使用 `uv run --no-project --python /opt/pyvenv/bin/python python -m pytest` 在隔離 src 下執行；不稱為 locked 全庫 gate。完整鎖定環境、ruff／pyright／全庫測試由 GitHub runner 執行，正式結果以本卡與 PR 的 exact candidate CI readback 為準。原始 log 雜湊由 #7 append-only 收據保存，不以 hash 冒稱原始檔永久可下載。

需求、Roadmap、Event Storming、Story AC、原 migrations 與架構／治理測試未變；技術補充放本 Story plan 和 data-model。CLI 初始化、profile、FAISS、真來源、模型與郵件未執行。所有階段只交付已測候選，沒有自我 ACCEPT。
'''
}


def command(args):
    return subprocess.check_output(args, text=True).strip()


def selected_paths():
    paths = command(['git', 'diff', '--name-only', '--diff-filter=A', BASE, 'HEAD', '--', 'src/libs/kernel']).splitlines()
    assert len(paths) == 28, paths
    assert all(p.endswith('.py') and '/tests/architecture/' not in p and '/tests/governance/' not in p for p in paths)
    return paths


def semantic_shape(text):
    tree = ast.parse(text)
    imports = sorted(ast.dump(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))
    body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    return imports, ast.dump(ast.Module(body=body, type_ignores=tree.type_ignores))


def prepare(paths):
    before = {p: semantic_shape(Path(p).read_text(encoding='utf-8')) for p in paths}
    subprocess.run(['uv', 'run', '--locked', '--offline', 'ruff', 'check', '--select', 'I', '--fix', *paths], check=True)
    subprocess.run(['uv', 'run', '--locked', '--offline', 'ruff', 'format', *paths], check=True)
    for path in paths:
        assert semantic_shape(Path(path).read_text(encoding='utf-8')) == before[path], f'Logic changed: {path}'
    for path, suffix in APPENDS.items():
        current = Path(path).read_text(encoding='utf-8')
        marker = next(line for line in suffix.splitlines() if line.startswith('## '))
        assert marker not in current, f'Refusing duplicate append: {path}'
        Path(path).write_text(current + suffix, encoding='utf-8')
    print(f'TASK7_PREPARED: {len(paths)} new Python paths; three append-only document changes')


def export(paths):
    changed = command(['git', 'diff', '--name-only', 'HEAD']).splitlines()
    assert set(changed) <= set(paths) | set(APPENDS), changed
    assert set(APPENDS) <= set(changed)
    entries = [{'path': p, 'mode': '100644', 'type': 'blob', 'content': Path(p).read_text(encoding='utf-8')} for p in changed]
    entries += [{'path': p, 'mode': '100644', 'type': 'blob', 'sha': None} for p in TEMPORARY]
    payload = {'base_tree': command(['git', 'rev-parse', 'HEAD^{tree}']), 'tree': entries}
    result = subprocess.run(['gh', 'api', '-X', 'POST', f'repos/{REPOSITORY}/git/trees', '--input', '-'],
                            input=json.dumps(payload, ensure_ascii=False), text=True, capture_output=True, check=True)
    tree = json.loads(result.stdout)['sha']
    print('TASK7_CHECKED_COMMIT=' + command(['git', 'rev-parse', 'HEAD']))
    print('TASK7_VERIFIED_TREE=' + tree)
    print('Only a tree object was created. No commit, branch movement, merge, issue or Project mutation.')


assert os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
assert os.environ.get('GITHUB_REF_NAME') == 'codex/7-durable-local-storage'
paths = selected_paths()
if sys.argv[1:] == ['prepare']:
    prepare(paths)
elif sys.argv[1:] == ['export']:
    export(paths)
else:
    raise SystemExit('Expected prepare or export')
