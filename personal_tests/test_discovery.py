import re
import subprocess
import tomllib
from pathlib import Path

from personal import commits, discovery


def row(repo, at="2026-11-11T00:00:00Z"):
    return {"repository": {"nameWithOwner": repo}, "createdAt": at}


def git_repo(path, **remotes):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for name, url in remotes.items():
        subprocess.run(["git", "-C", str(path), "remote", "add", name, url], check=True)


def test_local_clones_match_by_remote_and_skip_worktrees(tmp_path):
    git_repo(tmp_path / "Codewhale", origin="https://github.com/codewhale-hq/Codewhale.git", fork="git@github.com:luochen211/Codewhale.git")
    (tmp_path / "Codewhale-wt/feature").mkdir(parents=True)
    (tmp_path / "Codewhale-wt/feature/.git").write_text("gitdir: /elsewhere")  # worktree：.git 是文件
    git_repo(tmp_path / "deep/a/b/far", origin="https://github.com/far/away")
    clones = discovery.local_clones([{"path": str(tmp_path), "depth": 2}])
    assert clones["codewhale-hq/codewhale"][0] == tmp_path / "Codewhale"
    assert "far/away" not in clones and len(clones) == 2  # 上游和 fork 两个名字指向同一检出


def test_plan_adds_only_unregistered_upstream_repos(tmp_path):
    projects = [{"id": "careerops", "repository": "luochen211/career-ops", "commit_repositories": ["career-ops-hq/career-ops"]},
                {"id": "codewhale", "repository": "x/y"}]
    rows = [row("codewhale-hq/Codewhale"), row("codewhale-hq/Codewhale"), row("career-ops-hq/career-ops"),
            row("luochen211/own"), row("vinhnx/VTCode")]
    clones = {"codewhale-hq/codewhale": (tmp_path / "Codewhale", {"origin": "codewhale-hq/Codewhale", "fork": "luochen211/Codewhale"})}
    entries = discovery.plan(rows, projects, clones)
    assert [(e["id"], e["github"], e["repository"], e["repo_path"]) for e in entries] == [
        ("codewhale2", "codewhale-hq/Codewhale", "luochen211/Codewhale", str(tmp_path / "Codewhale")),
        ("vtcode", "vinhnx/VTCode", "vinhnx/VTCode", "")]
    assert all(e["group"] == "开源协作" and e["issue_scope"] == "mine" for e in entries)


def test_slug_fits_the_project_id_pattern():
    PROJECT_ID_PATTERN = discovery.PROJECT_ID_PATTERN
    taken = set()
    names = [discovery.slug(repo, taken) for repo in ("a/awesome-vibe-coding-resources", "b/free-programming-books",
                                                       "c/free-programming-books-zh_CN", "d/free-programming-books", "e/30-days", "f/9")]
    assert names == ["awesomevibecodin", "freeprogrammingb", "freeprogramming2", "freeprogramming3", "days", "upstream"]
    assert all(re.fullmatch(PROJECT_ID_PATTERN, name) for name in names)


def test_registered_project_ids_fit_the_project_id_pattern():
    """工作台启动时逐个登记 projects.toml 的项目，有一个标识不合规就起不来（11-17 的 awesomevibecodingresourc）。"""
    PROJECT_ID_PATTERN = discovery.PROJECT_ID_PATTERN
    projects = tomllib.loads((Path(__file__).resolve().parents[1] / "personal/projects.toml").read_text(encoding="utf-8")).get("projects", [])
    assert [p["id"] for p in projects if not re.fullmatch(PROJECT_ID_PATTERN, str(p.get("id", "")))] == []

def test_append_keeps_the_registry_valid(tmp_path):
    config = tmp_path / "projects.toml"
    config.write_text('[[projects]]\nid = "a"\nname = "A"\nrepo_path = "/a"\n')
    discovery.append(config, discovery.plan([row("vinhnx/VTCode")], [{"id": "a"}], {}), today="2026-11-12")
    projects = tomllib.loads(config.read_text())["projects"]
    assert [p["id"] for p in projects] == ["a", "vtcode"] and projects[1]["repo_path"] == ""
    assert discovery.roots(config) == discovery.DEFAULT_ROOTS


def test_upstream_prs_and_issues_count_as_recent_activity():
    projects = [{"id": "careerops", "repository": "luochen211/career-ops", "commit_repositories": ["career-ops-hq/career-ops"]},
                {"id": "vtcode", "repository": "vinhnx/VTCode", "github": "vinhnx/VTCode", "repo_path": ""}]
    evidence = commits.authored_evidence([row("career-ops-hq/career-ops"), row("vinhnx/VTCode")])
    records = commits.combine(projects, {}, evidence)
    assert records["careerops"]["last_commit_at"] and records["vtcode"]["evidence"][0]["kind"] == "GitHub 本人 PR/issue"
    assert commits.local_evidence({"id": "vtcode", "repo_path": ""}, None, set(), set()) == []


def test_skip_owners_keeps_listed_accounts_out_of_the_registry(tmp_path):
    config = tmp_path / "projects.toml"
    config.write_text('[discovery]\nskip_owners = ["Client-Org"]\n\n[[projects]]\nid = "a"\nname = "A"\nrepo_path = "/a"\n')
    assert discovery.skip_owners(config) == {"client-org"}
    entries = discovery.plan([row("client-org/app"), row("vinhnx/VTCode")], [{"id": "a"}], {}, discovery.skip_owners(config))
    assert [e["github"] for e in entries] == ["vinhnx/VTCode"]


def test_append_creates_the_local_registry_when_missing(tmp_path):
    """自动发现写进数据目录的 projects.local.toml：文件还没有就新建，不写进仓库里的登记表。"""
    target = tmp_path / "data" / "projects.local.toml"
    discovery.append(target, [{"id": "vtcode", "name": "VTCode", "group": "开源协作", "repo_path": "",
                               "repository": "vinhnx/VTCode", "github": "vinhnx/VTCode", "issue_scope": "mine",
                               "source_status": "上游 · 只看与我有关"}], today="2026-11-21")
    assert tomllib.loads(target.read_text(encoding="utf-8"))["projects"][0]["github"] == "vinhnx/VTCode"
