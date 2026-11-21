"""通知按仓库归类且只读 GitHub，不能从标题猜项目或访问任意地址。"""
from types import SimpleNamespace
import json
import time
import pytest
from personal import github
from personal import notifications as inbox


@pytest.fixture(autouse=True)
def clean_states():
    inbox._states.clear(); inbox._states_loaded.clear(); github.reset()
    yield
    inbox._states.clear(); inbox._states_loaded.clear(); github.reset()


def row(repo='me/repo', number='7', title='A title'):
    return {'id': '100', 'unread': True, 'reason': 'mention', 'updated_at': '2026-11-06T01:00:00Z',
            'repository': {'full_name': repo},
            'subject': {'title': title, 'type': 'Issue', 'url': f'https://api.github.com/repos/{repo}/issues/{number}', 'latest_comment_url': None}}


def test_classification_prefers_exact_repository_and_respects_scope():
    projects = [{'id': 'fork', 'name': 'Fork', 'repository': 'me/repo', 'commit_repositories': ['org/repo']},
                {'id': 'original', 'name': 'Original', 'repository': 'org/repo'}]
    matched = inbox.classify(row('org/repo'), projects, projects)
    assert matched['project_id'] == 'original' and matched['classification'] == 'workspace'
    paused = inbox.classify(row('me/repo'), [], projects)
    assert paused['classification'] == 'paused' and paused['project_id'] is None
    assert inbox.classify(row('outsider/repo', title='please work on me/repo'), projects, projects)['classification'] == 'other'


def test_archived_project_messages_go_to_other_messages_marked_archived():
    live = {'id': 'live', 'name': 'Live', 'repository': 'o/live'}
    done = {'id': 'done', 'name': '旧项目', 'repository': 'o/done', 'archived': True, 'archived_at': '2026-11-12'}
    # 归档项目不在当前范围（active）里，只在登记表（known）里
    match = inbox.classify(row('o/done'), [live], [live, done])
    assert match == {'classification': 'archived', 'project_id': None, 'project_name': '旧项目', 'candidates': []}
    assert inbox.classify(row('o/live'), [live], [live, done])['classification'] == 'workspace'
    stale = {'id': 'stale', 'name': 'Stale', 'repository': 'o/stale'}
    assert inbox.classify(row('o/stale'), [live], [live, done, stale])['classification'] == 'paused'


def test_renamed_repository_and_ambiguous_alias_are_explicit():
    p = {'id': 'one', 'name': 'One', 'repository': 'old/repo', 'commit_repositories': ['new/repo']}
    assert inbox.classify(row('NEW/repo'), [p], [p])['project_id'] == 'one'
    q = {'id': 'two', 'name': 'Two', 'repository': 'else/repo', 'commit_repositories': ['new/repo']}
    assert inbox.classify(row('new/repo'), [p, q], [p, q])['classification'] == 'ambiguous'


def test_subject_links_and_safe_api_paths():
    pr = row(); pr['subject'].update(type='PullRequest', url='https://api.github.com/repos/me/repo/pulls/7')
    assert inbox.subject_link(pr) == 'https://github.com/me/repo/pull/7'
    check = row(); check['subject'].update(type='CheckSuite', url=None)
    assert inbox.subject_link(check) == 'https://github.com/me/repo/actions'
    assert inbox.resource_path('https://evil.invalid/repos/me/repo/issues/7', 'me/repo') is None
    assert inbox.resource_path('https://api.github.com/repos/other/repo/issues/7', 'me/repo') is None
    assert inbox.resource_path('https://api.github.com/repos/me/repo/issues/comments/8', 'me/repo') == 'repos/me/repo/issues/comments/8'


def test_github_transport_forces_get_and_paginates(monkeypatch):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps([[row()], []]), stderr='')
    monkeypatch.setattr(inbox.subprocess, 'run', run)
    assert len(inbox.fetch_notifications()) == 1
    assert '--paginate' in commands[0] and '--slurp' in commands[0]
    assert commands[0][commands[0].index('--method') + 1] == 'GET'
    assert 'all=true' in commands[0][-1]


def test_detail_reads_body_and_comments_separately(monkeypatch):
    paths = []
    def get(path, **kwargs):
        paths.append(path)
        if path.endswith('/comments?per_page=100'):
            return [{'body': 'second', 'user': {'login': 'b'}, 'created_at': '2026-11-06T02:00:00Z'},
                    {'body': 'first', 'user': {'login': 'a'}, 'created_at': '2026-11-06T01:00:00Z'}]
        return {'body': '## What and why', 'html_url': 'https://github.com/me/repo/pull/7', 'user': {'login': 'me'},
                'created_at': '2026-11-05T00:00:00Z', 'updated_at': '2026-11-06T00:00:00Z'}
    monkeypatch.setattr(inbox, 'github_get', get)
    n = row(); n['subject']['url'] = 'https://api.github.com/repos/me/repo/pulls/7'
    n['subject']['latest_comment_url'] = 'https://api.github.com/repos/me/repo/pulls/7'
    detail = inbox.read_content(n)
    assert detail['body'] == '## What and why' and detail['author'] == 'me' and detail['content_kind'] == 'pull'
    assert [c['body'] for c in detail['comments']] == ['first', 'second']
    assert paths == ['repos/me/repo/pulls/7', 'repos/me/repo/issues/7/comments?per_page=100']


def test_rate_limited_content_is_not_cached_and_old_value_survives(monkeypatch):
    import time
    from personal import github
    github.reset()
    state = {'limited': True}
    def run(command, kind, **kwargs):
        if state['limited']:
            raise github.RateLimited(time.time() + 300, 'secondary rate limit')
        return json.dumps([] if command[-1].endswith('/comments?per_page=100') else {'body': '正文', 'user': {'login': 'me'}})
    monkeypatch.setattr(inbox.github, 'run', run)
    n = row()
    try:
        first = github.cached(('notification', '100'), lambda: inbox.read_content(n), 300)
        assert first['value'] is None and first['limited_until']  # 限流没被当成读到的内容
        github.reset(); state['limited'] = False
        fresh = github.cached(('notification', '100'), lambda: inbox.read_content(n), 300)
        assert fresh['value']['body'] == '正文' and fresh['value']['body_error'] is None
        state['limited'] = True
        again = github.cached(('notification', '100'), lambda: inbox.read_content(n), 300, force=True)
        assert again['value']['body'] == '正文' and again['limited_until']  # 限流时给旧值
    finally:
        github.reset()


def test_only_review_requests_and_assignments_need_action(monkeypatch):
    reasons = ['review_requested', 'assign', 'ci_activity', 'author', 'state_change', 'subscribed', 'mention', 'comment']
    raws = [{**row(number=str(n)), 'id': str(100 + n), 'reason': reason} for n, reason in enumerate(reasons)]
    monkeypatch.setattr(inbox, 'metadata', lambda data: {'account': 'me', 'synced_at': None, 'ids': [r['id'] for r in raws],
                                                         'rows': {r['id']: r for r in raws}})
    monkeypatch.setattr(inbox, 'read_states', lambda keys: {k: 'open' for k in keys})
    rows = inbox.view(None, [], [])['notifications']
    assert len(rows) == len(reasons)
    assert {r['reason'] for r in rows if r['actionable']} == {'请你评审', '分配给你'}
    assert sum(r['actionable'] for r in rows) == 2


def thread(repo, number, kind='PullRequest', updated='2026-11-06T01:00:00Z', id=None):
    path = 'pulls' if kind == 'PullRequest' else 'issues'
    return {'id': id or str(number), 'unread': True, 'reason': 'author', 'updated_at': updated,
            'repository': {'full_name': repo},
            'subject': {'title': f'#{number}', 'type': kind, 'url': f'https://api.github.com/repos/{repo}/{path}/{number}',
                        'latest_comment_url': None}}


def test_subject_state_separates_merged_closed_completed_and_open():
    pr, issue = 'PullRequest', 'Issue'
    assert inbox.subject_state({'__typename': pr, 'state': 'MERGED', 'merged': True}) == 'merged'
    assert inbox.subject_state({'__typename': pr, 'state': 'CLOSED', 'merged': False}) == 'closed'
    assert inbox.subject_state({'__typename': pr, 'state': 'OPEN', 'merged': False}) == 'open'
    assert inbox.subject_state({'__typename': issue, 'state': 'CLOSED', 'stateReason': 'COMPLETED'}) == 'completed'
    assert inbox.subject_state({'__typename': issue, 'state': 'CLOSED', 'stateReason': 'NOT_PLANNED'}) == 'closed'
    assert inbox.subject_state({'__typename': issue, 'state': 'CLOSED', 'stateReason': 'DUPLICATE'}) == 'closed'
    assert inbox.subject_state({'__typename': issue, 'state': 'CLOSED', 'stateReason': None}) == 'closed'
    assert inbox.subject_state({'__typename': issue, 'state': 'OPEN', 'stateReason': 'REOPENED'}) == 'open'
    assert inbox.subject_state(None) == 'unknown'
    assert inbox.DONE_STATES == {'merged', 'completed'}


def test_only_issue_and_pull_request_threads_have_a_subject():
    assert inbox.subject_key(thread('Org/Repo', 7)) == 'org/repo#7'
    assert inbox.subject_key(thread('org/repo', 8, 'Issue')) == 'org/repo#8'
    check = thread('org/repo', 9); check['subject'].update(type='CheckSuite', url=None)
    assert inbox.subject_key(check) is None
    foreign = thread('org/repo', 9); foreign['subject']['url'] = 'https://api.github.com/repos/else/repo/pulls/9'
    assert inbox.subject_key(foreign) is None


def test_states_are_read_in_one_graphql_request_grouped_by_repository(monkeypatch):
    queries = []
    def graphql(query, kind, partial=False, **variables):
        queries.append((query, kind, partial))
        return {'r0': {'s3': {'__typename': 'Issue', 'state': 'CLOSED', 'stateReason': 'COMPLETED'},
                       's7275': {'__typename': 'PullRequest', 'state': 'MERGED', 'merged': True},
                       's7287': {'__typename': 'PullRequest', 'state': 'OPEN', 'merged': False}},
                'r1': None}
    monkeypatch.setattr(inbox.github, 'graphql', graphql)
    found = inbox.read_states(['codewhale-hq/codewhale#7275', 'codewhale-hq/codewhale#7287', 'codewhale-hq/codewhale#3', 'gone/repo#1'])
    assert found == {'codewhale-hq/codewhale#7275': 'merged', 'codewhale-hq/codewhale#7287': 'open',
                     'codewhale-hq/codewhale#3': 'completed', 'gone/repo#1': 'unknown'}
    assert len(queries) == 1 and queries[0][1] == 'graphql:states' and queries[0][2] is True
    assert queries[0][0].count('repository(') == 2 and queries[0][0].count('issueOrPullRequest') == 4


def test_states_are_chunked(monkeypatch):
    calls = []
    monkeypatch.setattr(inbox, 'STATE_CHUNK', 2)
    monkeypatch.setattr(inbox.github, 'graphql', lambda query, kind, partial=False: calls.append(query) or {})
    assert set(inbox.read_states([f'o/r#{n}' for n in range(5)]).values()) == {'unknown'}
    assert len(calls) == 3


def test_merged_is_cached_for_good_open_expires_and_closed_waits_for_new_activity(monkeypatch, tmp_path):
    reads, answer = [], {'o/r#1': 'merged', 'o/r#2': 'open', 'o/r#3': 'closed'}
    def read(keys):
        reads.append(sorted(keys))
        return {k: answer[k] for k in keys}
    rows = [thread('o/r', 1), thread('o/r', 2), thread('o/r', 3, 'Issue')]
    states, note = inbox.subject_states(tmp_path, rows, read=read)
    assert states == {'o/r#1': 'merged', 'o/r#2': 'open', 'o/r#3': 'closed'} and note == ''
    assert reads == [['o/r#1', 'o/r#2', 'o/r#3']]
    inbox.subject_states(tmp_path, rows, read=read)
    assert len(reads) == 1  # 都还新鲜，不发请求
    inbox._states['o/r#2']['at'] -= inbox.STATE_TTL + 1
    inbox._states['o/r#1']['at'] -= 10 ** 7
    inbox.subject_states(tmp_path, rows, read=read)
    assert reads[-1] == ['o/r#2']  # 只有开着的过期重查
    rows[2] = thread('o/r', 3, 'Issue', updated='2026-11-07T00:00:00Z')  # 关闭的有了新动态（可能重新打开）
    answer['o/r#3'] = 'open'
    states, _ = inbox.subject_states(tmp_path, rows, read=read)
    assert reads[-1] == ['o/r#3'] and states['o/r#3'] == 'open'
    # 重启后已合并的从 .personal 读回来，不再查 GitHub
    inbox._states.clear(); inbox._states_loaded.clear()
    saved = json.loads((tmp_path / inbox.STATES_FILE).read_text(encoding='utf-8'))
    assert saved['o/r#1']['state'] == 'merged' and 'o/r#2' not in saved
    states, _ = inbox.subject_states(tmp_path, rows[:1], read=read)
    assert states == {'o/r#1': 'merged'} and reads[-1] == ['o/r#3']


def test_rate_limited_sends_nothing_keeps_old_states_and_says_what_is_missing(monkeypatch):
    reads = []
    def read(keys):
        reads.append(keys)
        return {k: 'open' for k in keys}
    inbox.subject_states(None, [thread('o/r', 1)], read=read)
    inbox._states['o/r#1']['at'] -= inbox.STATE_TTL + 1
    github.strike('secondary rate limit')
    states, note = inbox.subject_states(None, [thread('o/r', 1), thread('o/r', 2)], read=read)
    assert len(reads) == 1  # 限流中没有再发请求
    assert states == {'o/r#1': 'open', 'o/r#2': 'unknown'}
    assert 'GitHub 限流中' in note and '1 条没读到' in note and '暂按未完成显示' in note


def test_failed_read_marks_unknown_and_waits_before_retrying(monkeypatch):
    calls = []
    def read(keys):
        calls.append(keys)
        raise RuntimeError('gh 未登录')
    states, note = inbox.subject_states(None, [thread('o/r', 1)], read=read)
    assert states == {'o/r#1': 'unknown'} and '1 条' in note and 'gh 未登录' in note
    states, note = inbox.subject_states(None, [thread('o/r', 1)], read=read)
    assert len(calls) == 1 and 'gh 未登录' in note


def test_view_marks_merged_and_completed_threads_done(monkeypatch):
    raws = [thread('o/r', 1, id='1'), thread('o/r', 2, id='2'), thread('o/r', 3, 'Issue', id='3'),
            thread('o/r', 4, 'Issue', id='4'), {**thread('o/r', 5, id='5'), 'subject': {'title': 'CI', 'type': 'CheckSuite', 'url': None}}]
    monkeypatch.setattr(inbox, 'metadata', lambda data: {'account': 'me', 'synced_at': None, 'ids': [r['id'] for r in raws],
                                                         'rows': {r['id']: r for r in raws}})
    answer = {'o/r#1': 'merged', 'o/r#2': 'closed', 'o/r#3': 'completed', 'o/r#4': 'open'}
    monkeypatch.setattr(inbox, 'read_states', lambda keys: {k: answer[k] for k in keys})
    report = inbox.view(None, [], [])
    rows = {r['id']: r for r in report['notifications']}
    assert {k: (v['state'], v['done']) for k, v in rows.items()} == {
        '1': ('merged', True), '2': ('closed', False), '3': ('completed', True), '4': ('open', False), '5': ('', False)}
    assert report['state_note'] == ''


def test_state_lookup_never_writes_to_github(monkeypatch):
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'data': {'r0': {'s1': {'__typename': 'PullRequest', 'state': 'MERGED', 'merged': True}}}}), stderr='')
    monkeypatch.setattr(github.subprocess, 'run', run)
    assert inbox.read_states(['o/r#1']) == {'o/r#1': 'merged'}
    assert commands[0][:3] == ['gh', 'api', 'graphql'] and 'mutation' not in ' '.join(commands[0])
    assert '--method' not in commands[0] and 'PATCH' not in commands[0]


def test_normalize_keeps_last_read_at_for_counting_new_comments():
    raw = dict(row(), last_read_at='2026-11-05T00:00:00Z')
    assert inbox.normalize(raw)['last_read_at'] == '2026-11-05T00:00:00Z'
    assert inbox.normalize(row())['last_read_at'] is None


def test_view_rows_carry_reason_code_number_and_new_replies_for_the_page(monkeypatch):
    from personal import pr_news
    pr_news.reset()
    raws = [{**thread('acme/orders', 4, 'Issue', id='1'), 'reason': 'mention', 'last_read_at': None},
            {**thread('else/repo', 5, id='2'), 'reason': 'review_requested', 'unread': False},
            {**thread('acme/old', 6, id='3')},
            {**thread('acme/orders', 7, id='4'), 'subject': {'title': 'CI', 'type': 'CheckSuite', 'url': None}}]
    known = [{'id': 'orders', 'name': '订单', 'group': '合作项目', 'github': 'ACME/Orders'},
             {'id': 'old', 'name': '旧', 'group': '合作项目', 'repository': 'acme/old', 'archived': True},
             {'id': 'else', 'name': '别的', 'group': '开源协作', 'github': 'else/repo'}]
    monkeypatch.setattr(inbox, 'metadata', lambda data: {'account': 'me', 'synced_at': None, 'ids': [r['id'] for r in raws],
                                                         'rows': {r['id']: r for r in raws}})
    monkeypatch.setattr(inbox, 'read_states', lambda keys: {k: 'open' for k in keys})
    monkeypatch.setattr(pr_news, 'read_threads', lambda keys: {k: {'comments': {'nodes': [
        {'author': {'login': 'boss'}, 'bodyText': '看一下', 'createdAt': '2026-11-06T00:30:00Z', 'url': ''}]}} for k in keys})
    try:
        rows = {r['id']: r for r in inbox.view(None, known[:1], known, summary=True)['notifications']}
    finally:
        pr_news.reset()
    assert {k: (r['reason_code'], r['number'], r['project_id']) for k, r in rows.items()} == {
        '1': ('mention', 4, 'orders'), '2': ('review_requested', 5, None), '3': ('author', 6, None), '4': ('author', None, 'orders')}
    assert all('intake_id' not in r for r in rows.values())
    assert rows['1']['new'] == 1  # 从没读过：别人留的都算
    assert 'new' not in rows['2'] and 'new' not in rows['4']  # 已读的、CI 的不标
    plain = {r['id']: r for r in inbox.view(None, known[:1], known)['notifications']}
    assert 'new' not in plain['1']  # 不带 summary 时不读


def legacy_db(path, rows, account='me'):
    """旧版任务库里的收件记录：表结构照旧版 intake_records 的列。"""
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("""CREATE TABLE intake_records (id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            sender_id TEXT NOT NULL, sender_name TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL, scope TEXT NOT NULL,
            source_message_id TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, status TEXT NOT NULL, assessment_json TEXT NOT NULL DEFAULT '{}',
            proposal_json TEXT NOT NULL DEFAULT '[]', history_json TEXT NOT NULL DEFAULT '[]', card_ids_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_response_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1)""")
        for sender, raw in rows:
            conn.execute("INSERT INTO intake_records (source_key, kind, sender_id, session_id, scope, source_message_id, text, status, "
                         "assessment_json, created_at, updated_at, last_response_at) VALUES (?, 'github_notification', ?, 'github:notifications', "
                         "'github:notifications', ?, 't', 'type_pending', ?, 'x', 'x', 'x')",
                         (f"{sender}:{raw['id']}", sender, raw['id'], json.dumps({'notification': raw, 'project_id': None})))
        conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY)")
        conn.commit()


def test_old_task_store_is_imported_once_read_only(tmp_path, monkeypatch):
    mine, other, theirs = {**row(), 'id': '100'}, {**row(number='8'), 'id': '101'}, {**row(number='9'), 'id': '102'}
    legacy_db(tmp_path / 'tasks.sqlite3', [('github:me', mine), ('github:me', other), ('github:someone', theirs)])
    before = (tmp_path / 'tasks.sqlite3').read_bytes()
    (tmp_path / 'github-notifications.json').write_text(json.dumps({'account': 'me', 'synced_at': '2026-11-06T02:00:00+08:00', 'ids': ['100', '102']}))
    monkeypatch.setattr(inbox, 'read_states', lambda keys: {k: 'open' for k in keys})
    rows = inbox.view(tmp_path, [{'id': 'p', 'name': 'P', 'repository': 'me/repo'}], [])['notifications']
    assert [(r['id'], r['project_id']) for r in rows] == [('100', 'p')]  # 只有这个账号的、还在上次同步里的
    stored = json.loads((tmp_path / 'github-notifications.json').read_text())
    assert sorted(stored['rows']) == ['100', '101'] and stored['ids'] == ['100', '102']
    assert (tmp_path / 'tasks.sqlite3').read_bytes() == before  # 任务库只读
    assert not (tmp_path / 'tasks.sqlite3-wal').exists() and not (tmp_path / 'tasks.sqlite3-shm').exists()
    (tmp_path / 'tasks.sqlite3').unlink()
    assert [r['id'] for r in inbox.view(tmp_path, [], [])['notifications']] == ['100']  # 导入过就不再读任务库


def test_migration_without_an_old_store_or_table_starts_empty(tmp_path):
    (tmp_path / 'github-notifications.json').write_text(json.dumps({'account': 'me', 'synced_at': None, 'ids': ['100']}))
    assert inbox.view(tmp_path, [], [])['notifications'] == []
    assert json.loads((tmp_path / 'github-notifications.json').read_text())['rows'] == {}
    import sqlite3
    other = tmp_path / 'other'; other.mkdir()
    sqlite3.connect(other / 'tasks.sqlite3').close()  # 有库没表
    (other / 'github-notifications.json').write_text(json.dumps({'account': 'me', 'synced_at': None, 'ids': ['100']}))
    assert inbox.metadata(other)['rows'] == {}
