"""Bounded publication into the exact private repository selected by the owner.
No new repo/project, source collection, model call, email, agent, or product run.
Transport inputs and the execution journal stay outside the product git tree.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent / 'bundle'
spec = importlib.util.spec_from_file_location('bootstrap', ROOT / 'bootstrap_github.py')
assert spec and spec.loader
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)
FULL = 'z411392/paper-radar'
BASE = 'b100b5bf3dffe3a8195dc5922b693979936c21c6'
REPO_ID = 1381086277
MESSAGE = 'docs: publish approved Paper Radar planning baseline'
RUN_ID = 'owner-created-1381086277-20260922-v1'
PROJECT_UNKNOWN = '`Paper Radar`（既有 Project；尚未取得存取與欄位同步證據）'

original_api = boot.gh_api
last_write = 0.0


def api(endpoint, method='GET', payload=None):
    global last_write
    if method != 'GET':
        time.sleep(max(0.0, 1.1 - (time.monotonic() - last_write)))
        last_write = time.monotonic()
    return original_api(endpoint, method, payload)


boot.gh_api = api


def blob_sha(text):
    data = text.encode('utf-8')
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def verify_tree(entries, tree):
    if tree.get('truncated'):
        raise boot.ProvisionError('Incomplete git tree readback.')
    actual = {e['path']: (e['mode'], e['sha']) for e in tree['tree'] if e['type'] == 'blob'}
    desired = {e['path']: (e['mode'], blob_sha(e['content'])) for e in entries}
    if actual != desired:
        missing = sorted(set(desired) - set(actual))
        extra = sorted(set(actual) - set(desired))
        changed = sorted(k for k in desired.keys() & actual.keys() if desired[k] != actual[k])
        raise boot.ProvisionError(f'Tree mismatch: missing={missing}; extra={extra}; changed={changed}')


class ExistingPublisher(boot.Publisher):
    def event(self, name, result=None):
        super().event(name, result)
        print(json.dumps({'event': name, 'detail': result}, ensure_ascii=False), flush=True)

    def publish_documents(self, mapping):
        entries = boot.repository_entries(self.seed, mapping)
        current = api(f'repos/{FULL}/git/ref/heads/main')['object']['sha']
        head = api(f'repos/{FULL}/git/commits/{current}')
        if current != BASE:
            if head['message'] != MESSAGE or [p['sha'] for p in head['parents']] != [BASE]:
                raise boot.ProvisionError('main moved outside this publication; no overwrite.')
            verify_tree(entries, api(f"repos/{FULL}/git/trees/{head['tree']['sha']}?recursive=1"))
            self.s.update(candidate_sha=current, candidate_tree=head['tree']['sha'])
            self.save()
        else:
            base_tree = head['tree']['sha']
            tree = api(f'repos/{FULL}/git/trees', 'POST', {'base_tree': base_tree, 'tree': entries})
            commit = api(f'repos/{FULL}/git/commits', 'POST', {
                'message': MESSAGE, 'tree': tree['sha'], 'parents': [BASE]})
            self.s.update(candidate_sha=commit['sha'], candidate_tree=tree['sha'])
            self.save()
            if api(f'repos/{FULL}/git/ref/heads/main')['object']['sha'] != BASE:
                raise boot.ProvisionError('main changed during preparation; candidate left unmerged.')
            api(f'repos/{FULL}/git/refs/heads/main', 'PATCH', {'sha': commit['sha'], 'force': False})
        published = api(f'repos/{FULL}/git/ref/heads/main')['object']['sha']
        if published != self.s['candidate_sha']:
            raise boot.ProvisionError('main SHA readback mismatch.')
        tree = api(f"repos/{FULL}/git/trees/{self.s['candidate_tree']}?recursive=1")
        verify_tree(entries, tree)
        self.s['published_sha'] = published
        self.event('documents_readback', {'commit': published, 'files': len(entries)})


def main():
    if os.environ.get('GITHUB_REPOSITORY') != FULL:
        raise boot.ProvisionError('Only the explicitly selected repository is authorized.')
    repo = api('repos/' + FULL)
    if repo['id'] != REPO_ID or not repo['private'] or repo['default_branch'] != 'main':
        raise boot.ProvisionError('Repository identity, visibility, or default branch mismatch.')
    current = api(f'repos/{FULL}/git/ref/heads/main')['object']['sha']
    if current != BASE:
        head = api(f'repos/{FULL}/git/commits/{current}')
        if head['message'] != MESSAGE or [p['sha'] for p in head['parents']] != [BASE]:
            raise boot.ProvisionError('Unexpected existing main; no remote mutation performed.')

    seed = json.loads((ROOT / 'seed/backlog.json').read_text())
    boot.validate_seed(seed)
    if len(seed['items']) != 52:
        raise boot.ProvisionError('Unexpected seed scope.')
    journal = ROOT.parent / 'publication-journal.json'
    if not journal.exists():
        boot.write_json(journal, {'version': 1, 'run_id': RUN_ID, 'owner': 'z411392',
                                 'repo': 'paper-radar', 'issues': {}, 'events': []})
    publisher = ExistingPublisher(seed, 'z411392', 'paper-radar', journal)
    publisher.s['initial_sha'] = BASE
    publisher.s['repository'] = {k: repo[k] for k in ('id','node_id','full_name','html_url','private','default_branch')}
    publisher.save()

    project = None
    project_receipt = {'title': 'Paper Radar', 'status': 'NOT_VERIFIED', 'url': None}
    try:
        matches = [p for p in publisher.projects() if p['title'] == 'Paper Radar']
        if len(matches) == 1 and not matches[0]['public']:
            project = matches[0]
            publisher.s['project'] = project
            publisher.save()
            project_receipt.update(status='READ_VERIFIED', url=project['url'])
        else:
            project_receipt['reason'] = 'Exact private Project could not be uniquely identified; no replacement created.'
    except boot.ApiError as exc:
        project_receipt['reason'] = str(exc)
        project_receipt['status'] = 'PROJECT_AUTHORIZATION_UNAVAILABLE'
    publisher.event('project_read_attempt', project_receipt)

    # The user selected an existing repository and Project. Update only the obsolete
    # bootstrap-adoption sentence; do not change product or model/agent policy.
    ops = ROOT / 'repository/.claude/rules/90-operations.md'
    text = ops.read_text()
    text = text.replace(
        '這次 initializer 只新增一個 private repository／Project／工作基線；禁止改 Kaledoxa、提升公開可見性、覆蓋同名既有庫、套用舊狀態到已開始的工作，或執行產品 live 工作。',
        '本次規劃發布只寫入使用者明示建立的 private repository `z411392/paper-radar`，並沿用既有 `Paper Radar` Project；先核對精確 repository ID、main SHA 與既有內容。禁止改 Kaledoxa、建立替代 Project、提升公開可見性、覆蓋未經核對的既有工作、套用舊狀態到已開始的工作，或執行產品 live 工作。')
    ops.write_text(text)

    original_render = boot.render
    def render(text, mapping):
        if project is None:
            text = text.replace('[GitHub Delivery Project]({{project_url}})',
                                'Paper Radar（既有 Project；尚未取得存取與欄位同步證據）')
        return original_render(text, mapping)
    boot.render = render

    publisher.ensure_labels()
    publisher.ensure_issues()
    mapping = boot.mapping_for(seed, publisher.s['issues'], repo['html_url'],
                               project['url'] if project else PROJECT_UNKNOWN)
    publisher.publish_documents(mapping)
    publisher.finalize_bodies(mapping)

    if project:
        try:
            boot.mutate('linkProjectV2ToRepository', 'LinkProjectV2ToRepositoryInput',
                        {'projectId': project['id'], 'repositoryId': repo['node_id']}, 'repository{id}')
            publisher.ensure_fields()
            publisher.ensure_items()
            publisher.ensure_views()
            project_receipt['status'] = 'FIELDS_ITEMS_VIEWS_READBACK_VERIFIED'
        except (boot.ApiError, boot.ProvisionError) as exc:
            project_receipt.update(status='PROJECT_PARTIAL', reason=str(exc))

    # Repeat the actual remote read; Issue existence is not product completion.
    remote = [i for i in boot.rest_list(f'repos/{FULL}/issues?state=all') if 'pull_request' not in i]
    indexed = {i['number']: i for i in remote}
    edges = 0
    for item in seed['items']:
        saved = publisher.s['issues'][item['key']]
        actual = indexed[saved['number']]
        desired = boot.render((ROOT / item['body_file']).read_text(), mapping).rstrip() + '\n\n' + publisher.marker_for(item['key']) + '\n'
        if actual['id'] != saved['id'] or actual['body'] != desired:
            raise boot.ProvisionError('Final issue readback mismatch: ' + item['key'])
        if item.get('parent'):
            parent = api(f"repos/{FULL}/issues/{actual['number']}/parent")
            if parent['id'] != publisher.s['issues'][item['parent']]['id']:
                raise boot.ProvisionError('Final hierarchy mismatch.')
            edges += 1
    receipt = {
        'repository': repo['html_url'], 'commit': publisher.s['published_sha'],
        'issues': len(publisher.s['issues']), 'native_parent_relations': edges,
        'subtasks': sum(len(i.get('steps', [])) for i in seed['items']),
        'project': project_receipt, 'product_implementation': 'NOT_RUN',
        'independent_acceptance': 'NOT_RUN', 'agents': 'UNBOUND',
        'source_live_model_live_email': 'NOT_RUN', 'issue_map': publisher.s['issues']}
    boot.write_json(ROOT.parent / 'publication-receipt.json', receipt)
    publisher.event('PUBLICATION_READBACK_COMPLETE', receipt)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as f:
            f.write('# Paper Radar planning publication\n\n```json\n' +
                    json.dumps(receipt, ensure_ascii=False, indent=2) + '\n```\n')


if __name__ == '__main__':
    main()
