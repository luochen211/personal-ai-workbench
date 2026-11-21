"""全部测试共用：不读真实数据目录里的本机覆盖文件 projects.local.toml，要用的测试自己往 tmp_path 写。"""
import os

import pytest

# 夹具里的「本人」账号固定写 luochen211：不看本机登记表、也不去问 gh，换台机器跑结果一样
os.environ["WORKBENCH_GITHUB_LOGIN"] = "luochen211"

from personal import projects_config


@pytest.fixture(autouse=True)
def no_local_projects(tmp_path, monkeypatch):
    path = tmp_path / "local-data" / projects_config.LOCAL_NAME
    monkeypatch.setattr(projects_config, "local_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def no_real_hub(tmp_path, monkeypatch):
    """测试一律不碰本机真的中台目录（rules.json 等）和微信抽取的游标、账号：都指到临时目录，要用的测试自己再换。"""
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "autoloop-home"))
    monkeypatch.setenv("WECHAT_ISSUES_STATE", str(tmp_path / "wechat-cursor.json"))
    monkeypatch.setenv("WECHAT_ISSUES_ACCOUNTS", str(tmp_path / "wechat-accounts.json"))
    from personal import __main__ as app
    monkeypatch.setattr(app, "DATA", tmp_path / "workbench-data")  # 默认数据目录也换掉：插件装没装不看本机真的
