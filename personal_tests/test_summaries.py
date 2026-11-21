from personal import summaries


def test_order_is_toml_then_github_then_readme(tmp_path):
    (tmp_path / "README.md").write_text(
        "# 标题\n\n[![CI](https://x/badge.svg)](https://x)\n\nEnglish | [中文](README.zh.md)\n\n"
        "快速开始 · 现在能做什么 · 当前进展\n\n这是一个**示例**项目，用来[演示](https://x)简介。\n第二行。\n\n下一段不要。\n")
    project = {"repo_path": str(tmp_path), "repository": "o/r"}
    assert summaries.summarize({**project, "summary": "手写的"}, {"o/r": "仓库描述"}) == ("手写的", "projects.toml")
    assert summaries.summarize(project, {"o/r": "仓库描述"}) == ("仓库描述", "GitHub")
    assert summaries.summarize(project, {"o/r": ""}) == ("这是一个示例项目，用来演示简介。 第二行。", "README")
    assert summaries.summarize({"repo_path": str(tmp_path / "none")}, {}) == ("", "")


def test_readme_is_clipped_and_front_matter_skipped(tmp_path):
    (tmp_path / "README.md").write_text("---\ntitle: x\n---\n\n```sh\nnpm i\n```\n\n" + "长" * 100 + "\n")
    text = summaries.readme_summary(tmp_path)
    assert len(text) == 60 and text.endswith("…")


def test_descriptions_are_cached_for_a_day(tmp_path):
    calls = []
    fetch = lambda repos: calls.append(repos) or {r: "描述" for r in repos}
    assert summaries.descriptions(tmp_path, ["O/R"], fetch=fetch, now=1000) == {"o/r": "描述"}
    summaries.descriptions(tmp_path, ["o/r"], fetch=fetch, now=2000)
    assert len(calls) == 1
    summaries.descriptions(tmp_path, ["o/r", "a/b"], fetch=fetch, now=3000)  # 新仓库要重查
    summaries.descriptions(tmp_path, ["o/r", "a/b"], fetch=fetch, now=3000 + summaries.CACHE_SECONDS + 1)
    assert len(calls) == 3
