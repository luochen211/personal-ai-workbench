# 派活 · 个人开发工作台

本机运行的 GitHub 工作台：把登记项目的 open issue、你开着的 PR 和 GitHub 通知合进一个收件箱，按「下一步该谁做」归类，需要时交给本机的 agent-autoloop 中台，由 Agent 改代码、开 PR、写回复草稿。后端只用 Python 标准库，前端是 React + TypeScript，构建成一个 HTML 由后端提供。

## 能做什么

- **收件箱**（`#/inbox`）：一条 `owner/repo#编号` 只列一行，分「待你回」「草稿待确认」「待动手」「待你看」「Agent 处理中」「等对方」「我发的 PR」「搁置」「记录」，可按仓库收窄，底部有「近 30 天已完成」。归类规则在 `personal/next_step.py`，谁最后说话由 `personal/conversation.py` 判断。
- **交给 Agent**：待动手的 issue 交出去改代码（自己的仓库打 `agent` 标签，上游仓库 fork 后开 PR）；待你回里有「帮我回复」（只写回复草稿）和「处理并回复」（先改代码再写草稿）。Agent 不合并 PR、不直接发评论。
- **回复草稿**：Agent 写的回复放进「草稿待确认」，你看过、改过、点「确认发送」才用你的账号发出；发之前重读讨论，对方又回了会先问你。
- **暂时不处理**：只记在本机，对方有新动静或到了选的时间自动回来。
- **从微信拟 issue**：收件箱工具条右端的「新需求」按钮打开右侧抽屉，点里面的「读新需求」，登记了的微信会话和新的录音、会议转写（得到、飞书妙记、本机录音文件）里的待办、需求拟成 issue 草稿（录音也可以点「上传录音」直接传，本机转写），一行一条；你看过、改过、选了仓库（或新建一个私有仓库），点创建才创建。原话只在本机，不进 issue，见 [RUN.md](RUN.md#从微信拟-issue)。
- **微信插件**：`python3 -m personal.plugins install wechat` 装上后，收件箱多一个「微信」范围，列出本机微信漏看检查里的私聊待回、群里 @我、待收转账等，只读；「新需求」也读登记了的微信群（中台规则「微信拟 issue」随插件装上和去掉）；`python3 -m personal.plugins wechat articles` 查订阅的公众号推过哪些文章。`uninstall` 就停用，数据留着；加 `--purge` 连数据一起删。初始化步骤见 [personal/plugins/wechat/README.md](personal/plugins/wechat/README.md)，细节见 [RUN.md](RUN.md#微信插件)。
- **项目详情**（`#/p/<项目>`）、**助理**（今日简报、事项、学习、回顾、健康）、**自媒体**（麦田、在做的内容）、**Agent**（运行记录、巡检状态）。

页面对 GitHub 只有五种写操作：给自己仓库的 issue 打 `agent` 标签，发出你确认过的回复草稿，创建你确认过的 issue 草稿，在你确认后为草稿新建一个私有仓库，以及从草稿创建的 issue 做完后在 issue 下评论 @ 你填的 GitHub 账号。其余都是只读。

## 运行

需要 Python 3.11 以上、已登录的 `gh`；改前端时需要 Node 24。

```sh
cd workbench                              # 仓库根目录
python3 -m personal                       # 打开 http://127.0.0.1:8790
python3 -m personal refresh-commits       # 只重新核对近月项目范围
python3 -m personal.agent_watch install   # 第一次用或仓库搬家后：装中台规则、技能链接和桌面通道
```

第一次用：仓库里的登记表是空的，先在数据目录建 `.personal/projects.local.toml`，写上你的账号和要管的项目：

```toml
[owner]
login = "your-github-login"

[[projects]]
id = "myapp"
name = "我的应用"
group = "个人工具"
repo_path = "/path/to/myapp"
repository = "your-github-login/myapp"
github = "your-github-login/myapp"
source_status = "GitHub Issues"
```

「交给 Agent」要配合 [agent-autoloop](https://github.com/KKKKhazix/agent-autoloop) 中台使用。

只有最近一个月有本人真实提交（或在上游创建过 PR、issue）的项目进入当前范围；核对结果每 24 小时在后台自动更新。

## 数据放在哪

- `personal/projects.toml`：公开的登记表模板和字段说明，里面没有项目。你的账号（`[owner]`）和你的项目（`[[projects]]`，包括私有项目、客户项目）写在数据目录的 `projects.local.toml`，不进仓库，启动时合进来，见 [RUN.md](RUN.md#本机私有项目)。
- `.personal/`：本机运行数据（提交核对结果、GitHub 缓存、通知同步结果、回复草稿、issue 草稿、暂时不处理的记录），不进仓库。环境变量 `WORKBENCH_DATA` 可以换成别的目录。
- `~/.agent-autoloop/issues/`：交给 Agent 的记录，和中台共用。

## 开发与检查

```sh
python3 -m pip install -r requirements.txt
python3 -m pytest -q
cd web && npm ci && npm test && npm run build   # 产物是 personal/static/index.html，要一起提交
```

## 文档

- [RUN.md](RUN.md)：运行细节、GitHub 请求与限流、各接口和中台规则
- [docs/GitHub消息接入.md](docs/GitHub消息接入.md)
- [docs/PR反馈交给Agent.md](docs/PR反馈交给Agent.md)
- [docs/上游issue交给Agent.md](docs/上游issue交给Agent.md)
- [AGENTS.md](AGENTS.md)：在本仓改代码的规矩
