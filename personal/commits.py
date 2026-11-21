"""仅按最近一个月的本人真实提交收敛项目范围。"""
from __future__ import annotations
import calendar
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import upstream

COMMIT_KINDS = {'本地本人提交', 'GitHub 本人提交', 'GitHub 本人 PR/issue'}


def month_before(now):
    year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    return now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))


def cutoff(now):
    return month_before(now).replace(hour=0, minute=0, second=0, microsecond=0)


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def active_ids(records, now=None):
    now = now or datetime.now().astimezone()
    return {pid for pid, row in records.items() if cutoff(now) <= instant(row.get('last_commit_at')) <= now}


def git(path, *args):
    result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, errors='replace', timeout=45)
    return result.stdout if result.returncode == 0 else ''


def local_evidence(project, since, actors, emails):
    if not project.get('repo_path'):
        return []
    path = Path(project['repo_path'])
    if not path.is_dir():
        return []
    logs = git(path, 'log', '--all', '--since-as-filter=' + since.isoformat(), '--format=%aI%x1f%ae%x1f%an%x1f%H')
    evidence = []
    now = datetime.now().astimezone()
    for line in logs.splitlines():
        fields = line.split('\x1f')
        if len(fields) == 4 and (fields[1].lower() in emails or fields[2].lower() in actors) and since <= instant(fields[0]) <= now:
            evidence.append({'at': fields[0], 'kind': '本地本人提交', 'commit': fields[3]})
    return evidence


def graphql(query, variables):
    from . import github
    try:
        return github.graphql(query, kind='graphql:commits', **variables)
    except github.RateLimited as exc:
        raise ValueError(f'GitHub 本人提交没读到：{exc}') from None
    except RuntimeError:
        raise ValueError('GitHub 本人提交读取失败，请检查 gh 登录与网络') from None


def github_evidence(login, since, until):
    query = '''query($login:String!,$from:DateTime!,$to:DateTime!) {
      user(login:$login) { contributionsCollection(from:$from,to:$to) {
        commitContributionsByRepository(maxRepositories:100) {
          repository { nameWithOwner } contributions(first:100) { nodes { occurredAt commitCount } }
        }
      } }
    }'''
    collection = graphql(query, {'login': login, 'from': since.isoformat(), 'to': until.isoformat()})['user']['contributionsCollection']
    return [{'repository': item['repository']['nameWithOwner'], 'at': node['occurredAt'], 'kind': 'GitHub 本人提交'}
            for item in collection['commitContributionsByRepository'] for node in item['contributions']['nodes']
            if node['commitCount'] > 0 and since <= instant(node['occurredAt']) <= until]


def authored_evidence(rows):
    """上游仓库里本人创建的 PR 和 issue 也算近月活动（开源协作往往没有本人提交）。"""
    return [{'repository': r['repository']['nameWithOwner'], 'at': r['createdAt'], 'kind': 'GitHub 本人 PR/issue'} for r in rows]


def combine(projects, local, remote):
    records = {}
    now = datetime.now().astimezone()
    for project in projects:
        aliases = {str(value).lower() for value in [project.get('repository'), project.get('github'), *project.get('commit_repositories', [])] if value}
        evidence = local.get(project['id'], []) + [r for r in remote if r['repository'].lower() in aliases]
        evidence = [row for row in evidence if row.get('kind') in COMMIT_KINDS and instant(row['at']) <= now]
        evidence.sort(key=lambda row: instant(row['at']), reverse=True)
        records[project['id']] = {'last_commit_at': evidence[0]['at'] if evidence else None, 'evidence': evidence[:5]}
    return records


def refresh(projects, data, now=None, authored=()):
    now = now or datetime.now().astimezone()
    since = cutoff(now)
    root = Path(__file__).resolve().parents[1]
    email = git(root, 'config', '--global', 'user.email').strip().lower()
    name = git(root, 'config', '--global', 'user.name').strip().lower()
    actors = {*upstream.OWN_ACCOUNTS, name} - {''}
    emails = {email, *upstream.OWN_EMAILS} - {''}
    with ThreadPoolExecutor(max_workers=6) as executor:
        local = dict(executor.map(lambda p: (p['id'], local_evidence(p, since, actors, emails)), projects))
    error = ''
    try:
        remote = github_evidence(upstream.LOGIN, since, now)
    except (ValueError, subprocess.TimeoutExpired) as exc:
        remote = []; error = str(exc)
    report = {'checked_at': now.isoformat(), 'since': since.isoformat(), 'github_error': error,
              'records': combine(projects, local, remote + authored_evidence(authored))}
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=data, prefix='commits-', suffix='.tmp', delete=False) as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    temporary.replace(data / 'commits.json')
    return report


def load(data):
    path = data / 'commits.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'records': {}}
