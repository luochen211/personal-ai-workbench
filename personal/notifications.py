"""GitHub 通知只读接入：同步结果存在本机数据目录的 github-notifications.json，按可信仓库映射归类。

旧版把通知存在本机任务库 tasks.sqlite3 的 intake_records 表里；第一次读到没有 rows 的同步结果时，
从那张表只读导入同一账号的通知原文（migrate），之后不再读任务库。"""
from __future__ import annotations
import json
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from .errors import APIError
from . import activity, github

LOCK = threading.RLock()
STORE = 'github-notifications.json'  # {account, synced_at, ids, rows: {线程编号: 通知原文}}
LEGACY_DB = 'tasks.sqlite3'  # 旧版的本机任务库，只在迁移时只读打开
LEGACY_KIND = 'github_notification'
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
REASONS = {'mention': '提到你', 'team_mention': '提到你的团队', 'review_requested': '请你评审',
           'assign': '分配给你', 'author': '你发起的事项', 'comment': '参与讨论', 'ci_activity': 'CI 检查',
           'state_change': '状态变化', 'subscribed': '订阅更新', 'manual': '手动订阅', 'security_alert': '安全提醒'}
# 只有这两种要本人动手；CI、自己 PR 的更新、提到、评论、订阅、状态变化都不算待处理。
ACTION_REASONS = {'review_requested', 'assign'}
# 主题状态：merged 已合并的 PR；completed 以「已完成」关闭的 issue；closed 其他关闭（未合并的 PR、不做或重复的 issue）；
# open 开着；unknown 没读到。前两种算消息已完成。
DONE_STATES = {'merged', 'completed'}
FINAL_STATES = {'merged', 'closed', 'completed'}
STATE_TTL = 300  # 开着的（和没读到的）5 分钟后重查
STATE_CHUNK = 100  # 一个 GraphQL 请求最多查这么多条主题
STATES_FILE = 'github-subject-states.json'  # 已合并、已关闭的状态长期保存在这里
SUBJECT = re.compile(r'repos/([^/]+/[^/]+)/(?:issues|pulls)/(\d+)')
_states_lock = threading.Lock()
_states = {}  # 'owner/repo#N'（小写）→ {'state', 'at', 'updated_at'}
_states_loaded = set()


def github_get(path, *, paginated=False, raise_limited=False):
    """raise_limited 为真时限流原样抛出 github.RateLimited，交给 github.cached 保留旧值。"""
    command = ['gh', 'api', '--method', 'GET']
    if paginated:
        command += ['--paginate', '--slurp']
    command += [path]
    try:
        return json.loads(github.run(command, 'rest:notifications'))
    except github.RateLimited as exc:
        if raise_limited:
            raise
        raise APIError(429, f'{exc}；先显示上次同步的结果') from None
    except (RuntimeError, subprocess.TimeoutExpired):
        raise APIError(502, 'GitHub 通知读取失败，请检查 gh 登录、网络和通知读取权限') from None


def fetch_notifications():
    pages = github_get('notifications?all=true&per_page=100', paginated=True)
    return [row for page in pages for row in page]


def normalize(row):
    repository = str(row.get('repository', {}).get('full_name') or '')
    if not REPO.fullmatch(repository) or not str(row.get('id', '')).isdigit():
        raise ValueError('GitHub 通知缺少有效仓库或线程编号')
    subject = row.get('subject') or {}
    return {'id': str(row['id']), 'repository': {'full_name': repository}, 'unread': bool(row.get('unread')),
            'reason': str(row.get('reason') or ''), 'updated_at': row.get('updated_at'),
            'last_read_at': row.get('last_read_at'),
            'subject': {key: subject.get(key) for key in ('title', 'type', 'url', 'latest_comment_url')}}


def classify(row, active, known):
    repo = row['repository']['full_name'].lower()
    def matches(projects, exact):
        return [p for p in projects if repo in {str(v).lower() for v in
                ([p.get('repository'), p.get('github')] if exact else p.get('commit_repositories', [])) if v}]
    candidates = matches(active, True) or matches(active, False)
    if len(candidates) == 1:
        project = candidates[0]
        return {'classification': 'workspace', 'project_id': project['id'], 'project_name': project['name'], 'candidates': []}
    if len(candidates) > 1:
        return {'classification': 'ambiguous', 'project_id': None, 'project_name': None,
                'candidates': [{'id': p['id'], 'name': p['name']} for p in candidates]}
    paused = matches(known, True) or matches(known, False)
    # 归档的项目（archived = true）和暂缓的一样不归到当前工作台，放进「其他消息」，只是写明已归档
    kind = 'archived' if paused and all(p.get('archived') is True for p in paused) else 'paused' if paused else 'other'
    return {'classification': kind, 'project_id': None,
            'project_name': paused[0]['name'] if len(paused) == 1 else None, 'candidates': []}


def resource_path(url, repo):
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'api.github.com' or parsed.query or parsed.fragment:
        return None
    prefix = '/repos/' + repo + '/'
    if not parsed.path.lower().startswith(prefix.lower()):
        return None
    resource = parsed.path[len(prefix):]
    if not re.fullmatch(r'(?:issues/\d+|pulls/\d+|issues/comments/\d+|pulls/comments/\d+|pulls/\d+/reviews/\d+|commits/[0-9a-fA-F]+|releases/\d+|check-suites/\d+)', resource):
        return None
    return parsed.path.lstrip('/')


def subject_link(row):
    repo = row['repository']['full_name']
    path = resource_path(row['subject'].get('url'), repo)
    if path:
        resource = path.split('/', 3)[3]
        if re.fullmatch(r'pulls/\d+', resource):
            return f'https://github.com/{repo}/' + resource.replace('pulls/', 'pull/')
        if re.fullmatch(r'issues/\d+', resource):
            return f'https://github.com/{repo}/{resource}'
        if resource.startswith('commits/'):
            return f'https://github.com/{repo}/' + resource.replace('commits/', 'commit/')
    suffix = '/actions' if row['subject'].get('type') == 'CheckSuite' else '/releases' if row['subject'].get('type') == 'Release' else ''
    return f'https://github.com/{repo}{suffix}'


def category(row):
    reason, kind = row['reason'], row['subject']['type']
    if reason == 'review_requested':
        return '评审请求'
    if reason in {'mention', 'team_mention', 'assign'}:
        return '与你有关'
    return {'CheckSuite': 'CI 检查', 'PullRequest': 'PR 更新', 'Issue': 'Issue 更新', 'Discussion': '讨论'}.get(kind, '其他消息')


def _write(data, report):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=data, prefix='github-notifications-', suffix='.tmp', delete=False) as stream:
        json.dump(report, stream, ensure_ascii=False)
        temporary = stream.name
    Path(temporary).replace(Path(data) / STORE)


def migrate(data, report):
    """从旧版任务库（tasks.sqlite3 的 intake_records）只读导入这个账号的通知原文，返回 {线程编号: 通知}。
    没有任务库、没有那张表或没有记录时是空的；读失败返回 None（下次再试），不写任务库。"""
    db = Path(data) / LEGACY_DB
    account = report.get('account')
    if not account or not db.exists():
        return {}
    try:
        # 只读打开；旁边没有 -wal 时再加 immutable，不在旧数据目录里生成 -shm、-wal
        flags = '?mode=ro' + ('' if db.with_name(db.name + '-wal').exists() else '&immutable=1')
        with closing(sqlite3.connect(db.resolve().as_uri() + flags, uri=True)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'intake_records'").fetchone():
                return {}
            found = conn.execute('SELECT source_message_id, assessment_json FROM intake_records WHERE kind = ? AND sender_id = ?',
                                 (LEGACY_KIND, 'github:' + account)).fetchall()
    except sqlite3.Error:
        return None
    rows = {}
    for number, text in found:
        try:
            raw = json.loads(text or '{}').get('notification')
        except (ValueError, AttributeError):
            continue
        if isinstance(raw, dict) and str(raw.get('id')) == str(number):
            rows[str(number)] = raw
    return rows


def metadata(data):
    """上次同步的结果：{account, synced_at, ids, rows}。旧版没有 rows：第一次读时从旧任务库导入并写回（只做一次）。"""
    path = Path(data) / STORE
    if not path.exists():
        return {'account': None, 'synced_at': None, 'ids': [], 'rows': {}}
    report = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(report.get('rows'), dict):
        with LOCK:
            rows = migrate(data, report)
            report['rows'] = rows or {}
            if rows is not None:
                _write(data, report)
    return report


def stored(report):
    """[(线程编号, 通知原文)]：只取上次同步里还在的（ids）。"""
    rows = report.get('rows') or {}
    return [(number, rows[number]) for number in report.get('ids') or [] if isinstance(rows.get(number), dict)]


def sync(data, active, known):
    with LOCK:
        account = github_get('user')['login']
        rows = {str(row['id']): normalize(row) for row in fetch_notifications()}
        before = metadata(data)
        old = before['rows'] if before.get('account') == account else {}
        created = sum(number not in old for number in rows)
        updated = sum(number in old and old[number] != row for number, row in rows.items())
        _write(data, {'account': account, 'synced_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                      'ids': list(rows), 'rows': rows})
    renew(rows, old, active)
    return {**view(data, active, known, summary=True), 'created': created, 'updated': updated}


def renew(rows, old, active, *, refresh=None):
    """这次同步里新来的或更新了的信号通知（未读，提到你、请你评审、你参与或你发起的，见 personal/fresh.py），登记项目里的：
    马上在后台单独重读这几条主题，不等 issue 和 PR 列表下一次整表刷新。返回交给重读的主题。"""
    from . import fresh
    wanted = {}
    for number, row in rows.items():
        key = subject_key(row)
        if not key or not row.get('unread') or row.get('reason') not in fresh.SIGNALS or old.get(number) == row:
            continue
        if classify(row, active, []).get('classification') != 'workspace':
            continue
        wanted[key] = max(wanted.get(key) or '', row.get('updated_at') or '')
    return (refresh or fresh.refresh)(wanted) if wanted else []


def subject_key(row):
    """issue 或 PR 通知的主题：'owner/repo#N'（小写）；其他类型（CI、发布……）没有主题状态，返回 None。"""
    if row['subject'].get('type') not in {'Issue', 'PullRequest'}:
        return None
    path = resource_path(row['subject'].get('url'), row['repository']['full_name'])
    found = SUBJECT.fullmatch(path or '')
    return f'{found[1].lower()}#{int(found[2])}' if found else None


def subject_state(node):
    """GraphQL issueOrPullRequest 节点 → merged / closed / completed / open；读不到是 unknown。"""
    node = node or {}
    if node.get('__typename') == 'PullRequest':
        return 'merged' if node.get('merged') or node.get('state') == 'MERGED' else 'closed' if node.get('state') == 'CLOSED' else 'open'
    if node.get('__typename') == 'Issue':
        if node.get('state') != 'CLOSED':
            return 'open'
        return 'completed' if node.get('stateReason') == 'COMPLETED' else 'closed'
    return 'unknown'


def read_states(keys):
    """一个 GraphQL 请求查最多 STATE_CHUNK 条主题（按仓库分组），返回 {key: 状态}。限流时抛 github.RateLimited，
    之后的分组不再发请求；仓库或编号读不到（删了、没权限）就是 unknown，不影响同一请求里的其他主题。"""
    pairs = sorted((key.split('#')[0], int(key.split('#')[1])) for key in keys)
    out = {}
    for start in range(0, len(pairs), STATE_CHUNK):
        repos = {}
        for repo, number in pairs[start:start + STATE_CHUNK]:
            repos.setdefault(repo, []).append(number)
        fields = ' '.join(
            f'r{i}: repository(owner: {json.dumps(repo.split("/")[0])}, name: {json.dumps(repo.split("/")[1])}) {{ '
            + ' '.join(f's{n}: issueOrPullRequest(number: {n}) {{ __typename ... on PullRequest {{ state merged }} '
                       '... on Issue { state stateReason } }' for n in numbers) + ' }'
            for i, (repo, numbers) in enumerate(repos.items()))
        data = github.graphql(f'query {{ {fields} }}', kind='graphql:states', partial=True)
        for i, (repo, numbers) in enumerate(repos.items()):
            found = data.get(f'r{i}') or {}
            for n in numbers:
                out[f'{repo}#{n}'] = subject_state(found.get(f's{n}'))
    return out


def _load_states(data):
    if data is None or str(data) in _states_loaded:
        return
    _states_loaded.add(str(data))
    try:
        saved = json.loads((Path(data) / STATES_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    for key, entry in saved.items():
        if isinstance(entry, dict) and entry.get('state') in {'merged', 'closed', 'completed'}:
            _states.setdefault(key, entry)


def _save_states(data):
    if data is None:
        return
    final = {k: v for k, v in _states.items() if v['state'] in {'merged', 'closed', 'completed'}}
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=data, prefix='github-subject-states-', suffix='.tmp', delete=False) as stream:
        json.dump(final, stream, ensure_ascii=False)
        temporary = stream.name
    Path(temporary).replace(Path(data) / STATES_FILE)


def closed_subjects(data=None):
    """消息里已经看到关闭了的 issue 和 PR：'owner/repo#N'（小写）→ 看到关闭的时刻（UTC）。只看已有状态，不发请求。"""
    with _states_lock:
        _load_states(data)
        return {key: datetime.fromtimestamp(entry['at'], timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                for key, entry in _states.items() if entry.get('state') in {'closed', 'completed'} and entry.get('at')}


def _fresh(entry, updated_at, now):
    """已合并的永远不变；已关闭的（可能被重新打开）在通知没有新动态前不重查；开着的和没读到的 5 分钟后重查。"""
    if not entry:
        return False
    if entry['state'] == 'merged':
        return True
    if entry['state'] in {'closed', 'completed'}:
        return (updated_at or '') <= (entry.get('updated_at') or '')
    return now - entry['at'] < STATE_TTL


def subject_states(data, raws, *, read=None):
    """给通知补上主题状态：先用缓存，过期或没有的一次批量查。返回 ({key: 状态}, 说明)；
    限流时不发请求，用旧状态，没有旧状态的算 unknown，说明里写清几条没读到。"""
    wanted = {}
    for raw in raws:
        key = subject_key(raw)
        if key:
            wanted[key] = max(wanted.get(key) or '', raw.get('updated_at') or '')
    with _states_lock:
        _load_states(data)
        now = time.time()
        stale = [key for key, updated in wanted.items() if not _fresh(_states.get(key), updated, now)]
        error = ''
        if stale:
            try:
                github.check()
                found = (read or read_states)(stale)
            except github.RateLimited:
                found = {}
                error = 'limited'
            except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
                # 读失败：没有旧状态的记成 unknown，5 分钟内不再重试；有旧状态的保留旧的
                found = {key: 'unknown' for key in stale if key not in _states}
                error = str(exc)[:200]
            finished = set()
            for key, state in found.items():
                if state in FINAL_STATES and (_states.get(key) or {}).get('state') not in FINAL_STATES:
                    finished.add(key.split('#')[0])
                _states[key] = {'state': state, 'at': now, 'updated_at': wanted[key], **({'error': error} if error else {})}
            for repo in sorted(finished):  # 有 PR 合并或 issue 关闭：这个项目的 issue 和 PR 下次请求时重读
                activity.invalidate(repo)
            if found and any(state in {'merged', 'closed', 'completed'} for state in found.values()):
                try:
                    _save_states(data)
                except OSError:
                    pass
        states = {key: (_states.get(key) or {}).get('state', 'unknown') for key in wanted}
        error = error or next((_states[k].get('error') for k, v in states.items() if v == 'unknown' and k in _states
                               and _states[k].get('error')), '')
    missing = sum(state == 'unknown' for state in states.values())
    note = ''
    if error == 'limited':
        until = github.blocked_until()
        later = f'，约 {max(1, round((until - time.time()) / 60))} 分钟后重试' if until else ''
        note = f'GitHub 限流中，{len(stale)} 条 PR / issue 的状态没更新，其中 {missing} 条没读到，暂按未完成显示{later}'
    elif missing:
        note = f'{missing} 条 PR / issue 的状态没读到，暂按未完成显示' + (f'：{error}' if error else '（仓库可能已删除或没有权限）')
    return states, note


def view(data, active, known, *, summary=False):
    """summary 为真时加上 digest（各项目未读条数和最近一条是谁说了什么，见 pr_news.digest），
    并给未读的 issue / PR 行补上 new（上次读过之后别人新留的条数，不明时为 None）。
    每行还带 reason_code（GitHub 原始原因，给页面按原因筛）、number（issue / PR 编号）。"""
    report = metadata(data)
    raws = [raw for _, raw in stored(report)]
    states, note = subject_states(data, raws)
    rows, pairs = [], []
    for raw in raws:
        match = classify(raw, active, known)
        key = subject_key(raw)
        state = states.get(key, '') if key else ''
        url = subject_link(raw)
        rows.append({'id': raw['id'], 'repository': raw['repository']['full_name'],
            'title': raw['subject'].get('title') or 'GitHub 通知', 'type': raw['subject'].get('type') or 'Unknown',
            'number': int(key.split('#')[1]) if key else None,
            'category': category(raw), 'reason': REASONS.get(raw['reason'], raw['reason'] or '通知'), 'reason_code': raw['reason'],
            'unread': raw['unread'], 'actionable': raw['reason'] in ACTION_REASONS,
            'updated_at': raw['updated_at'], 'url': url, 'state': state, 'done': state in DONE_STATES, **match})
        pairs.append((rows[-1], raw))
    rows.sort(key=lambda row: row['updated_at'] or '', reverse=True)
    out = {'account': report['account'], 'synced_at': report['synced_at'], 'notifications': rows, 'state_note': note}
    if summary:
        from . import pr_news
        out['digest'] = pr_news.digest(pairs)
        for row, raw in pairs:
            found = pr_news.cached(raw)
            if found:
                row['new'] = found.get('count')
    return out


def project_threads(data, active, known, project_id):
    """这个项目的 issue 和 PR 通知（原始通知，含已读的）：只读本机同步结果，不发 GitHub 请求。
    给项目详情页的「我的 PR」对新消息和最近动态用。"""
    out = [raw for _, raw in stored(metadata(data))
           if subject_key(raw) and classify(raw, active, known).get('project_id') == project_id]
    return sorted(out, key=lambda raw: raw.get('updated_at') or '', reverse=True)


def read_content(row):
    """读通知对应的正文（issue、PR、提交、发布……的描述）和评论，正文和评论分开，评论按时间排。不标记已读。
    限流时抛 github.RateLimited，不把限流当成读到的内容缓存起来。"""
    repo, subject = row['repository']['full_name'], row['subject']
    path = resource_path(subject.get('url'), repo)
    if not path:
        return {'body': '', 'author': None, 'url': subject_link(row), 'content_kind': 'summary', 'comments': [],
                'body_error': '该类型通知未提供可读取的正文，请查看 GitHub 原文'}
    try:
        result = github_get(path, raise_limited=True)
    except APIError as exc:
        return {'body': '', 'author': None, 'url': subject_link(row), 'content_kind': 'summary', 'comments': [], 'body_error': str(exc)}
    link = result.get('html_url') or subject_link(row)
    if urlsplit(link).scheme != 'https' or urlsplit(link).netloc != 'github.com':
        link = subject_link(row)
    comments, comment_error = [], None
    thread = re.fullmatch(r'repos/[^/]+/[^/]+/(?:issues|pulls)/(\d+)', path)
    if thread:
        try:
            listed = github_get(f'repos/{repo}/issues/{thread[1]}/comments?per_page=100', raise_limited=True)
            comments = sorted(({'author': (c.get('user') or {}).get('login', ''), 'body': c.get('body') or '',
                                'created_at': c.get('created_at') or ''} for c in listed), key=lambda c: c['created_at'])[-30:]
        except APIError as exc:
            comment_error = str(exc)
    kind = 'pull' if '/pulls/' in path else 'issue' if '/issues/' in path else 'description'
    return {'body': result.get('body') or result.get('output', {}).get('summary') or '',
            'author': (result.get('user') or result.get('author') or {}).get('login'), 'url': link,
            'created_at': result.get('created_at'), 'updated_at': result.get('updated_at'), 'content_kind': kind,
            'comments': comments, 'comment_error': comment_error, 'body_error': None}


def detail(data, number, active, known):
    report = view(data, active, known)
    item = next((r for r in report['notifications'] if r['id'] == number), None)
    if not item:
        raise APIError(404, '通知不在本次同步结果中，请重新同步')
    raw = dict(stored(metadata(data)))[number]
    content = github.cached(('notification', number, raw.get('updated_at')), lambda: read_content(raw), 300)
    if content['value'] is None:
        raise APIError(429 if content['limited_until'] else 502, github.describe(content)['message'] or 'GitHub 读取失败')
    return {'notification': item, **content['value']}
