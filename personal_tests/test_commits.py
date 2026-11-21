"""只认本人真实提交，防止评论、会话、切分支或代别人提交混入项目范围。"""
from datetime import datetime, timezone
import os
import subprocess
from personal import commits


def test_calendar_month_window_handles_short_month():
    assert commits.month_before(datetime(2026, 3, 31, tzinfo=timezone.utc)).isoformat().startswith('2026-02-28')


def test_scope_uses_commit_dates_and_ignores_old_activity_cache():
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    records = {'new': {'last_commit_at': '2026-10-30T00:00:00Z'},
               'old': {'last_commit_at': '2026-10-01T00:00:00Z'},
               'future': {'last_commit_at': '2027-01-01T00:00:00Z'},
               'session': {'last_activity_at': '2026-11-04T00:00:00Z'}}
    assert commits.active_ids(records, now) == {'new'}


def test_local_commits_require_author_and_recent_author_date(tmp_path):
    def run(*args, env=None):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], env=env, stderr=subprocess.DEVNULL, text=True).strip()
    run('init')
    base = {**os.environ, 'GIT_COMMITTER_NAME': 'me', 'GIT_COMMITTER_EMAIL': 'me@example.test', 'GIT_COMMITTER_DATE': '2026-11-04T00:00:00Z'}
    def commit(name, email, when):
        run('commit', '--allow-empty', '-m', 'change', env={**base, 'GIT_AUTHOR_NAME': name, 'GIT_AUTHOR_EMAIL': email, 'GIT_AUTHOR_DATE': when})
        return run('rev-parse', 'HEAD')
    commit('someone', 'someone@example.test', '2026-11-03T00:00:00Z')
    own = commit('me', 'me@example.test', '2026-11-02T00:00:00Z')
    commit('me', 'me@example.test', '2026-09-01T00:00:00Z')
    evidence = commits.local_evidence({'repo_path': str(tmp_path)}, datetime(2026, 10, 5, tzinfo=timezone.utc), {'me'}, {'me@example.test'})
    assert [row['commit'] for row in evidence] == [own]


def test_github_query_only_reads_commit_contributions(monkeypatch):
    queries = []
    def query(text, variables):
        queries.append(text)
        return {'user': {'contributionsCollection': {'commitContributionsByRepository': [{'repository': {'nameWithOwner': 'org/repo'}, 'contributions': {'nodes': [{'occurredAt': '2026-11-01T00:00:00Z', 'commitCount': 2}]}}]}}}
    monkeypatch.setattr(commits, 'graphql', query)
    rows = commits.github_evidence('me', datetime(2026, 10, 5, tzinfo=timezone.utc), datetime(2026, 11, 5, tzinfo=timezone.utc))
    assert len(rows) == 1 and rows[0]['kind'] == 'GitHub 本人提交'
    assert len(queries) == 1
    assert all(name not in queries[0] for name in ('issueComments', 'pullRequestContributions', 'pullRequestReviewContributions'))


def test_fork_alias_matches_real_commit_repository():
    project = {'id': 'one', 'repository': 'me/fork', 'github': '', 'commit_repositories': ['org/project']}
    remote = [{'repository': 'org/project', 'at': '2026-11-01T00:00:00Z', 'kind': 'GitHub 本人提交'}]
    assert commits.combine([project], {}, remote)['one']['last_commit_at'] == remote[0]['at']
