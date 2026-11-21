"""工作台插件（用户 2026-11-21 定）：一整套可选功能装上才能用，去掉就停用；页面上的按钮和位置不因插件变。

插件放在本目录下，一个子目录一个，清单是 plugin.json（见 _manifests）。核心经 route、hook、start_threads 调插件。
现在只有一个插件 wechat（微信）：收件箱的「微信」范围、后台刷新微信明文库、「新需求」里的微信来源、会话登记（map/unmap/mappings）。
没装时这些入口全部停用：GET /api/wechat 只回 {enabled: false}（页面就不出现微信），写接口拒绝，后台刷新不干活，
「读新需求」不刷新微信库、抽取脚本跳过微信只读录音。录音、草稿抽屉本身不归插件管。

登记表是数据目录里的 plugins.json：{"wechat": {"installed": true, "installed_at": "..."}}。
还没有登记表时按老数据推断：数据目录里有 wechat.json 就当微信已装，老用户升级后照旧；装过或去掉过一次就只认登记表。

命令（在哪个目录运行都行）：
    python3 -m personal.plugins list
    python3 -m personal.plugins install wechat --vault-dir <读取工具目录> --python <它要用的 python>
        # wechat.json 已经有了就不用带这两个参数，也不会改它
    python3 -m personal.plugins uninstall wechat    # 只停用，数据都留着，重新 install 就回来
    python3 -m personal.plugins wechat map --chat <群名> --project <项目 id>   # 插件自己的命令（装了才能用）
工作台在跑时不用重启：每个入口每次用之前都查登记表。
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REGISTRY = "plugins.json"
HERE = Path(__file__).resolve().parent


def _manifests():
    """插件目录下每个带 plugin.json 的子目录是一个插件。清单：id、name、legacy（老数据的标志文件，没有登记表时它在就当已装）、
    routes（[{method, path, call, open}]，open 的路由没装时也接，由插件自己回「没启用」）、threads（后台线程的启动函数）、
    hooks（核心在固定时机调的函数）、command（python3 -m personal.plugins <id> ... 转给它）、
    needs_sources（「读新需求」的来源：[{id, event, rule, subject}]）、
    autoloop（装上时加进中台 rules.json 的 agents 和 rules，{root} 换成本仓库目录；replaces 是插件接手之前的旧脚本，命令还指着它的换成新的）。
    call 写成「模块.函数」。"""
    found = {}
    for path in sorted(HERE.glob("*/plugin.json")):
        raw = _read(path)
        if raw and raw.get("id") == path.parent.name:
            found[raw["id"]] = raw
    return found


def _read(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


KNOWN = _manifests()


def _call(plugin, ref):
    module, name = ref.rsplit(".", 1)
    return getattr(importlib.import_module(f"{__name__}.{plugin}.{module}"), name)


def registry(data):
    """登记表；还没有（或写坏了）是 None。"""
    return _read(Path(data) / REGISTRY) if data else None


def installed(data, plugin):
    if plugin not in KNOWN or not data:
        return False
    found = registry(data)
    if found is None:
        return (Path(data) / KNOWN[plugin]["legacy"]).is_file()
    entry = found.get(plugin)
    return isinstance(entry, dict) and entry.get("installed") is True


def status(data):
    return {plugin: installed(data, plugin) for plugin in KNOWN}


def _stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _set(data, plugin, entry):
    """写一个插件的登记；第一次写登记表时，别的插件按老数据推断的状态一起落进去，不会因为写了登记表就掉了。"""
    found = registry(data)
    if found is None:
        found = {p: {"installed": installed(data, p)} for p in KNOWN}
    found[plugin] = entry
    _write(Path(data) / REGISTRY, found)


def _check(plugin):
    if plugin not in KNOWN:
        raise ValueError(f"没有这个插件：{plugin}（现有：{'、'.join(KNOWN)}）")


def install(data, plugin, *, vault_dir="", python="", rules=True):
    """装上：微信要有 wechat.json（读取工具在哪、用哪个 python）；已经有就不改。再把插件的规则加进中台。
    中台规则没改成（校验不过）就什么都不写，插件也不算装上。返回给人看的几行说明。"""
    _check(plugin)
    config = Path(data) / KNOWN[plugin]["legacy"] if KNOWN[plugin].get("legacy") else None
    fresh = plugin == "wechat" and not config.is_file()
    if fresh and (not vault_dir or not python):
        raise ValueError("第一次装微信要用 --vault-dir 指明微信读取工具的目录，--python 指明它要用的 python")
    lines = []
    if rules and KNOWN[plugin].get("autoloop"):
        lines.append(update_rules(lambda found: autoloop_add(found, plugin)))
    if plugin == "wechat":
        if fresh:
            _write(config, {"reader": "vault", "vault_dir": str(Path(vault_dir).expanduser()), "python": str(Path(python).expanduser())})
            lines.insert(0, f"已写 {config.name}")
        else:
            lines.insert(0, f"沿用已有的 {config.name}")
        from .wechat import inbox
        cfg = inbox.config(data, check_plugin=False)
        if cfg and cfg["problem"]:
            lines.append(f"注意：{cfg['problem']}")
        binary = inbox.real_binary(cfg["python"]) if cfg and cfg.get("python") else "读取工具用的 python"
        lines.append(f"要让后台能读微信，到 系统设置 → 隐私与安全性 → 完全磁盘访问 给 {binary} 打开")
        lines.append("接着登记要读的群：python3 -m personal.plugins wechat map --chat <群名> --project <项目 id>；"
                     "完整步骤见 personal/plugins/wechat/README.md")
    _set(data, plugin, {"installed": True, "installed_at": _stamp()})
    return [f"已装上「{KNOWN[plugin]['name']}」插件", *lines]


def uninstall(data, plugin, *, rules=True, purge=False):
    """去掉：停用，删掉它在中台加的规则（只删对得上的）。默认数据都留着，重新 install 就回来；
    purge 再删掉它在数据目录里的数据（清单的 data_files，再交给插件的 purge 清它在共用文件里的那部分）。"""
    _check(plugin)
    name = KNOWN[plugin]["name"]
    lines = [f"已去掉「{name}」插件：相关功能停用" + ("" if purge else "，数据都留着，重新 install 就回来")]
    if rules and KNOWN[plugin].get("autoloop"):
        lines.append(update_rules(lambda found: autoloop_remove(found, plugin)))  # 规则没改成（校验不过）就不停用
    _set(data, plugin, {"installed": False, "removed_at": _stamp()})
    if purge:
        removed = []
        for file in KNOWN[plugin].get("data_files", []):
            target = Path(data) / file
            if target.is_file():
                target.unlink()
                removed.append(file)
        lines.append(f"已删掉数据目录里的：{'、'.join(removed) or '（没有）'}")
        if KNOWN[plugin].get("purge"):
            lines += _call(plugin, KNOWN[plugin]["purge"])(data)
    return lines


# ---- 中台规则：装上加、去掉删 ----

def _filled(value, root):
    """清单里的 {root} 换成本仓库的目录。"""
    if isinstance(value, str):
        return value.replace("{root}", str(root))
    if isinstance(value, list):
        return [_filled(v, root) for v in value]
    if isinstance(value, dict):
        return {k: _filled(v, root) for k, v in value.items()}
    return value


def _spec(plugin):
    from .. import agent_watch
    spec = KNOWN[plugin].get("autoloop") or {}
    return _filled({"agents": spec.get("agents") or {}, "rules": spec.get("rules") or []}, agent_watch.ROOT), spec.get("replaces") or []


def _event(rule):
    return tuple((rule.get("when") or {}).get("types") or []) if isinstance(rule, dict) else ()


def autoloop_add(data, plugin):
    """把插件的 Agent 和规则加进中台规则（data 是 rules.json 的内容，原地改）。已有的不改，只有一种例外：
    Agent 的命令还指着插件接手之前的旧脚本（清单的 replaces），换成插件里的，对应规则的 cwd 一起换。返回 (改了哪些, 说明)。"""
    from .. import agent_watch
    spec, replaces = _spec(plugin)
    rules, agents = (data.get("rules"), data.get("agents")) if isinstance(data, dict) else (None, None)
    if not isinstance(rules, list) or not isinstance(agents, dict):
        return [], f"；rules.json 里没有 rules 或 agents，{KNOWN[plugin]['name']}插件的规则没加"
    changed, moved = [], set()
    for agent_id, agent in spec["agents"].items():
        have = agents.get(agent_id)
        if have is None:
            agents[agent_id] = {"name": agent.get("name", agent_id), **agent_watch.command_worker(agents),
                                **{k: v for k, v in agent.items() if k != "name"}}
            changed.append(f"加了 Agent {agent_id}")
        elif isinstance(have, dict) and have.get("command") != agent.get("command") \
                and any(old in part for part in have.get("command") or [] for old in replaces):
            have["command"] = agent["command"]
            moved.add(agent_id)
            changed.append(f"Agent {agent_id} 的命令换成插件里的")
    for rule in spec["rules"]:
        same = next((r for r in rules if isinstance(r, dict) and r.get("name") == rule["name"]), None)
        if same is None:
            rules.append(rule)
            changed.append(f"新增规则「{rule['name']}」")
        elif same.get("to") in moved and same.get("cwd") != rule.get("cwd"):
            same["cwd"] = rule["cwd"]
            changed.append(f"规则「{rule['name']}」的目录换成本仓库")
    return changed, ""


def autoloop_remove(data, plugin):
    """删掉插件加的规则和 Agent：只删名字、事件、Agent 都对得上的规则，和不再被别的规则用、命令也对得上的 Agent；
    对不上的（本人改过）不删，说明里写一句。返回 (删了哪些, 说明)。"""
    spec, replaces = _spec(plugin)
    rules, agents = (data.get("rules"), data.get("agents")) if isinstance(data, dict) else (None, None)
    if not isinstance(rules, list) or not isinstance(agents, dict):
        return [], ""
    changed, notes = [], []
    for rule in spec["rules"]:
        for same in [r for r in rules if isinstance(r, dict) and r.get("name") == rule["name"]]:
            if same.get("to") == rule.get("to") and _event(same) == _event(rule):
                rules.remove(same)
                changed.append(f"删了规则「{rule['name']}」")
            else:
                notes.append(f"规则「{rule['name']}」改过，没删，要删请手动改 rules.json")
    for agent_id, agent in spec["agents"].items():
        have = agents.get(agent_id)
        if not isinstance(have, dict) or any(isinstance(r, dict) and r.get("to") == agent_id for r in rules):
            continue
        command = have.get("command") or []
        if command == agent.get("command") or any(old in part for part in command for old in replaces):
            del agents[agent_id]
            changed.append(f"删了 Agent {agent_id}")
        else:
            notes.append(f"Agent {agent_id} 的命令改过，没删")
    return changed, "".join(f"；{n}" for n in notes)


def _check_rules(data):
    """改好的规则先交给 agent-autoloop check 校验（写进临时目录，不碰正在用的 rules.json）；没装 agent-autoloop 就不校验。
    不过关抛 ValueError。"""
    import shutil
    import tempfile
    from .. import agent_watch
    if not shutil.which("agent-autoloop"):
        return
    with tempfile.TemporaryDirectory(prefix="rules-check-") as folder:
        candidate = Path(folder) / "rules.json"
        candidate.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            agent_watch.run(["agent-autoloop", "check", str(candidate)], timeout=60)
        except (RuntimeError, OSError) as exc:
            raise ValueError(f"改好的中台规则没通过校验，rules.json 没动：{exc}") from None


def update_rules(change, path=None):
    """读中台的 rules.json，交给 change(data) 原地改；有改动先校验、再把原文件备份成 rules.json.bak-<时间>，最后写回。
    返回一句说明。没有 rules.json 就不改，说明里写一句。"""
    import shutil
    from .. import agent_watch
    path = Path(path or agent_watch.home() / "rules.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"没读到中台的 {path}，规则没改（装好中台后再运行一次）"
    changed, note = change(data)
    if not changed:
        return "中台规则不用改" + note
    _check_rules(data)
    backup = agent_watch.backup_path(path)
    shutil.copy2(path, backup)
    agent_watch.write_json(path, data)
    return f"中台规则：{'、'.join(changed)}（原文件备份在 {backup.name}）{note}"


def overview(data):
    """插件弹窗：每个插件装没装、第一次装要不要给配置，和它自己的「装好了没有」检查（清单的 checks）。"""
    found = []
    for plugin, info in KNOWN.items():
        legacy = info.get("legacy")
        found.append({"id": plugin, "name": info["name"], "installed": installed(data, plugin),
                      "needs_config": bool(legacy) and not (Path(data) / legacy).is_file(),
                      "checks": _call(plugin, info["checks"])(data) if info.get("checks") else []})
    return {"plugins": found}


def act(data, body, *, remove=False):
    """弹窗里的「装上」「去掉」：和命令行一样（去掉只停用、数据留着；页面上不做 --purge）。返回 {lines, plugins}。"""
    plugin = str(body.get("id") or "")
    lines = uninstall(data, plugin) if remove else install(data, plugin, vault_dir=str(body.get("vault_dir") or "").strip(),
                                                           python=str(body.get("python") or "").strip())
    return {"lines": lines, **overview(data)}


def needs_sources(data):
    """已装插件给「读新需求」的来源：[{id, event, rule, subject}]。"""
    return [src for plugin, info in KNOWN.items() if installed(data, plugin) for src in info.get("needs_sources", [])]


# ---- 核心调插件：路由、钩子、后台线程 ----

def route(data, method, path):
    """这个请求归哪个插件：(处理函数, 没装时的拒绝原因)。不归插件是 (None, "")；没装且不是 open 的路由给原因、不给函数。"""
    for plugin, info in KNOWN.items():
        for r in info.get("routes", []):
            if r["method"] == method and r["path"] == path:
                if not r.get("open") and not installed(data, plugin):
                    return None, f"{info['name']}插件没装"
                return _call(plugin, r["call"]), ""
    return None, ""


def hook(base, name, /, *args, **kwargs):
    """调已装插件的钩子（base 是数据目录，用来查装没装；其余参数原样转给钩子），返回各插件的结果（没装、没这个钩子的不调）。"""
    return [_call(plugin, info["hooks"][name])(*args, **kwargs)
            for plugin, info in KNOWN.items() if name in info.get("hooks", {}) and installed(base, plugin)]


def start_threads(data):
    """启动各插件的后台线程。没装也启动：线程每次醒来自己查装没装，装上去掉不用重启工作台。"""
    for plugin, info in KNOWN.items():
        for ref in info.get("threads", []):
            _call(plugin, ref)(data)


def main(argv=None, *, data=None):
    parser = argparse.ArgumentParser(prog="python3 -m personal.plugins", description="工作台插件：装上才能用，去掉就停用")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出插件和装没装")
    put = sub.add_parser("install", help="装上一个插件")
    put.add_argument("plugin")
    put.add_argument("--vault-dir", default="", help="微信：读取工具的目录（第一次装时要）")
    put.add_argument("--python", default="", help="微信：读取工具要用的 python（第一次装时要）")
    drop = sub.add_parser("uninstall", help="去掉一个插件（数据留着；加 --purge 连数据一起删）")
    drop.add_argument("plugin")
    drop.add_argument("--purge", action="store_true", help="连它在数据目录里的数据一起删（删了就回不来，重新 install 要重新配置）")
    for plugin, info in KNOWN.items():
        if info.get("command"):
            sub.add_parser(plugin, help=f"{info['name']}插件自己的命令（{plugin} -h 看）", add_help=False)
    argv = sys.argv[1:] if argv is None else list(argv)
    if data is None:
        from ..__main__ import DATA
        data = DATA
    if argv and argv[0] in KNOWN and KNOWN[argv[0]].get("command"):
        if not installed(data, argv[0]):
            print(f"{KNOWN[argv[0]]['name']}插件没装：先运行 python3 -m personal.plugins install {argv[0]}", file=sys.stderr)
            return 2
        from ..__main__ import project_config
        return _call(argv[0], KNOWN[argv[0]]["command"])(argv[1:], data=data, projects=project_config())
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            lines = [f"{p}（{info['name']}）：{'已装' if installed(data, p) else '没装'}" for p, info in KNOWN.items()]
        elif args.command == "install":
            lines = install(data, args.plugin, vault_dir=args.vault_dir, python=args.python)
        else:
            lines = uninstall(data, args.plugin, purge=args.purge)
    except ValueError as exc:
        print(f"没成：{exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0
