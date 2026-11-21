"""个人开发工作台入口：python -m personal 启动本机网页，python -m personal refresh-commits 只核对近月范围。"""
from __future__ import annotations
import argparse
import json
import logging
import os
import re
import sys
import threading
import tomllib
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .errors import APIError
from . import commits, plugins

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("WORKBENCH_DATA") or ROOT / ".personal").resolve()  # 运行数据目录，测试和临时实例可以换
TEMPLATE = ROOT / "personal/static/index.html"
SCOPE_MAX_AGE = timedelta(hours=24)  # 近月范围核对超过这么久就在后台重新核对
SCOPE_RETRY = timedelta(hours=1)  # 上次核对 GitHub 没读到、或核对出错时，隔这么久再试
SCOPE_WAKE = 3600  # 后台最多睡这么久就醒来看一次（电脑睡眠后也能及时补上）
_scope_lock = threading.Lock()


# 浏览器关页、刷新或请求超时会断开连接，写回时就报这几种错：这是正常现象，不打印堆栈，最多记一行 debug
DISCONNECTS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
log = logging.getLogger("personal.web")


def disconnected(client, exc):
    log.debug("浏览器已断开 %s：%s", client, type(exc).__name__)


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, DISCONNECTS):
            disconnected(client_address, exc); return
        super().handle_error(request, client_address)


OTHER_SUBGROUP = "其他"  # 没填 subgroup 的项目在页面上归到这里
OWN_TAG, UPSTREAM_TAG = "我的仓库", "上游贡献"  # 开源协作按 github 的 owner 自动加，不用手填


def project_config():
    """登记表 personal/projects.toml 合上数据目录里可选的本机覆盖文件 projects.local.toml（personal/projects_config.py）。"""
    from . import projects_config
    return validate(projects_config.load(ROOT / "personal/projects.toml")["projects"])


def validate(projects):
    """subgroup 可选，写字符串；tags 可选，写字符串数组。值不限定，写错类型才报错。
    archived 可选，写 true / false；archived_at 可选，写带引号的 "YYYY-MM-DD"，archived 不是 true 时不起作用。
    review_prs 可选，写 true / false：不是本人名下的仓库也审 agent PR（见 agent_watch.reviewable）。
    paused 可选，写 true / false，paused_note 可选写字符串：暂停的项目 issue 在收件箱里全归「搁置」（见 next_step.classify）。
    main_branch 可选，写分支名：项目主线不是 GitHub 默认分支时写它，「已合并到主线的 PR」和「主线多久没提交」按它算。
    push_access 可选，写 true / false：别人名下、你有推送权限的仓库，交给 Agent 时在原仓库开分支开 PR，不 fork（见 issues.forks）。"""
    for p in projects:
        if not isinstance(p.get("subgroup", ""), str):
            raise ValueError(f"projects.toml 里 {p.get('id', '?')} 的 subgroup 要写成字符串")
        tags = p.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError(f"projects.toml 里 {p.get('id', '?')} 的 tags 要写成字符串数组")
        if not isinstance(p.get("archived", False), bool):
            raise ValueError(f"projects.toml 里 {p.get('id', '?')} 的 archived 要写成 true 或 false")
        if not isinstance(p.get("review_prs", False), bool):
            raise ValueError(f"projects.toml 里 {p.get('id', '?')} 的 review_prs 要写成 true 或 false")
        if not isinstance(p.get("push_access", False), bool):
            raise ValueError(f"projects.toml 里 {p.get('id', '?')} 的 push_access 要写成 true 或 false")
        if not isinstance(p.get("paused", False), bool):
            raise ValueError(f"projects.toml 里 {p.get('id', '?')} 的 paused 要写成 true 或 false")
        for key in ("paused_note", "main_branch"):
            if not isinstance(p.get(key, ""), str):
                raise ValueError(f"projects.toml 里 {p.get('id', '?')} 的 {key} 要写成字符串")
        at = p.get("archived_at", "")
        if not isinstance(at, str) or (at and not valid_day(at)):
            raise ValueError(f"projects.toml 里 {p.get('id', '?')} 的 archived_at 要写成带引号的日期，例如 \"2026-11-12\"")
    return projects


def valid_day(text):
    try:
        return bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text)) and bool(date.fromisoformat(text))
    except ValueError:
        return False


def archived(project):
    """归档的项目：默认视图、计数、巡检和派活都不处理，只在总览的「已归档」里列出。
    只在读取时过滤，不删不改任务库、提交记录和消息记录；改回 archived = false 或删掉这一行就完全恢复。"""
    return project.get("archived") is True


def classify(config):
    """项目的二级分类和标签：subgroup 没填归「其他」；开源协作再按 github（没有就 repository）的 owner
    自动加「我的仓库」或「上游贡献」，放在手填标签前面。"""
    from .upstream import LOGIN
    tags = [t.strip() for t in config.get("tags", []) if t.strip()]
    repo = config.get("github") or config.get("repository") or ""
    if config.get("group") == "开源协作" and repo:
        tags.insert(0, OWN_TAG if repo.split("/", 1)[0].lower() == LOGIN else UPSTREAM_TAG)
    return {"subgroup": config.get("subgroup", "").strip() or OTHER_SUBGROUP, "tags": list(dict.fromkeys(tags))}


def subgroup_order(projects):
    """每个一级分组下的二级分类，按在 projects.toml 里第一次出现的顺序，「其他」排最后。"""
    order = {}
    for p in projects:
        names = order.setdefault(p.get("group", "其他"), [])
        name = classify(p)["subgroup"]
        if name not in names:
            names.append(name)
    return {g: [n for n in names if n != OTHER_SUBGROUP] + [n for n in names if n == OTHER_SUBGROUP] for g, names in order.items()}


def scoped_project_config():
    """近月有本人活动的项目，含归档的。"""
    ids = commits.active_ids(commits.load(DATA).get("records", {}))
    return [p for p in project_config() if p["id"] in ids]


def active_project_config():
    """当前项目：近月有本人活动、没有归档。归档只在读取时过滤，改回 archived = false 就回到所有视图。"""
    return [p for p in scoped_project_config() if not archived(p)]


def refresh_scope():
    """重新核对近月范围：先发现近月本人在上游创建过 PR 或 issue、还没登记的仓库并追加到登记表，
    再按本人提交和本人 PR/issue 算出当前项目。"""
    from . import discovery, github, projects_config
    since = commits.cutoff(datetime.now().astimezone()).date().isoformat()
    try:
        authored, added = discovery.run(ROOT / "personal/projects.toml", project_config(), since,
                                        target=projects_config.local_path())
        note = ""
    except (github.RateLimited, RuntimeError, OSError, ValueError) as exc:
        authored, added, note = [], [], f"GitHub 上本人的 PR 和 issue 没读到：{exc}"
    report = commits.refresh(project_config(), DATA, authored=authored)
    report["github_error"] = "；".join(x for x in (report["github_error"], note) if x)
    report["discovered"] = [f"{e['github']}（{e['repo_path'] or '无本地检出'}）" for e in added]
    return report


def rescope():
    """重新核对近月范围；手动和后台核对共用一把锁，不会同时跑两次。"""
    with _scope_lock:
        return refresh_scope()


def scope_due(report, now=None):
    """commits.json 的 checked_at 超过 SCOPE_MAX_AGE（上次 GitHub 没读到时超过 SCOPE_RETRY）就该重新核对。"""
    now = now or datetime.now().astimezone()
    age = now - commits.instant(report.get("checked_at"))
    return age >= SCOPE_MAX_AGE or (bool(report.get("github_error")) and age >= SCOPE_RETRY)


def keep_scope_fresh(stop, *, refresh=None, load=None, wake=SCOPE_WAKE):
    """后台线程：启动时和之后每次醒来检查一次，过期就重新核对，不阻塞页面。
    核对过一次（成功或出错）后至少隔 SCOPE_RETRY 再核对，出错不会连着重试。"""
    refresh, load = refresh or rescope, load or (lambda: commits.load(DATA))
    while not stop.is_set():
        pause = wake
        if scope_due(load()):
            try:
                report = refresh()
                print(f"已在后台重新核对近月范围：{len(active_project_config())} 个项目"
                      + (f"；{report['github_error']}" if report.get("github_error") else ""), file=sys.stderr, flush=True)
            except Exception:
                traceback.print_exc(file=sys.stderr)
            pause = max(wake, SCOPE_RETRY.total_seconds())
        stop.wait(pause)


def start_scope_keeper(stop=None, **options):
    stop = stop or threading.Event()
    thread = threading.Thread(target=keep_scope_fresh, args=(stop,), kwargs=options, daemon=True, name="scope-keeper")
    thread.start()
    return thread, stop


def archived_projects(report, described):
    """总览「已归档」里的项目：不读 issue，所以 issue_repository 留空；仓库链接用 repository。"""
    from . import summaries
    rows = []
    for config in project_config():
        if not archived(config):
            continue
        summary, summary_source = summaries.summarize(config, described)
        rows.append({"id": config["id"], "name": config["name"], "enabled": False, "repo_path": config.get("repo_path", ""),
                     "issue_scope": config.get("issue_scope", ""), "group": config.get("group", "其他"), **classify(config),
                     "summary": summary, "summary_source": summary_source,
                     "repository": config.get("repository", config.get("github", "")), "issue_repository": "",
                     "last_commit_at": report.get("records", {}).get(config["id"], {}).get("last_commit_at"),
                     "source_status": "已归档", "archived": True, "archived_at": config.get("archived_at", "")})
    return rows


def snapshot():
    """页面数据：当前项目（projects.toml 里近月有本人活动、没归档的，按登记表顺序）、已归档的项目和核对范围。"""
    report = commits.load(DATA)
    from . import summaries
    described = summaries.descriptions(DATA, [r for p in project_config() for r in (p.get("repository"), p.get("github")) if r])
    projects = []
    for config in active_project_config():
        summary, summary_source = summaries.summarize(config, described)
        projects.append({"id": config["id"], "name": config["name"], "enabled": True,
                         "repo_path": config.get("repo_path", ""), "issue_scope": config.get("issue_scope", ""),
                         "group": config.get("group", "其他"), **classify(config), "summary": summary, "summary_source": summary_source,
                         "repository": config.get("repository", config.get("github", "")),
                         "issue_repository": config.get("github", ""),
                         "last_commit_at": report.get("records", {}).get(config["id"], {}).get("last_commit_at"),
                         "source_status": config.get("source_status", "GitHub Issues" if config.get("github") else "仅本地登记")})
    return {"projects": projects, "archived": archived_projects(report, described), "subgroups": subgroup_order(project_config()),
            "commit_window": {"since": commits.cutoff(datetime.now().astimezone()).isoformat(),
                                "checked_at": report.get("checked_at"), "github_error": report.get("github_error", "")},
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}


def find_project(project_id):
    project = next((p for p in active_project_config() if p["id"] == project_id), None)
    if project is None:
        raise ValueError("这个项目不在当前范围内")
    return project


def age(query):
    """?max_age=秒：项目详情页要的数据新鲜度，限定在 30 秒到 5 分钟之间。"""
    raw = (query.get("max_age") or [""])[-1]
    return max(30, min(300, int(raw))) if raw.isdigit() else None


def render(payload):
    raw = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return TEMPLATE.read_text(encoding="utf-8").replace("__PAIHUO_DATA__", raw)


def with_news(project, report):
    """项目 PR 结果加上新消息和最近动态：通知只读本机已同步的结果；读不到就照原样返回并写明。"""
    from . import notifications, pr_news
    try:
        threads = notifications.project_threads(DATA, active_project_config(), project_config(), project["id"])
    except (OSError, ValueError, KeyError) as exc:
        return {**pr_news.annotate(report, []), "news_note": f"GitHub 消息没读到，新消息标记暂缺：{str(exc)[:200]}"}
    return pr_news.attach(report, threads)


def make_web(port):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, kind="application/json"):
            if kind == "application/json":
                body = json.dumps(body, ensure_ascii=False)
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(data)

        def send_file(self, data, kind, policy):
            """学习目录、导读目录和回顾目录里的只读静态文件：放进沙箱（policy），不让页面借工作台的来源调接口。"""
            self.send_response(200)
            self.send_header("Content-Type", kind + ("; charset=utf-8" if kind.startswith("text/") else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", policy)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers(); self.wfile.write(data)

        def guarded(self, action):
            try:
                self.answer(action)
            except DISCONNECTS as exc:
                disconnected(self.client_address, exc)

        def answer(self, action):
            try:
                action()
            except DISCONNECTS:
                raise
            except APIError as exc:
                self.send(exc.status, {"error": str(exc)})
            except (ValueError, KeyError) as exc:
                self.send(400, {"error": str(exc)})
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                self.send(502, {"error": f"来源暂时不可用（{type(exc).__name__}：{str(exc)[:200]}），请检查本机服务后重试"})

        def do_GET(self):
            self.guarded(self.read)

        def read(self):
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}", "127.0.0.1:5174", "localhost:5174"}:
                self.send(403, {"error": "请从本机工作台读取数据"}); return
            path = urlsplit(self.path).path
            if path == "/api/brief":
                from . import assistant
                dates = parse_qs(urlsplit(self.path).query).get("date")
                self.send(200, assistant.brief(dates[-1] if dates else None))
            elif path == "/api/boards":
                from . import assistant
                self.send(200, assistant.boards())
            elif path == "/api/learning":
                from . import learning, novels
                self.send(200, {**learning.view(), "novels": novels.view(DATA)})
            elif path == "/api/review":
                from . import review
                self.send(200, review.view())
            elif path == "/api/health/daily":
                from . import health
                self.send(200, health.view())
            elif path == "/api/media/maitian":
                from . import media
                self.send(200, media.view(DATA))
            elif path == "/api/media/content":
                from . import media
                self.send(200, media.content())
            elif path == "/media/maitian":
                # 「打开麦田大屏」：后端 302 到麦田带口令的入口，口令不进页面数据
                from . import media
                self.send_response(302)
                self.send_header("Location", media.open_url(DATA))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif match := re.fullmatch(r"/learning/([^/]+)/(.+)", path):
                from . import learning
                from urllib.parse import unquote
                data, kind = learning.static_file(unquote(match[1]), unquote(match[2]))
                self.send_file(data, kind, learning.SANDBOX)
            elif match := re.fullmatch(r"/review/(week|month)/([^/]+)", path):
                from . import learning, review
                from urllib.parse import unquote
                data, kind = review.static_file(match[1], unquote(match[2]))
                self.send_file(data, kind, learning.SANDBOX)
            elif match := re.fullmatch(r"/novels/(.+)", path):
                from . import learning, novels
                from urllib.parse import unquote
                data, kind = novels.static_file(unquote(match[1]))
                self.send_file(data, kind, learning.SANDBOX)
            elif path == "/api/github/notifications":
                from . import notifications
                self.send(200, notifications.view(DATA, active_project_config(), project_config(), summary=True))
            elif match := re.fullmatch(r"/api/github/notifications/([0-9]+)", path):
                from . import notifications
                self.send(200, notifications.detail(DATA, match[1], active_project_config(), project_config()))
            elif path == "/api/issues":
                from . import issues
                query = parse_qs(urlsplit(self.path).query)
                self.send(200, issues.view(active_project_config(), max_age=age(query), focus=(query.get("focus") or [None])[-1], data=DATA))
            elif path == "/api/issues/done":
                from . import activity
                self.send(200, activity.done_all(active_project_config(), DATA))
            elif match := re.fullmatch(r"/api/projects/([\w.-]+)/activity", path):
                from . import activity
                project = find_project(match[1])
                report = activity.view(project, DATA, max_age=age(parse_qs(urlsplit(self.path).query)))
                self.send(200, with_news(project, report))
            elif match := re.fullmatch(r"/api/issues/([\w.-]+/[\w.-]+)/([0-9]+)", path):
                from . import issues
                self.send(200, issues.detail(active_project_config(), match[1], match[2]))
            elif match := re.fullmatch(r"/api/issues/([\w.-]+/[\w.-]+)/([0-9]+)/prs", path):
                from . import issues
                self.send(200, issues.linked(active_project_config(), match[1], match[2]))
            elif path == "/api/prs/feedback":
                from . import pr_feedback
                self.send(200, pr_feedback.view(project_config(), DATA))
            elif path == "/api/agents":
                from . import conversation
                self.send(200, conversation.agents())
            elif path == "/api/replies/drafts":
                from . import reply_drafts
                self.send(200, reply_drafts.listing(DATA))
            elif path == "/api/issue-drafts":  # 从微信提炼出的需求拟成的 issue 草稿，等你确认后才创建；带扫描状态
                from . import issue_drafts
                self.send(200, issue_drafts.listing(project_config(), DATA))
            elif path == "/api/autoloop/runs":
                from . import issues
                self.send(200, issues.run_log())
            elif match := re.fullmatch(r"/api/autoloop/runs/([1-9][0-9]{0,8})", path):
                from . import run_detail  # 只读本机 worker 的运行目录
                try:
                    self.send(200, run_detail.detail(match[1]))
                except LookupError as exc:
                    self.send(404, {"error": str(exc)})
            elif path == "/api/autoloop/watch":
                from . import issues
                self.send(200, issues.watch_status())
            elif path == "/api/chats":  # 对话页：只读的助理（personal/chat.py），对话只存本机
                from . import chat
                self.send(200, chat.listing(DATA))
            elif path.startswith("/api/chats/"):
                from . import chat
                self.send(200, chat.detail(path.rsplit("/", 1)[-1], DATA))
            elif path == "/api/board":
                self.send(200, snapshot())
            elif path in ("/", "/index.html"):
                self.send(200, render(snapshot()), "text/html")
            elif path == "/api/plugins":  # 插件弹窗：装没装、装好了没有
                self.send(200, plugins.overview(DATA))
            else:
                handler, refused = plugins.route(DATA, "GET", path)  # 插件的接口（personal/plugins/*/plugin.json 的 routes）
                if handler:
                    self.send(200, handler(DATA))
                else:
                    self.send(404, {"error": refused or "没有这个路径"})

        def do_POST(self):
            self.guarded(self.write)

        def write(self):
            host = self.headers.get("Host", "")
            trusted = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}",
                       "http://127.0.0.1:5174", "http://localhost:5174"}
            if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}", "127.0.0.1:5174", "localhost:5174"} or self.headers.get("Origin") not in trusted or self.headers.get("X-Paihuo-Request") != "1":
                self.send(403, {"error": "请从本机工作台提交操作"}); return
            if urlsplit(self.path).path == "/api/issue-drafts/audio":  # 上传一段录音（请求体就是音频）：存进投放目录，接着读一轮，本机转写
                from urllib.parse import unquote
                from . import issue_drafts
                self.send(200, issue_drafts.save_audio(unquote(self.headers.get("X-File-Name", "")), self.rfile,
                                                       int(self.headers.get("Content-Length", "0")), data=DATA)); return
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError("操作需要 JSON 请求")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 262144:
                raise ValueError("请求大小无效")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求需要 JSON 对象")
            path = urlsplit(self.path).path
            if path == "/api/github/notifications/sync":
                from . import notifications
                self.send(200, notifications.sync(DATA, active_project_config(), project_config())); return
            if path == "/api/brief/run":
                from . import assistant
                self.send(200, assistant.run_brief()); return
            if path == "/api/learning/feedback":
                from . import learning
                self.send(200, learning.save_feedback(body)); return
            if path == "/api/learning/next":
                from . import learning
                self.send(200, learning.handoff(body)); return
            if path == "/api/review/answer":  # 回顾标签的三处写：保存三问的回答、运行回顾脚本、交给 Agent 写总结和三问
                from . import review
                self.send(200, review.save_answer(body)); return
            if path == "/api/review/run":
                from . import review
                self.send(200, review.run(body)); return
            if path == "/api/review/next":
                from . import review
                self.send(200, review.handoff(body)); return
            if path == "/api/learning/novel":
                from . import novels
                self.send(200, novels.set_status(DATA, body)); return
            if path == "/api/issues/refresh":
                from . import issues
                self.send(200, issues.view(active_project_config(), refresh=True, data=DATA)); return
            if path == "/api/issues/done/refresh":
                from . import activity
                self.send(200, activity.done_all(active_project_config(), DATA, force=True)); return
            if match := re.fullmatch(r"/api/projects/([\w.-]+)/activity/refresh", path):
                from . import activity
                project = find_project(match[1])
                self.send(200, with_news(project, activity.view(project, DATA, force=True))); return
            if path == "/api/issues/handoff":
                from . import issues
                self.send(200, issues.batch_handoff(active_project_config(), body.get("items"), mode=body.get("mode"), data=DATA)); return
            if match := re.fullmatch(r"/api/issues/([\w.-]+/[\w.-]+)/([0-9]+)/handoff", path):
                from . import issues
                self.send(200, issues.handoff(active_project_config(), match[1], match[2], mode=body.get("mode"), data=DATA,
                                              force=body.get("force") is True)); return
            if match := re.fullmatch(r"/api/issues/([\w.-]+/[\w.-]+)/([0-9]+)/decisions", path):
                from . import issues
                self.send(200, issues.answer_package(active_project_config(), f"{match[1]}#{match[2]}", body)); return
            if path == "/api/prs/feedback/refresh":
                from . import pr_feedback
                self.send(200, pr_feedback.view(project_config(), DATA, refresh=True)); return
            if path == "/api/prs/handoff":
                from . import pr_feedback
                self.send(200, pr_feedback.handoff(project_config(), body.get("prs"), mode=body.get("mode"), data=DATA,
                                                   force=body.get("force") is True)); return
            if match := re.fullmatch(r"/api/prs/questions/([0-9]+)/answer", path):
                from . import pr_feedback
                self.send(200, pr_feedback.answer(match[1], body)); return
            if match := re.fullmatch(r"/api/questions/([0-9]+)/answer", path):
                from . import issues
                self.send(200, issues.answer(active_project_config(), match[1], body)); return
            if path == "/api/snooze":  # 暂时不处理：只记本机 .personal/snoozed.json，不写 GitHub
                from . import snooze
                self.send(200, snooze.put(DATA, body.get("items"), str(body.get("wait") or "news"))); return
            if path == "/api/snooze/restore":
                from . import snooze
                self.send(200, snooze.restore(DATA, body.get("items"))); return
            if path in ("/api/plugins/install", "/api/plugins/uninstall"):  # 插件弹窗的「装上」「去掉」：只改本机配置和中台规则
                self.send(200, plugins.act(DATA, body, remove=path.endswith("uninstall"))); return
            handler, refused = plugins.route(DATA, "POST", path)  # 插件的写接口：没装回 404
            if refused:
                self.send(404, {"error": refused}); return
            if handler:
                self.send(200, handler(DATA, body)); return
            if path == "/api/replies/send":  # 确认发送用户审阅过的回复草稿（用户 2026-11-18 要求）
                from . import reply_drafts
                self.send(200, reply_drafts.send(str(body.get("subject") or ""), body.get("bodies"), force=body.get("force") is True, data=DATA)); return
            if path == "/api/replies/discard":
                from . import reply_drafts
                self.send(200, reply_drafts.discard(str(body.get("subject") or ""), data=DATA)); return
            if path == "/api/replies/regenerate":
                from . import reply_drafts
                self.send(200, reply_drafts.regenerate(project_config(), str(body.get("subject") or ""), data=DATA, mode=body.get("mode"))); return
            if path == "/api/issue-drafts/create":  # 确认创建用户审阅过的 issue 草稿（用户 2026-11-20 要求）
                from . import issue_drafts
                self.send(200, issue_drafts.create(body.get("id"), project_config(), title=body.get("title"), body=body.get("body"),
                                                   repo=body.get("repo"), new_repo=body.get("new_repo"), mention=body.get("mention"),
                                                   data=DATA)); return
            if path == "/api/issue-drafts/promote":  # 没拟的话题补成待评审的草稿（本机数据，不写 GitHub）
                from . import issue_drafts
                self.send(200, issue_drafts.promote(body.get("id"), data=DATA)); return
            if path == "/api/issue-drafts/reject":
                from . import issue_drafts
                self.send(200, issue_drafts.reject(body.get("id"), data=DATA)); return
            if path == "/api/issue-drafts/scan":  # 读新需求：记状态后立刻返回，后台刷新微信明文库再触发中台；聊天内容不经过工作台
                from . import issue_drafts
                self.send(200, issue_drafts.start_scan(data=DATA)); return
            if path == "/api/chat/send":  # 发一条消息：后台起只读的 Claude，立刻返回对话 id，页面再读进度
                from . import chat
                self.send(200, chat.send(str(body.get("text") or ""), body.get("id") or None, data=DATA)); return
            if path == "/api/chat/stop":
                from . import chat
                self.send(200, chat.stop(str(body.get("id") or ""))); return
            if path == "/api/chat/delete":
                from . import chat
                self.send(200, chat.delete(str(body.get("id") or ""), data=DATA)); return
            if path == "/api/chat/draft":  # 回复里的 issue 草稿存进「新需求」：只存本机，创建仍要在抽屉里确认
                from . import chat
                self.send(200, chat.save_draft(str(body.get("id") or ""), body.get("draft"), project_config(), data=DATA)); return
            if path == "/api/commits/refresh":
                report = rescope()
                self.send(200, {"active_projects": len(active_project_config()), "discovered": report["discovered"],
                                "github_error": report["github_error"]}); return
            self.send(404, {"error": "没有这个操作"})

        def log_message(self, *_):
            pass
    return Server(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description="个人开发工作台")
    parser.add_argument("command", choices=["serve", "refresh-commits"], nargs="?", default="serve")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    if args.command == "refresh-commits" or not (DATA / "commits.json").exists():
        report = refresh_scope()
        if report["github_error"]:
            print(report["github_error"] + "；当前只采用本地证据", flush=True)
    if args.command == "refresh-commits":
        print(json.dumps({"since": report["since"], "active_projects": len(active_project_config()),
                          "discovered": report["discovered"], "github_error": report["github_error"]}, ensure_ascii=False, indent=2))
        return
    web = None
    try:
        web = make_web(args.port)
        from . import agent_watch
        if agent_watch.ensure_pointer(ROOT):
            print(f"agent-autoloop 监看已指向本仓库：{ROOT}", flush=True)
        (ROOT / "board.html").write_text(render(snapshot()), encoding="utf-8")
        print(f"工作台：http://127.0.0.1:{web.server_port}；数据目录：{DATA}；Ctrl+C 停止", flush=True)
        from . import issues
        threading.Thread(target=lambda: issues.view(active_project_config(), data=DATA), daemon=True).start()  # 先把 issue 缓存读好
        from . import activity
        threading.Thread(target=lambda: activity.done_all(active_project_config(), DATA), daemon=True).start()  # 已完成：有保存的结果就只读增量
        start_scope_keeper()  # 启动时和运行期间每 24 小时：核对超过 24 小时就在后台重新核对
        plugins.start_threads(DATA)  # 插件的后台线程：每次醒来都查装没装，装上去掉不用重启（微信：有 wechat.json 才每 interval_minutes 分钟增量解析一次、出漏看检查）
        from . import issue_drafts
        issue_drafts.start_mention_keeper(DATA)  # 从微信拟的 issue 做完后 @ 对方：每 10 分钟看一遍，没有等着 @ 的不请求 GitHub
        from . import watch_gate
        watch_gate.start()  # 每 5 分钟一个请求看 agent issue 和 agent PR，有监看没看过的变化才触发中台（WORKBENCH_WATCH_GATE=0 不开）
        web.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if web:
            web.server_close()


if __name__ == "__main__":
    main()
