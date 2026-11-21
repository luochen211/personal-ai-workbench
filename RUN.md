# 个人版运行与验证

Python 3.11 以上、已登录的 `gh`；改前端时需要 Node 24。Python 运行时只用标准库；测试使用 pytest。「交给 Agent」要配合 [agent-autoloop](https://github.com/KKKKhazix/agent-autoloop) 中台使用。

## 第一次用

```sh
cd workbench                              # 仓库根目录
python3 -m personal                       # 打开 http://127.0.0.1:8790
python3 -m personal refresh-commits       # 只重新核对近月项目范围
python3 -m personal.agent_watch install   # 第一次用或仓库搬家后：装中台规则、技能链接和桌面通道
```

仓库里的登记表是空的，先在数据目录建 `.personal/projects.local.toml`，写上你的账号和要管的项目（字段说明见下面「本机私有项目」和 `personal/projects.toml`）：

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

数据放在哪：

- `personal/projects.toml`：公开的登记表模板和字段说明，里面没有项目；你的账号和项目写在数据目录的 `projects.local.toml`，不进仓库。
- `.personal/`：本机运行数据（提交核对结果、GitHub 缓存、通知同步结果、回复草稿、issue 草稿、对话、暂时不处理的记录），不进仓库。环境变量 `WORKBENCH_DATA` 可以换成别的目录。
- `~/.agent-autoloop/issues/`：交给 Agent 的记录，和中台共用。

## 本机工作台

```sh
cd workbench                              # 仓库根目录
python3 -m personal --port 8790
```

项目位置与来源配置来自 `personal/projects.toml`，当前范围由数据目录里 `commits.json` 的近月证据决定。服务只绑定 127.0.0.1。

运行数据目录默认是仓库根目录的 `.personal/`，环境变量 `WORKBENCH_DATA` 可以换成别的目录（试跑、测试用临时目录，不碰真实数据）。

### 本机私有项目

你的账号和项目（包括私有项目、客户项目）写在数据目录的 `projects.local.toml`（建议 `chmod 600`），写法和 `personal/projects.toml` 一样，启动和每次读页面时合进登记表（`personal/projects_config.py`）；网页、监看（`agent_watch`）和自动发现都读合并后的结果。没有这个文件就只用登记表。

- `[owner]`：`login` 是你的 GitHub 账号（环境变量 `WORKBENCH_GITHUB_LOGIN` 优先，都没写时用 `gh` 当前登录的账号），`accounts` 是你的其他账号，`emails` 是你提交用的其他邮箱（比如 GitHub 的 noreply 地址）。
- `[[projects]]`：`id` 和登记表里相同的整条替换，不同的追加。
- `[discovery]`：写了的键替换登记表里的同名键，例如把 `skip_owners` 放这里，客户账号名就不进仓库。`skip_owners` 只管自动发现时不往 `projects.local.toml` 追加，这里登记的项目不受它影响。
- 别人名下、你有推送权限的仓库写 `issue_scope = "mine"` 和 `push_access = true`：只读与你有关的 issue 和 PR（本人创建、被指派、被 @ 或评论过）；交给 Agent 时不打 `agent` 标签，记 `upstream-handoffs.json` 或 issue 留言账本，detail 不写「范围：上游仓库」，技能在原仓库开分支开 PR，不 fork；收件箱按自己的仓库归类（你开的、你最后说话的是「待动手」）。只写 `issue_scope = "mine"` 的照旧是上游仓库。

页面以 GitHub issue 为单位，不创建或编辑本地任务，也没有任务库。

## GitHub 请求与限流

所有 GitHub 请求经 `personal/github.py`：同时最多 3 个；遇到 403/429 限流（含二级限流）整体暂停，有 Retry-After 用它，没有就从 60 秒起按次数翻倍（最长 15 分钟），暂停期间不再发请求。结果按 key 缓存（issue 列表和与我有关的 issue、PR 5 分钟，项目简介 24 小时），同一 key 同时只有一个请求在刷新；过期时先给旧值、后台刷新。issue 详情缓存 1 分钟，过期就等新的，限流或读失败时给旧的并写明。通知正文读到限流不缓存成结果，有旧值给旧值。并发的请求一起撞上同一次限流只算一次，不叠加翻倍；在并发槽位外排队的请求拿到槽位后再检查一次是否在限流。监看进程（`personal/agent_watch.py`）读 issue、读标签事件和打标签也走这里。被限流时页面继续显示上次的数据，并写「GitHub 限流中，显示的是 HH:MM 的数据，约 N 分钟后自动重试」。每分钟发了多少请求、打到哪类接口，写到服务的标准错误输出（后台启动时在 `.personal/server.log`）。

项目详情的「我的 PR」和「已完成」（`GET /api/projects/<项目>/activity`，`personal/activity.py`）每个仓库两份缓存：开着的 PR 一个 GraphQL 搜索，5 分钟过期；已合并、已关闭的 PR 和近 30 天关闭的 issue 一个 GraphQL 请求（两个搜索），长期缓存（兜底 6 小时，存在 `.personal/project-activity.json`，重启不重读）。看到合并或关闭的迹象就让该项目的 issue 和 PR 缓存提前过期、下次请求重读：同步 GitHub 消息时主题变成已合并或已关闭，开着的 PR 从列表里消失，issue 从 open 列表里消失。过期不清读取时间，先给旧值、后台刷新。项目详情页带 `max_age=120`：它用到的 issue 和开着的 PR 超过 2 分钟就后台重读，页面隔 3 秒再取一次新结果（最多 5 次）。

新消息标记只用已经同步好的通知（`personal/pr_news.py`）：通知未读、主题对上 `owner/repo#编号` 就给那一行 PR 打标记。谁说了什么、几条新评论：对未读的 issue / PR 主题用一个 GraphQL 请求批量读最近 20 条评论和 10 条评审（只算别人留的、在通知 `last_read_at` 之后的），按（主题, 通知更新时间）缓存，通知没有新动态就不重读；限流时不发请求，只标「有新消息」；读失败的 5 分钟内不重读。GitHub 消息接口带 `digest`（各项目未读条数、最近一条、最近 3 条），也走这份缓存。最近动态用开着的 PR 搜索里顺带读的 CI 结束时间（最后一个提交的 check suite），不另发请求。

收件箱的「近 30 天已完成」（`GET /api/issues/done`，`personal/activity.py` 的 `done_all`）不按项目逐个查，用两个跨仓库 GraphQL 搜索：`is:issue is:closed author:luochen211 closed:>=<30 天前>`（带关闭事件）和 `is:pr is:merged author:luochen211 merged:>=<30 天前>`（带它关闭的 issue），每页 100 条，再按仓库归到项目，每个仓库照项目详情页的规则算。第一次和每 6 小时整读 30 天，其余每 10 分钟只读上次以来新关闭、新合并的（一般两个请求），并进 `.personal/done-issues.json`；过期先给旧值、后台刷新，限流时不发请求并写明。各项目详情页已经读到的结果一起并进来，同一条 issue 只留一条。`/api/issues` 列 open issue 时，这些结果和 GitHub 消息里看到关闭的 issue，关闭时刻不早于列表更新时间的就不列。

页面「每 60 秒刷新」默认开启，只读本机接口；GitHub 请求仍由上面的缓存决定。停在项目详情时，每个缓存最多 2 分钟读一次 GitHub（issue 一份、开着的 PR 一个搜索，每小时各约 30 次）；停在其他页每 5 分钟一次（每小时约 12 次）；已合并的只在有新合并或关闭时重读。GraphQL 每小时额度 5000 点，搜索每分钟上限 30 次，都远低于限额。

issue 列表用 GraphQL 一次读多个仓库的 open issue，不用搜索接口；上游仓库只用一个跨仓库查询 `involves:luochen211 is:open` 读与我有关的 issue 和 PR，另一个查询读近月合并的我的 PR。

## 最近一个月的项目范围

```sh
python3 -m personal refresh-commits
```

按当前本机日期向前一个日历月的当天零点起计算。例如 2026-11-05 核对时，范围从北京时间 2026-10-05 00:00 起。页面底部提供同一核对入口。

核对结果在 `.personal/commits.json`，会过期（新开工的项目不会自己出现）。所以服务启动时、以及运行期间每小时醒来看一次：`checked_at` 超过 24 小时（上次 GitHub 没读到时超过 1 小时）就在后台重新核对，不阻塞页面，核对完下次读页面就是新范围；核对过一次后至少隔 1 小时再核对。只有第一次启动、还没有 `commits.json` 时才先核对再开服务。页面的 60 秒刷新只刷新 issue 和 PR 数据，不扫描个人提交。

读取本地本人作为作者的提交、GitHub 本人提交记录，以及近一个月本人在上游仓库创建的 PR 和 issue（开源协作往往没有本人提交）。本地记录按作者身份和作者日期核对，防止把本人作为 committer 的他人提交或旧提交的近期 rebase 当成新的本人提交。GitHub 的旧用户名和个人 fork 上游仓库通过可信提交别名核对。评论、评审、会话、分支操作和未提交修改不进入判定。

核对时还会自动发现：近一个月本人创建过 PR 或 issue、但登记表里没有的上游仓库，追加到数据目录的 `projects.local.toml`，进「开源协作」组，`issue_scope = "mine"`。本地有检出的按 remote 匹配填 `repo_path`，扫描目录在登记表的 `[discovery] roots`；只认主检出，不把 worktree 当项目；没有检出的 `repo_path` 留空。

暂缓项目保留在登记表中，但项目列表、统计和搜索不处理它们；没有“显示全部历史项目”的兼容入口。重新核对出现新的有效提交后再纳入。若 GitHub 提交读取失败，会明确报告，当前只采用本地已核对的证据。

## GitHub 消息收件箱

GitHub 通知在左侧导航「收件箱」里，导航上的数是「待你回」条数。使用本机 `gh` 的当前登录身份，先确认 `gh auth status` 和 `gh api notifications` 能读取通知。

- `POST /api/github/notifications/sync`：分页读取 GitHub 已读及未读通知，保存到数据目录的 `github-notifications.json`。
- `GET /api/github/notifications`：读最近一次完整同步结果，并按当前提交范围重新计算归属。
- `GET /api/github/notifications/<线程编号>`：只读最新回复或主题正文，不标记已读；不支持正文的通知明确提示查看原文。

消息接入的 GitHub 请求全部固定为 GET。只允许读取 GitHub API 上同一通知仓库的受支持资源路径，通知内容不能指定其他主机或改变执行配置。已登录凭据由 gh 管理，不在本项目保存。浏览器 Host 校验和写入口 Origin/请求头校验覆盖消息接口。

同步结果（账号、时刻、线程编号清单和每条通知原文）存在 `github-notifications.json`，临时文件写完再改名；重复同步只覆盖这一份，不建任务。归类每次读页面时按当前登记表重新算。旧版存在任务库 `tasks.sqlite3` 的 `intake_records` 里的通知，第一次读时只读导入一次（见 [docs/GitHub消息接入.md](docs/GitHub消息接入.md)）。

按可信项目仓库精确匹配优先，再匹配旧名称与协作别名；多个匹配显示待核对。没有近期本人提交的项目进入其他消息，不自动激活。通知读取和项目范围相互独立。

收件保留 GitHub 当前可见的完整同步结果，成功后更新本机元数据；读取失败不会清空上次结果。进入收件箱先展示缓存，缓存超过 5 分钟时自动同步，也可点「同步消息」。通知正文按纯文本显示。当前 GitHub 连接器没有通知读取接口，因此产品运行时使用 gh CLI。

## 上游仓库（开源协作）

`issue_scope = "mine"` 的项目只读与我有关的 issue 和 PR（本人创建、被指派、被 @ 或评论过），不整仓读取。收件箱里这些 issue 和我的 PR 跟别的仓库一样列出、按同一套筛选归类。监看不在上游仓库按 `agent` 标签派活（别人打的 `agent` 标签也不算交给）。这些 issue 照样有「交给 Agent」按钮，走下面「上游仓库的 issue 交给 Agent」。

## 交给 Agent（agent-autoloop）

收件箱里勾选待动手的 issue 交给 Agent，或在 issue 详情里点「交给 Agent」（`POST /api/issues/<owner>/<repo>/<编号>/handoff`）：工作台用 `gh` 给 issue 打 `agent` 标签（仓库没有这个标签时先建），记下交给时间，再运行 `agent-autoloop emit issues.check` 马上触发一次监看。中台没接上时标签照打，工作台的监看闸门（见下）10 分钟内再触发一次。写入口同样要求本机 Origin、自定义请求头和 JSON。

收件箱里勾选的 issue 一批送 `POST /api/issues/handoff`（`{"items": ["owner/repo#N", …]}`，一次最多 50 条，页面每 20 条一批；一次超过 10 条页面先确认）：`personal/issues.py` 的 `batch_handoff` 逐条看收件箱的筛选，只收「待动手」「待你回」两类，再过和单条接口同一个 `next_step.allowed`；issue 留言流程的（待你回，不论哪个仓库）一起交给 `conversation.handoff`，「处理 issue」流程的自己仓库打标签、上游仓库记 `upstream-handoffs.json`，这两种逐条记账时不触发中台，全部记完只发一次 `issues.check`。一条失败不影响别的，返回 `{results: [{subject, ok, flow, handed_at, error}], handed, routed, error}`，页面把跳过的条目和原因写在提示里。

### 监看闸门（`personal/watch_gate.py`，2026-11-19）

以前中台每 10 分钟跑一轮监看：两周 1,679 轮，占中台全部轮次的 79%，没有一轮派出过新东西（派出的都来自交给时触发的「立即看 agent issue」），每轮 6 个 GitHub 请求（1 个搜索、1 个读评论、4 个按仓库读 agent PR）。现在一份 GitHub 状态只读一次，没变化不进中台：

- **一个请求读全。** 监看要的 GitHub 状态合成一个 GraphQL 请求（三个搜索）：带 `agent` 标签的 open issue（带评论）、本人 `agent/issue-<编号>` 分支开着的 PR、近一周合并的这类 PR。结果写进 `~/.agent-autoloop/issues/github-snapshot.json`。
- **闸门。** 工作台后台线程每 5 分钟读一次，和监看上一轮处理过的状态（`gate.json` 的 `seen`，监看每轮结束时写）比：带标签的 issue 有增减，agent PR 的提交、草稿、CI 结论、本人的审查评论、合并有变化，就发一次 `issues.check`（id `gate:<时刻>`）。正文、标题、别人的评论变了不算（监看不会因此多派）。整台机器只开一个闸门（`gate.lock`），`WORKBENCH_WATCH_GATE=0` 不开。
- **到点复查。** 有些事 GitHub 不变也要再看：没有 CI 的提交放满 3 分钟才审、`WAIT` 一小时后重审、「处理 issue」还在跑（10 分钟后再看）、审查或返工卡住和 CI 太久的提醒。监看把最早的时刻写进 `gate.json` 的 `wake_at`，闸门到点触发。
- **没触发成的交给。** 交给记录（`handoffs.json`、`upstream-handoffs.json`、`pr-handoffs.json`、`issue-feedback-handoffs.json`、`learning-handoffs.json`、`review-handoffs.json`）里交出超过 5 分钟、还没报给中台（不在 `emitted.json`、`repeats.json`）的，闸门触发一次，同一批只触发一次。
- **不重复触发。** 触发后监看还没跑完一轮的，30 分钟内不再触发；中台没开、`emit` 失败的，下一次再试。GitHub 限流中闸门跳过。
- **监看接着用快照。** 监看读 GitHub 先看快照，4 分钟内的直接用（闸门刚触发的那一轮一个请求都不发），过期才自己读（每小时兜底那一轮一般 2 个请求）并写回。
- 巡检状态（「Agent」页）多一行：闸门多久看一次、最近一次看和触发的时间、下次到点复查。

实测（2026-11-19，真实数据的临时副本空跑）：旧监看每轮 6 个请求、每小时 36 个、中台每天 144 轮；现在闸门每小时 12 个请求，没有变化时中台每天只有 24 轮兜底，有变化的那一轮 0 个请求，回执和旧代码逐字相同。

### 上一轮已有结论的不再派（`personal/settled.py`，2026-11-19）

Agent 最近一轮（「处理 issue」「处理 issue 留言」「处理 PR 反馈」里新的那一轮）已结束、结论是**要你定**（结果行 `BLOCKED`，或还有没答的【问你】）或**不用动**（`NO_CHANGE`、`NO_ACTION`，或中台记成 nothing），而这一轮开始之后别人没有新的留言、评审、提交，这一轮结束之后你也没再留言：

- 收件箱改判：要你定的落「待你回」，细类「待你定」，行上写 Agent 要你定的那一句；不用动的落「待你看」，细类「Agent 看过」，行上写 Agent 的结论。都不出列表按钮、不能勾选批量交，也不出「只需回复 / 需要处理」的建议。PR 行（没有 issue 那一行时）留在原来的类别，同样不能勾，行上写结论。
- 交给接口（单条、批量、`/api/prs/handoff`）不带 `force` 就拒绝，原因写明第几轮、结论和怎么放开；详情里的「仍交给 Agent」带 `"force": true`。force 交的记进交给记录（`handoffs.json` 写成 `{handed_at, force: true}`，其余账本加 `force`）。
- 监看（`agent_watch.repeats`）再核一次：第一次要报的 `issues.opened`、`issues.feedback`、`prs.feedback`，最近一轮已有这两种结论、交给记录里交给时的动静（`their_at` / `their_last`、`mine_at`；带标签的 issue 用这一轮读到的评论）都不晚于那一轮、又不是 force 的，不报给中台，记进 `~/.agent-autoloop/issues/repeats.json`（两天后删），回执第一行写「上一轮已有结论、之后没有新动静，不再派」。读不到中台照派。
- 「帮我回复」那一轮回执第一行写了「这条要改代码……用『处理并回复』再交一次」：issue 和 PR 行带 `switch`，草稿里出「改用处理并回复」（`POST /api/replies/regenerate` 带 `"mode": "handle"`，交出去后收起那份只回复的草稿），没有草稿时详情里出同名按钮（交给接口带 `mode: handle`，不受等对方回的把关）。

本机中台 agent-autoloop 的规则在 `~/.agent-autoloop/rules.json`（`python3 -m personal.agent_watch install` 会补上审查和返工要的几处：Agent `claude-review`、规则「审查 PR」、「处理 issue」接 `issues.rework`、两条监看规则能发 `pr.review` 和 `issues.rework`；已有的不动，有改动先备份成 `rules.json.bak-<时间>`，中台发现文件改了会自己重读）：

1. **看带 agent 标签的 issue**：每小时运行一次 `~/.agent-autoloop/issues/watch.py`（定时 `5 * * * *`），是工作台没开或闸门出错时的兜底；平时由工作台的监看闸门按需触发第 2 条。
2. **立即看 agent issue**：收到本机来源的 `issues.check` 事件时运行同一个 `watch.py`：工作台交给时（id `handoff:…`）和监看闸门发现变化时（id `gate:<时刻>`）。
   两条监看规则的 `emits.types` 都要有 `issues.opened` 和 `prs.feedback`，`install` 缺了会补上。
3. **处理 issue**：按技能 handle-issue（正本在本仓 `personal/skills/handle-issue/SKILL.md`，`python3 -m personal.agent_watch install` 把 `~/.claude/skills/handle-issue` 装成指向它的符号链接；原来是真目录就挪到 `~/.claude/skill-backups/` 备份；工作台启动时发现这个链接悬空或没指向 `paihuo-root` 的仓库会重新链接，真目录不动）先用 `gh issue view --comments` 读全评论和关联 PR（事件 detail 受中台 4000 字上限，正文截到 1500 字、评论只带最近几条；评论里已回复、已有 PR 做完的不再让人重做），再在 issue 所在仓库新开工作区（`/Volumes/Luochen/worktrees/<repo>-issue-<编号>`，分支 `agent/issue-<编号>`，从默认分支拉出，不动本地检出），改好、跑测试、提交、推送并开 PR（`Closes #编号`），等 CI 结果，然后发通知汇报 PR 链接。不合并、不改标签。缺人定的事整条 issue 做完后收成一个决定包，在同一张回执里一次问完：每问带推荐，标题开头写档位，【问你】等本人定、依赖它的部分先不做，【默认过】这一轮已按推荐做完、不改就算定了；代码查得到的事不问。工作台在 issue 详情里一屏答完（默认选中推荐项，点按钮才送出，回车和空格不会送出），按顺序逐条交给中台；中台没有一次交多条回答的接口，每条各排一轮、只并进还在排队的那一轮，所以一个包可能分成同一个 PR 上的几轮处理，Agent 每轮只按拿到的那几条做，不重问剩下的；回答轮即使没有新提交也以 `RESULT: PR=<号>` 结尾；只剩【默认过】没答的 issue 算「等你看」。回执第一行仍是一句话结论，最后一行固定是 `RESULT: PR=<号>`、`RESULT: NO_CHANGE` 或 `RESULT: BLOCKED=<原因>` 之一（思路借自 ai-daming/daming-skills 的 issue-pr-pipeline，MIT）。

4. **审查 PR**：监看每轮还读本人从 `agent/issue-<编号>` 分支开的 PR（GraphQL 搜索 `is:pr author:@me head:agent`：开着的，和近一周合并的，再按仓库和分支名筛）以及 gh 当前登录的账号，按技能 review-pr（正本 `personal/skills/review-pr/SKILL.md`，`install` 一样链到 `~/.claude/skills/review-pr`）派给单独的 Agent `claude-review`（中台里独立的会话；模型在 `rules.json` 的 `agents.claude-review.model` 改，`install` 不会覆盖）。思路借自 ai-daming/daming-skills issue-pr-pipeline 的 Review Agent（MIT）。
   - **安全边界：只审本人的 PR。** 审查 Agent 会在本机装依赖、跑测试，等于执行 PR 里的代码，所以只审 PR 作者是本人账号（gh 当前登录的账号）、从同一仓库的分支开的 PR（`isCrossRepository` 为假）；别人开的、fork 来的、字段读不到的都不审。仓库默认只审本人账号（`[owner] login`）名下的；别的仓库要在 `personal/projects.toml` 给这个项目写 `review_prs = true` 才审（默认关），没开的仓库连 PR 都不读。审查评论也只认本人账号留的，别人贴的标记和结论不算，不会变成返工指令。
   - **审查独立只是约定，没有技术上的拦截。** `claude-review` 和「处理 issue」一样是 `access: full`（中台映射成跳过权限确认），因为审查要装依赖、跑测试，中台没有「能跑测试、能发评论、但不能推送」的中间档。「不改代码、不推送、不合并」只写在技能里。监看每轮核对一次，各给本人发一次提醒：审查派出后、审完之前 PR 有了新提交；agent PR 被本人以外的账号合并；审查或返工派出后、对应结论还没出来（审查还没留这个提交的评论，返工还没推新提交）PR 就合并了。Agent 用的就是本人的 gh 账号，所以「谁合并的」查不出是不是 Agent 合并的，只能抓到别的账号；第三条按时间先后判断，不管是谁合并的都提醒。
   - 审查只对一个提交：事件 `pr.review`，id 是 `review:<owner/repo>#<PR>@<head 前 12 位>`，link 是 `…/pull/<PR>/commits/<完整提交>`（中台的 `/v1/state` 不带 detail，工作台靠 link 知道审的是哪个提交）。PR 有新提交就会再审。草稿不审；这条 issue 的「处理 issue」还在排队或在跑时不审（中间提交不审，免得白白消耗返工轮次；同一轮刚派出的 issue 也算）；没读到中台就这一轮不派审查；CI 没过不审（由「处理 issue」那一轮修）；CI 还在跑不审；没有 CI 的提交放 3 分钟再审。
   - **派出记录。** 中台按 (来源, id) 永久去重，同一个 id 再发只会被静默丢掉。所以监看把派出的审查和返工记进 `~/.agent-autoloop/issues/dispatched.json`（读-改-写加文件锁，和 `handoffs.json` 一样；`sent` 是事件 id → 派出时间、PR、提交，`told` 是发过的提醒，`holds` 是每个 PR 暂时不审的原因，`redo` 是手动重审的次数），派过的 id 不再放进事件，回执第一行也只写这一轮真派出的。记录 30 天后清掉（PR 还开着的留着）。
   - **中台规则没补齐就不派。** 中台会拒收规则 `emits.types` 里没写的事件类型，派了却被拒，派出记录又记成派过，就再也不会审。所以每轮派审查和返工前先读 `~/.agent-autoloop/rules.json`，核对：两条监看规则的 `emits.types` 都含 `pr.review` 和 `issues.rework`、有规则接 `pr.review`、「处理 issue」接 `issues.rework`（来源都和监看发事件的来源一致）。缺一处就这一轮不派、不记、也不读 PR，回执第一行写「中台规则还没补，先运行 install」；补好后下一轮照常派。
   - **卡住了只提醒一次，不自动重派。** 审查派出 2 小时后 PR 上还没有这个提交的审查评论（审查没跑、`gh pr comment` 失败、评论格式不对），或返工派出 2 小时后 PR 还停在原来的提交，给本人发一次桌面提醒，不换 id 重派：重派大多还会卡在同一处，交给本人看更省。**恢复办法**：事件 id 被中台永久去重，删 `dispatched.json` 里的条目没用（监看会再发同一个 id，中台照样丢掉）。往 PR 推一个新提交，head 变了就会按新 id 重新审；不想改代码时运行 `python3 -m personal.agent_watch rereview <owner/repo>#<PR号>`，它给这个 PR 的审查 id 加一个新后缀（`+<次数>`）并马上触发一次监看，监看照常过 CI、「处理 issue」在跑等门槛后派出（已经有「通过」「要改」结论的提交不重审）。返工卡住没有这个命令：看清原因后推新提交，或在 issue 上再交给 Agent。CI 没过（同一提交一次）、CI 1 小时还没出结果、`WAIT` 满了，也各提醒一次；工作台的审查进度写出原因（例如「CI 没过，未审查」）。
   - 审查 Agent 在只读工作区看这个提交、跑检查，在 PR 上留一条评论：带 `<!-- paihuo-review head=<完整提交> -->` 标记，最后一行 `VERDICT: APPROVE | CHANGES_REQUIRED | WAIT`（容忍末尾多一个代码块围栏、加粗、句号或全角冒号）。派出后 PR 又有新提交的，它不审、不留评论，回执最后一行写 `VERDICT: STALE`，工作台显示「提交已更新，等重新审查」。它不改代码、不推送、不合并。
   - `CHANGES_REQUIRED`：监看派 `issues.rework`（id `rework:<owner/repo>#<PR>@<轮次>`，subject 是 issue），「处理 issue」按技能里「返工」一节回到同一分支和 PR 改，推送后再审。轮次按派出记录数，不按评论数（评论只读最近 100 条）。最多返工 3 轮；第 4 次还要改就不再派，工作台显示等你决定。返工一处都不改、没有新提交的，结果行写 `BLOCKED`，工作台算等你决定；返工那一轮结束了、head 还是派返工时的提交（比如误写了 `RESULT: PR=`），工作台同样算等你决定，不一直显示「返工第 N+1 轮」，因为监看不会对同一提交再派返工。
   - `WAIT`：一小时后再审同一个提交；同一提交在第一次之外最多再审 3 次（共 4 次），第 4 次还是 `WAIT` 就停下并提醒一次。
   - `APPROVE`：审查 Agent 发一条「审查通过」的通知，等本人合并。Agent 都不合并。
   - 读 PR 失败不影响派 issue，回执第一行写明，下一轮再看。

`watch.py` 是薄入口（`personal/watch_entry.py`），逻辑在 `personal/agent_watch.py`：用一个 GraphQL 搜索读 `personal/projects.toml` 里所有仓库带 `agent` 标签的 open issue（带最近 100 条评论），作为 `issues.opened` 事件交给中台（detail 带正文和评论：评论随搜索一起读到，超过 100 条的才单独读全，放不下时留最近的并写明全部的读法，读评论失败照派并注明）；事件 id 是 `owner/repo#编号`，中台去重。仓库位置不写死：按环境变量 `PAIHUO_ROOT` 或同目录的 `paihuo-root` 文件找；工作台启动时发现 `paihuo-root` 失效会改成当前仓库，搬盘后也可以在新位置运行 `python3 -m personal.agent_watch install`。

- 第一次运行把已有的带标签 issue 记进 `baseline.json`（带记录时间），不派（显式交给的照派）。之后标签晚于记录时间才打上的、或在页面显式交给的，照常派。旧格式的纯列表用文件修改时间当记录时间。
- 确认「标签早于记录时间」的结论记在 `held.json`，之后不再每轮分页读 events；issue 掉出搜索结果（标签被摘掉）就删掉结论，重新打标签后再核对。没读到 events 时不记结论。
- 显式交给记在 `~/.agent-autoloop/issues/handoffs.json`（读-改-写加 `fcntl` 文件锁，并发交给不丢记录），事件 id 加上交给时间，同一条 issue 处理完后可以再交一次。
- 连续 3 次没跑成发一次桌面通知（`watch-state.json` 记次数），成功后清零；`paihuo-root` 找不到仓库时也通知一次。

工作台阶段在下面六个之外加两个：「审查中」（「审查 PR」规则在跑、且在最近一轮处理之后）和「返工中」（返工那一轮在跑，或审查要改、还没满 3 轮，页面写成「返工第 N 轮」，N 取返工事件摘要里的轮次）。首页计数也有这两格。审查结论只认审查回执最后一行的 `VERDICT`，和结果行一样从 worker 输出读、记进 `.personal/run-results.json`；结论只对审的那个提交有效：工作台读 issue 列表的同一个 GraphQL 请求顺带读 agent PR 现在的 head，和审查事件 link 里的提交比，不一样就写「提交已更新，等重新审查」，阶段也不按旧结论算（决定包的回答轮排在审查之后、但没有新提交时，结论照样算数）；旧事件不知道审的提交时退回比轮次。审查要改且已返工 3 轮算等你决定，审查通过或等待算等你看。返工轮数取派出记录和中台轮次里较大的。issue 详情在处理结论下面写审查进度（审的提交前 7 位、结论、已返工几轮、PR 链接，或暂时不审的原因；审查一轮没跑完写「这次审查没跑完」），运行记录里审查和返工的轮次单独标出。审查留下的通知（审查通过、返工满了）和「处理 issue」的一样挂在这条 issue 上。

页面的处理进度按 `GET /v1/state` 读中台（本人密钥取自 `~/.agent-autoloop/config.json`），向前翻页直到上次确认都已结束的轮次，不只看最近 60 轮：未交给 Agent → 已交给 Agent → 处理中 → 等你决定 / 等你看 → 已处理。一轮结束后只认回执最后一行的结果行：中台只存第一行，全文从本机 worker 的输出（`~/.agent-autoloop/worker/runs/<轮次>-<第几次>/stdout-<第几回>.log`，留 14 天）读，读到的结果记进 `.personal/run-results.json`。`BLOCKED` 算等你决定并写原因；`PR=` 算等你看，详情里给 PR 链接；`NO_CHANGE` 才算已处理（还有没看的通知时仍是等你看）；没有结果行、读不到回执、这一轮没跑完（cancelled）都算结果不明，归等你看。刚交给的 issue 每 10 秒刷新一次进度。「已交给 Agent」一般禁用按钮，两种情况放开、显示「再交给 Agent」：只有 `agent` 标签、没有运行记录（启用监看前就带标签、在 `baseline.json` 里的 issue 不会自动派出，显式交给才放行）；显式交给后超过 15 分钟还没有新的一轮，提示「派发可能丢了，可重试」。
页面的处理进度按 `GET /v1/state` 读中台（本人密钥取自 `~/.agent-autoloop/config.json`），向前翻页直到上次确认都已结束的轮次，不只看最近 60 轮：未交给 Agent → 已交给 Agent → 处理中 → 等你决定 / 等你看 → 已处理。一轮结束后只认回执最后一行的结果行：中台只存第一行，全文从本机 worker 的输出（`~/.agent-autoloop/worker/runs/<轮次>-<第几次>/stdout-<第几回>.log`，留 14 天）读，最后一回出错时退回上一回，读到的结果连同这一轮的结束时间记进 `.personal/run-results.json`。`BLOCKED` 算等你决定并写原因；`PR=` 算等你看，详情里给 PR 链接；`NO_CHANGE` 才算已处理（还有没看的通知时仍是等你看）；没有结果行、读不到回执、这一轮没跑完（cancelled）都算结果不明，归等你看。刚交给的 issue 每 10 秒刷新一次进度。「已交给 Agent」一般禁用按钮，两种情况放开、显示「再交给 Agent」：只有 `agent` 标签、没有运行记录（启用监看前就带标签、在 `baseline.json` 里的 issue 不会自动派出，显式交给才放行）；显式交给后超过 15 分钟还没有新的一轮，提示「派发可能丢了，可重试」。

### 上游仓库的 issue 交给 Agent

同一个接口 `POST /api/issues/<owner>/<repo>/<编号>/handoff`，项目是 `issue_scope = "mine"` 的就不打标签（`personal/issues.py` 的 `upstream_handoff`）：

- 先确认中台有「处理 issue」规则接 `issues.opened`、监看规则能发 `issues.opened`（`agent_watch.issue_rule_missing`），不满足就报错、不记账；issue 已关闭也不交。
- 读这条 issue 的全部评论（一次 `gh issue view --json comments`，经 `personal/github.py`；读失败照交并在 detail 里写明，限流时不交），写好 detail：第一行「范围：上游仓库……」，下面是仓库、项目、本地目录（没有检出时为空）、issue 链接、正文和评论，不超过 4000 字。
- 在 `~/.agent-autoloop/issues/upstream-handoffs.json` 的 `fcntl` 锁里重读账本：删掉 7 天前的记录；这条还在派（交给不到 15 分钟、没有新的一轮）或在处理就报错不记（双击也只记一次）；否则记下交给时间、标题、链接和 detail。
- 再发一次 `issues.check` 触发监看；中台没接上时记录保留，工作台的监看闸门 10 分钟内再触发一次。

监看每轮把一天内交给的上游 issue 作为 `issues.opened` 事件交给中台（id 是 `upstream:owner/repo#N@交给时间`，再交一次会重派；不读 GitHub，带标签的 issue 没读到也照派），顺手删掉账本里 7 天前的记录。第一次报的才写进第一行「派出 N 条 上游 issue：…」，之后几轮照样带着事件给中台去重，但不再写「派出」（`emitted.json`）。规则「处理 issue」用技能 handle-issue 接它，技能看到 detail 第一行的「范围：上游仓库」就走「上游仓库」一节：核对 gh 账号，查自己已有的 PR（开着的不新开、已合并的不做）和别人在不在做，读贡献规矩，fork 到 luochen211 并核对 parent，从上游默认分支最新代码开工作区（`/Volumes/Luochen/worktrees/<owner>-<repo>-issue-<编号>`，分支 `agent/issue-<编号>`，同名已有就加 `-2`、`-3`），推到 fork，`gh pr create --repo <上游> --head luochen211:<分支>`，正文写 `Fixes #编号`。阶段（已交给 Agent / 处理中 / 等你决定 / 等你看 / 已处理）和结果行的读法跟自己仓库的 issue 一样，交给时间取自这个账本。

Agent 对某条 issue 的提问和处理结果只认「处理 issue」规则留下的（中台把它们和别的规则的一起放在 `/v1/state`）：创建时间等于那一轮交回执的时间，或正文写着 `owner/repo#编号`。在 issue 详情里回答（`POST /api/questions/<编号>/answer`），工作台调用中台协议的 `POST /v1/cards/<编号>/answer`（`agent-autoloop answer` 背后就是它；它的命令行把 `--` 开头的文字当成选项，所以不经命令行），只接受当前项目 issue 的提问。

另外三页：

- Agent 运行记录（`GET /api/autoloop/runs`）：全部规则的运行，新的在前，带规则、issue、结果、耗时；失败的写原因。已从中台 `~/.agent-autoloop/rules.json` 删掉的规则（比如以卡片为单位的旧规则）留下的轮次默认不列，只写条数；读不到 `rules.json` 时不按规则隐藏。
- 巡检状态（`GET /api/autoloop/watch`）：两条监看规则最近几轮、下次定时（取自 `agent-autoloop rules explain`）、`watch-state.json` 里的连续失败次数。失败的轮次只列当前的：同一条巡检规则之后已经跑成过的失败不再列，运行记录里照样查得到。
- 事项（`GET /api/boards`）：助理目录 `事项/` 下各板块的状态和下一步；板块有 `动态.md` 时另给最近 5 条按日期的条目（`## YYYY-MM` 月份段下 `- MM-DD：…` 的行），卡片里显示为「最近动态」。

浏览器关页或刷新时断开连接产生的 BrokenPipe、ConnectionReset 不写进 `server.log`（只有开了 debug 日志才记一行），其他异常照常打印堆栈。

## 收件箱的 PR 行（交给 Agent 处理 PR 反馈，原「PR 待处理」页）

`GET /api/prs/feedback`（`personal/pr_feedback.py`）：开着的、本人开的 PR 取自上面那个跨仓库查询 `involves:luochen211 is:open`（同一份 5 分钟缓存），归档项目的仓库不列，没登记的仓库照列。每条 PR 的时间线（评论、评审和行内意见、提交、转草稿 / 转可审；行内意见按发布时间算）几条一个 GraphQL 请求读，按 PR 的 `updatedAt` 缓存，没有新动态不重读；限流时不发请求。本人开着的 PR 全列（还没读到时间线的先不列），每行带 `state`：对方真人最后一句晚于本人最后一个动作是 `reply`（待你回；在别处引用这个 PR 不算本人回应），等你决定、等你看、派发可能丢了也是 `reply`，已交给还在派、正在处理是 `agent`，其余开着的是 `wait`（等对方），已合并或关闭的是 `done`；`new` 是本人最后一个动作之后对方新增的条数，`wait` 和 `done` 为 0；每条回复带身份，OWNER / MEMBER / COLLABORATOR 或被请求的评审人算维护者，其他人照样列出并标明；机器人（Bot、`[bot]`、coderabbitai、codecov、github-actions、vercel、claude 等）不算。

`POST /api/prs/handoff`（`{"prs": ["owner/repo#N", …]}`，一次最多 20 条）：重读这些 PR，只收本人开着的，先确认中台已有「处理 PR 反馈」规则（没有就提示先 install，不派），在账本锁里重读、跳过已在派或在处理的（双击也只记一次），删掉 7 天前的旧记录，逐条记进 `~/.agent-autoloop/issues/pr-handoffs.json`（加 `fcntl` 文件锁，带交给时间、当时对方最后一句的时间和写好的 detail），再发一次 `issues.check` 触发监看。监看每轮把一天内交给的 PR 作为 `prs.feedback` 事件交给中台（id 是 `pr:owner/repo#N@交给时间`，再交一次会重派；不读 GitHub，issue 没读到也照派），规则「处理 PR 反馈」用技能 handle-pr-feedback 处理。回执最后一行是 `RESULT: PUSHED=<短提交号>`、`CLOSED`、`REPLIED`、`NO_ACTION=<原因>` 或 `BLOCKED=<原因>`。处理完（前四种）且对方没有晚于交给时那一句的回复，就收进「最近处理过」（7 天）；等你决定、等你看、已交给的，你自己在那一轮结束（或交给）之后动过手、对方没有新回复，也收进去。回执写推送或回复、时间线里却没有本人在这一轮之后的动作的，降为等你看。

`python3 -m personal.agent_watch install` 会把 `~/.claude/skills/handle-pr-feedback` 链到本仓，并改 `~/.agent-autoloop/rules.json`：两条监看规则的 `emits.types` 加上 `prs.feedback`，在「处理 issue」后面照它加一条「处理 PR 反馈」（同一个目录）。没有「处理 issue」可照抄就都不改。改之前备份成 `rules.json.bak-<时间>`，已经有的不改（Agent 和 `parallel` 见下一节「并行和模型」）。

## 待你回的 issue 留言流程和 Agent 计数

`GET /api/agents`（`personal/conversation.py` 的 `agents`）：`{running, queued, unknown, limit, parallel, capacity, agents: [{rule, subject, started_at, state}]}`，取自中台 `GET /v1/state` 全部规则里没结束的轮次，两条监看规则（脚本）不算。`claimed` 算在跑，`queued` 算排队；`unknown` 是交给后超过 5 分钟没有回音、中台不确定做没做完的，单独数。`limit` 是 `parallel`（`rules.json` 里两条反馈规则的值）和 `capacity`（本机接单程序容量）里小的那个。收件箱顶部那一行用它，有 Agent 在跑或排队时每 10 秒读一次。

谁最后说话（`conversation.assess`）issue 和 PR 共用：别的真人最后一条留言晚于本人最后一条留言就是待你回；本人没留过言时，issue 是本人开的就和开 issue 的时间比，不是本人开的别人的留言都算，只有正文没人留言的也算（`body_only`）；机器人和非维护者的邮件自动回复不算。

待你回 issue 的「帮我回复」「处理并回复」经 `/api/issues/handoff` 和单条接口走 `conversation.handoff`：收登记了、没归档的全部项目的仓库（`conversation.handoff_repos`）。上游仓库（`issue_scope = "mine"`）的 issue detail 第一行写「范围：上游仓库」，技能照它走上游流程。先不用缓存读一次这些编号，分出 PR 和 issue，确认中台有要用的规则（没有就提示先 install，一条不记）。PR 走 `pr_feedback.record`，记进 `pr-handoffs.json`；开着的 issue 在 `~/.agent-autoloop/issues/issue-feedback-handoffs.json` 的 `fcntl` 锁里重读账本、跳过已在派或在处理的（双击也只记一次）、删掉 7 天前的旧记录，逐条记下交给时间、当时对方最后一条留言的时间和写好的 detail（仓库、issue 链接、标题、本地目录、对方的留言并标出新的、全部的读法，不超过 4000 字）。最后只发一次 `issues.check` 触发监看。监看每轮把一天内交给的 issue 作为 `issues.feedback` 事件交给中台（id 是 `issue-feedback:owner/repo#N@交给时间`，再交一次会重派；不读 GitHub），规则「处理 issue 留言」用技能 handle-issue-feedback 处理：读全讨论，回答问题、做留言要的事（代码改动在 `/Volumes/Luochen/worktrees` 下的新工作区里改、开 PR，照仓库自己的 AGENTS.md / CLAUDE.md，不推默认分支、不合并），回复写成草稿等本人确认；只有本人能定的事放进回执的 `ask` 问本人。回执最后一行是 `RESULT: REPLIED`、`PR=<编号，逗号分隔>`、`NO_ACTION=<原因>` 或 `BLOCKED=<原因>`。

## 微信插件

第一次装按 [personal/plugins/wechat/README.md](personal/plugins/wechat/README.md) 的步骤做（准备读取工具和 Python、安装、开完全磁盘访问、登记群、确认装好了）。

微信这一整套是一个插件：收件箱的「微信」范围、后台刷新微信明文库、「新需求」里的微信来源、会话登记（`python3 -m personal.plugins wechat map/unmap/mappings`）。装上才能用，去掉就停用；页面上的按钮和位置不变，没装时只是不出现微信、写接口回 404、后台不刷新，「读新需求」只读录音。

```sh
python3 -m personal.plugins list
python3 -m personal.plugins install wechat --vault-dir <微信读取工具的目录> --python <它要用的 python>
python3 -m personal.plugins uninstall wechat   # 只停用，数据都留着，重新 install 就回来
python3 -m personal.plugins uninstall wechat --purge   # 连数据一起删，删了回不来
```

`--purge` 删掉数据目录里微信的文件（`wechat.json`、`wechat-pending.json`、`wechat-status.json`、`wechat-done.json`、`wechat-projects.json` 等，清单见 `plugin.json` 的 `data_files`），再清共用文件里微信的那部分：「暂时不处理」里 `wechat:` 开头的条目、「新需求」里来源是微信、还没创建的草稿。不动：读取工具和它的明文库、已创建或不创建的草稿历史（免得同一件事再拟）、`wechat-people.json`（录音也用）、数据目录外的抽取游标。删了配置，重新 install 要重新给 `--vault-dir` 和 `--python`。

装上时把插件的中台规则（微信：「微信拟 issue」）加进 `~/.agent-autoloop/rules.json`，去掉时删掉（只删名字、事件、Agent 都对得上的，本人改过的留着并提示）；改之前先用 `agent-autoloop check` 校验、再备份成 `rules.json.bak-<时间>`（同一秒里已有就加 `-2`、`-3`，不盖掉前一份），校验不过什么都不改。登记表是数据目录里的 `plugins.json`。还没有登记表时，数据目录里有 `wechat.json` 就当已装（老用户升级后照旧）；装过或去掉过一次就只认登记表。工作台在跑时不用重启，每个入口每次用之前都查登记表。

插件不带读微信库的工具：要自己准备一个本机读取工具，目录里有 `scripts/decrypt_all_dbs.py`（增量解密到它自己的私密明文库）和 `scripts/vault_cli.py`（`pending`、`history` 等子命令，JSON 输出），并自行遵守它的许可。读取工具在哪写在数据目录的 `wechat.json`，只在本机，不进仓库（install 第一次会写它，已有的不改）：

```json
{"reader": "vault", "vault_dir": "<微信读取工具的目录>", "python": "<装了 pycryptodome 的 python>", "since": "7d", "interval_minutes": 15}
```

公众号文章也是插件的命令（装了才能用，只读）：`python3 -m personal.plugins wechat articles [--days N] [--query 关键词] [--from 公众号] [--account 号] [--format json]`，`articles accounts` 列公众号。它从读取工具的明文库里读 `message/biz_message_*.db`（默认号的明文库问读取工具 `vault_cli.py status`，别的号取 accounts.json 的 `decrypted_dir`），用 `wechat.json` 里读取工具的 python 跑 `personal/plugins/wechat/articles.py`。库里只有标题、摘要、链接和时间，正文要按链接读网页；纯文字推送的正文在 JSON 的 `text` 里。

老配置里的 `skill_dir` 照样认，等同 `vault_dir`。`reader` 选读取方式，现在只有 `vault`（本地明文库读取工具，不写就是它）；插件其余部分只经 `personal/plugins/wechat/reader.py` 的几个方法读微信，要接别的读取工具就在那里加一个类并登记。收件箱「微信」范围读的是读取工具的漏看检查（`personal/plugins/wechat/inbox.py`）：

- `python` 要装了 pycryptodome；`since` 默认 `7d`，`interval_minutes` 默认 15；可选 `decrypt_timeout`（默认 900 秒）、`pending_timeout`（默认 300 秒）。
- 后台每 `interval_minutes` 分钟跑一轮（收件箱「一键读取」也会触发，不等它跑完；同一时间只跑一轮）：`decrypt_all_dbs.py --mode incremental`（只更新读取工具的私密明文库，不碰微信界面），再 `vault_cli.py --include-identifiers pending --since <since> --previous <上次结果> --format json`。结果去掉账号标识后写 `wechat-pending.json`（权限 600）。
- 解析因没有权限中止后，后台改为每 6 小时自动重试一次（每次中止读取工具都会在 vault 的 `manifests/` 多写一个文件）；开了权限后点「一键读取」立即重试。
- 一行是「一类信号 × 一个会话」：私聊待回、群里 @我、引用我、提到我、@所有人、待收转账、转账已退回、我转出未被收、红包、未接来电。只有私聊待回（疑似客套/表情的除外）、群里 @我、待收转账算「待你回」，计入导航角标；其余在「待你看」。
- 微信只读：「已处理」只记 `wechat-done.json`，这个会话有更新的消息才回来；「暂时不处理」和 GitHub 的行共用 `snoozed.json`（键 `wechat:<类>:<会话>`）。不交给 Agent、不写微信回复草稿、不发消息。聊天里的待办和需求可以拟成 GitHub issue 草稿，见下面「从微信拟 issue」。
- 后台进程读不到微信目录时（解析中止、manifest 有中止或过期的缺口），照样显示上次的数据，并提示到「系统设置 → 隐私与安全性 → 完全磁盘访问」给解开符号链接后的 Python 打开权限。
- 消息原文只在数据目录和本机页面里：不写 `server.log`，不进离线快照 `board.html`。
- 接口：`GET /api/wechat`（没装插件只回 `{"enabled": false}`），`POST /api/wechat/refresh`，`POST /api/wechat/done`（`{"key", "ts"}`；没装插件这两个回 404）。

### 写一个插件

插件放在 `personal/plugins/<id>/`，清单是 `plugin.json`（核心见 `personal/plugins/__init__.py`）。能写的字段：

- `id`、`name`；`legacy`：老数据的标志文件（还没有登记表时它在就当已装）。
- `routes`：`[{method, path, call, open}]`，`call` 写成「模块.函数」，GET 收 `(data)`、POST 收 `(data, body)`；没装时回 404，`open` 的照接，由插件自己回「没启用」。路由不能和核心重名。
- `threads`：后台线程的启动函数，收 `(data)`；工作台启动时都起，线程每次醒来自己查装没装。
- `hooks`：核心在固定时机调的函数，只调已装插件的。现在有 `needs.prepare`（「读新需求」发事件之前）、`needs.project_of`（草稿没写仓库时按来源找项目）、`needs.created`（未归类的草稿选了仓库并创建后）。
- `needs_sources`：「新需求」的来源，`[{id, event, rule, subject}]`，一次点击给每个已装、中台有规则的来源发一条事件；抽取脚本跑完用 `scan-done --source <id>` 交回。
- `autoloop`：`{agents, rules, replaces}`，install 加进中台、uninstall 删掉；`{root}` 换成本仓库目录；`replaces` 列插件接手之前的旧脚本，install 时把还指着它们的 Agent 换过来。
- `command`：`python3 -m personal.plugins <id> ...` 转给它，收 `(argv, data=, projects=)`；没装时拒绝。
- `data_files`、`purge`：`uninstall --purge` 删的数据目录文件，和清共用文件里自己那部分的函数（收 `(data)`，返回给人看的几行）。

前端不按插件分包：组件编进核心，按接口回的「没启用」不显示。

## 从微信拟 issue

聊天里的待办、需求拟成 GitHub issue 草稿，你确认后才创建（`personal/issue_drafts.py`）。工作台进程不读聊天内容、不调用模型，只在读之前刷新微信明文库，然后存、列、确认后创建；读聊天和提炼由微信插件的抽取脚本 `personal/plugins/wechat/extract.py` 做（中台派出的独立进程，问一个不带工具的模型），录音那一路由另一个来源的脚本做。两个脚本共用 `scripts/needs_common.py`（跑命令、问模型、提议新仓库的校验、交回结果）；换模型用环境变量 `NEEDS_MODEL_COMMAND`。

1. **登记会话**（白名单，只读登记了的）：`python3 -m personal.plugins wechat map --chat <会话名> --project <项目 id>`（在工作台仓库根目录运行），记进数据目录 `wechat-projects.json`（600）：`{"chats": {"<键>": {"project_id", "chat", "at"}}}`。键是会话名；你在页面给未归类的草稿选了仓库并创建后，会按草稿的 `chat_key`（账号标识的 md5 前 10 位，和 `personal/plugins/wechat/inbox.py` 同一算法）再记一条。工作台由会话名算不出 `chat_key`，所以两种键都认。`mappings` 列出全部，`unmap --chat <会话名>` 去掉，`python3 scripts/issue_draft.py projects` 列出能选的项目。
2. **读**：收件箱工具条右端（「同步消息」旁边）的「新需求」按钮打开右侧抽屉，点抽屉顶上的「读新需求」（`POST /api/issue-drafts/scan`）。每个来源一条本机事件、一条中台规则：录音是核心来源（`recordings.issues.scan` → 规则「录音拟 issue」，Agent `recording-issues` 运行 `python3 <助理目录>/scripts/recording_issues.py --no-refresh`，由 `python3 -m personal.agent_watch install` 加）；微信是插件的来源（`wechat.issues.scan` → 规则「微信拟 issue」，Agent `wechat-issues` 运行 `python3 <本仓库>/personal/plugins/wechat/extract.py --no-refresh`，`python3 -m personal.plugins install wechat` 时加、`uninstall` 时删）。都是 `use: command`，不进沙箱、不是 Claude。工作台在 `issue-drafts-scan.json` 按来源记 `running` 并立刻返回；中台没有某个来源的规则就这一轮不读它，写进说明；一个都没有就记失败并提示先 install。后台先增量刷新微信明文库（微信插件的 `needs.refresh_all`），再给每个来源发 `agent-autoloop emit <事件> --id scan:<来源>:<时间>`；已有一轮在跑（15 分钟内）不重复触发，某个来源发不出去就只记它失败。刷新放在工作台做，是因为中台派出的进程读微信的数据目录会被系统拒绝（`Operation not permitted`），工作台进程有这个权限：默认微信号走 `inbox.refresh`（只在有登记的会话在默认号里时跑，等它跑完）；`~/.local/state/wechat-issues/accounts.json`（`WECHAT_ISSUES_ACCOUNTS` 可换，`{"accounts": {"<名>": {"chats": [...], "decrypted_dir", "home", "python"}}}`）里登记了会话的号，各在自己的 `HOME` 下跑一次读取工具的增量解析（`python` 缺省取 `wechat.json`，超时 600 秒）。没刷新成的不拦这一轮，记进 `refresh_notes`（`GET /api/issue-drafts` 的 `scan` 里），脚本照读现有的明文库。
3. **交草稿**：微信的抽取脚本在进程里直接存草稿；放在别处的脚本用 `python3 scripts/issue_draft.py save <文件|->`（一条对象或数组）。每个来源跑完交回 `scan-done --source <来源> --found N --saved M [--skipped K] [--error 原因]`。页面每 4 秒读 `GET /api/issue-drafts`（`drafts`、`skipped`、`projects`、`scan`）；`scan` 是各来源合成的一份：条数相加，有一个来源还在跑就是 running，全部来源都没跑成才算失败，只有一部分没跑成写进 `refresh_notes`。读完在抽屉顶上写「读到 N 条，新拟 M 条草稿」，有没拟的话题再写「K 个话题没拟」，说明附在后面；一个来源 15 分钟没交回算它超时。微信脚本的游标在 `~/.local/state/wechat-issues/cursor.json`（`WECHAT_ISSUES_STATE` 可换），试跑：`python3 personal/plugins/wechat/extract.py --dry-run`。

   ```json
   {"source": {"chat_key": "…", "chat": "会话名", "kind": "group"}, "excerpt": [{"time": "…", "from": "…", "text": "…"}],
    "message_at": "2026-11-20T09:30:00+08:00", "repo": "owner/name（留空是未归类）", "candidates": ["项目 id"],
    "title": "…", "body": "…", "labels": [], "dedupe_key": "可选",
    "new_repo": {"name": "可选：提议新建的仓库名", "description": "一句说明"}}
   ```

   - `repo` 只能是登记表里有 GitHub 仓库、没归档、没暂停的项目；留空时这个会话登记过项目就直接对应，否则是未归类，在页面里选。
   - `new_repo` 和 `repo` 二选一：对应不到现有仓库的需求可以提议连仓库一起建。名字只收字母、数字和 `. _ -`（1 到 100 字，不以 `.` 开头），说明 200 字以内，都不能提 AI 工具、不能带原话。带 `new_repo` 的草稿不按会话登记的项目自动对应。
   - `dedupe_key` 不给就按 会话键 + 消息时间 + 标题 算。同一个键已有待确认的草稿或没拟的话题、已创建过、或你选过不创建，都跳过。
   - **没拟的话题**：模型认为和开发相关、但按规则没拟成草稿的，同样交来，多带 `"skipped": true, "skip_reason": "一句原因"`，存成 `status: skipped`，标题正文照样过检查。它们不算待评审、不进按钮上的数字，列在抽屉底部「没拟的话题」（一行：标题、没拟的原因；点标题看正文和原话）：点 ＋（`POST /api/issue-drafts/promote`）补成待评审的草稿，排到最前；点 ✕ 和不创建一样，同一件事不再拟。
   - 标题和正文不能提 AI 工具，不能带原话：`excerpt` 里 12 个字以上的一条整句出现在标题或正文里就拒收。`labels` 不收 `agent`。
   - 有写得不对的条目时退出码是 1，其余照存。
4. **确认**：草稿列在「新需求」抽屉里，一行一条（标题、目标仓库的标签、✓ 创建、✕ 不创建），不进收件箱的列表，不计入导航角标，待评审条数写在按钮上。按钮在启用了微信、或已有草稿、读过一轮时出现。点标题在行内展开：看依据的原话（只在本机）、改标题和正文、选仓库，下拉最后一项是「新建仓库…」（填仓库名和一句话说明，一律私有）。收起时点 ✓ 直接创建；要新建仓库的、还没选仓库的、上次没建成的，点 ✓ 只展开那一行，不会一键建仓库。创建成了那一行换成「已创建 · 在收件箱」，两秒后消失，新 issue 出现在收件箱里。「创建 issue」（`POST /api/issue-drafts/create`，`{id, title, body, repo, new_repo}`，`repo` 和 `new_repo` 二选一，都不给就用草稿上的）用你的账号 `gh issue create --repo … --title … --body-file -`，发出去的只有标题、正文、标签，不打 `agent` 标签；成功后草稿连同原话删掉，只在记录里留标题、仓库和 issue 链接。失败保留草稿和原因；gh 超时时提示先去仓库看一眼，免得重复创建。「不创建」（`POST /api/issue-drafts/reject`）删掉草稿，同一件事不再拟。
   - **录音**：同一轮里抽取脚本还读新的录音和会议转写（得到的 `getnote`、飞书妙记的 `lark-cli minutes`、投放目录里的录音文件用本机 `whisper-cli` 转写，见助理仓库 `规范/定时任务.md`）。这些草稿的 `source.kind` 是 `getnote` / `lark` / `audio`，展开后第一行写来源和录音标题；看不出属于哪个项目的不归类，在下拉里选仓库，选了也不记进会话白名单。抽屉顶上的「上传录音」选一个音频文件（m4a、mp3、wav、aac、flac、ogg、opus、mp4、mov、webm，500 MB 以内）：`POST /api/issue-drafts/audio` 把它存进投放目录（文件名前加时间），接着读一轮，抽取脚本在本机转写后拟稿。已有一轮在跑时这段要等它结束后再点一次「读新需求」。
   - **做完后 @ 对方**：展开的那一行里，原话的每个发言人各有一格「做完后 @」，填对方的 GitHub 账号（留空不 @）；创建请求带 `mention: {微信里的名字: GitHub 账号}`，不带就照记下的对应。对应存数据目录 `wechat-people.json`（600），下次同一个人自动带出，清空再创建就不再记。创建成了，要 @ 的账号记在 `issue-drafts.json` 的 history 里；工作台后台每 10 分钟看一遍这些 issue（`gh issue view --json state,stateReason,closedByPullRequestsReferences,comments`），关闭且原因是完成的，用你的账号评论一句「@账号 这条已做完，改动见 #PR，麻烦看一下。」。关闭原因是不做的不发；你自己已经在评论里 @ 过的不重复发；没发成的原因列在抽屉底部「做完后要 @ 的」，下一轮再试。对方要有这个仓库的权限才收得到通知。
5. **新建仓库**：草稿带 `new_repo`，或确认时传 `new_repo`，点创建后依次：`gh repo view luochen211/<name>` 查在不在（在且登记过 → 当现有仓库用；在但没登记 → 停下，不建、不开 issue，换名字或先登记）→ `gh repo create luochen211/<name> --private [--description …]`（只建私有的，只建在自己账号下）→ 往数据目录 `projects.local.toml` 追加一条 `[[projects]]`（`id` 按 `discovery.slug` 取，`group = "其他"`，`repo_path = ""`，`repository` 和 `github` 是新仓库，之后可以手改）→ 在新仓库里开 issue。仓库建成后草稿记 `repo_created`：登记或开 issue 没成时草稿留着，再点一次接着做，不会重复建仓库；gh 超时提示先去 GitHub 看一眼。返回里 `repo_created` 是这次有没有新建仓库。页面上选「新建仓库」的入口还没做，现在只有带 `new_repo` 交来的草稿和接口能用。

## 学习（「助理」页的「学习」标签）

学习根目录默认 `/Volumes/Luochen/学习资料/学习课题`，环境变量 `LEARNING_ROOT` 覆盖。真源是根目录的 `INDEX.md`（「课题 | 当前进度 | 最近更新 | 下一步」）和课题目录，工作台不另存进度。

- `GET /api/learning`：INDEX.md 登记的每个课题（照搬各格）、已写的各篇（`NN.md`，标题取一级标题，有没有 `NN.html`、反馈原文、是否已填、文件版本），下一篇号（最大篇号 + 1），以及「写下一篇」的进度（读中台里「写学习文章」的轮次，只在有交给记录时读）。
- `GET /learning/<课题>/<文件>`：课题目录里的 HTML、图片和样式，只读；只认 INDEX.md 登记的课题，路径不出课题目录、不走符号链接、不读隐藏文件和 md。响应带 `Content-Security-Policy: sandbox`（不含 allow-same-origin），页面脚本照跑，但不能借工作台的来源调接口。
- `POST /api/learning/feedback`（`{"topic", "no", "text", "version"}`）：只改那一篇 `NN.md` 末尾「学习反馈」区「请写在这行下面：」之后那一段（没有反馈区就照技能模板补一个），临时文件换名写入；`version` 和文件现在的修改时间对不上就拒绝（409），不覆盖别人刚写的。内容没变不写。
- `POST /api/learning/next`（`{"topic"}`）：先确认中台有规则「写学习文章」（没有就提示先 install，一条不记），在 `~/.agent-autoloop/issues/learning-handoffs.json` 的 `fcntl` 锁里重读、已交给还在派（15 分钟内）或在写的不再交，记下交给时间、篇号和写好的 detail（课题目录、篇号、INDEX.md 的下一步、按 dbs-learning 写、先检索核实并在文末列可点来源、不给书单、写完运行 `_tools/build_learning.py` 并更新 `00-学习计划.md` 和 `INDEX.md`、只写这一篇），再发一次 `issues.check`。监看每轮把一天内交给的作为 `learning.next` 事件交给中台（id 是 `learning:学习/<课题>@交给时间`，中台按 id 去重），规则「写学习文章」接。回执最后一行是 `RESULT: ARTICLE=<篇号>` 或 `RESULT: BLOCKED=<原因>`。页面在交给后每 10 秒刷新，写完列表跟着更新。
- 小说导读：根目录默认 `/Volumes/Luochen/impact/教材`，环境变量 `NOVELS_ROOT` 覆盖。`GET /api/learning` 另带 `novels`：根目录下带 `index.html` 的书目录（隐藏目录、符号链接、没有 `index.html` 的跳过）和各自的阅读状态（在读的排前面），以及「人物选择对照」页的路径（`选择对照/人物选择对照.html`，不在就是空串）。
- `GET /novels/<路径>`：导读根目录里的 HTML、样式、脚本和图片，只读；按整个根目录给（导读页之间的 `../` 链接要走通），别的类型（epub、md、json……）、隐藏文件、符号链接和出根目录的路径一律 404，同样带沙箱策略。
- `POST /api/learning/novel`（`{"book", "status": "unread" | "reading" | "done"}`）：只认根目录里现有的书，写工作台运行数据 `.personal/novel-status.json`（回到未开始就删掉那条），不写教材目录。

## 回顾（「助理」页的「回顾」标签，`#/review`）

读助理目录（和今日简报同一个，默认 `/Volumes/Luochen/LCS/assistant`，环境变量 `ASSISTANT_ROOT`）的 `回顾/周/YYYY-Www.md`、`回顾/月/YYYY-MM.md` 和同名离线 HTML，都由助理仓库的 `scripts/review.py` 写；工作台不另存内容。md 里 `<!-- review:summary -->`、`review:top3`、`review:questions`、`review:projects` 四个块由回顾 Agent（技能 `assistant-review`，中台规则「周回顾」周日 21:00、「月回顾」每月 1 日 09:00）填，没填的页面只写「还没写」。

- `GET /api/review`：周、月两个分区，各期新的在前：期号、起止日期、是否还没过完、一句话总结、下周最重要的 3 件、三问和各自的回答、月回顾的各项目过三问、几个数字（本人合计小时、有记录的天数、各类别小时和占比、合并 PR 数、关掉 issue 数，只从 md 里认，认不到的不给）、有没有离线 HTML、文件版本；另有现在点生成会出哪一期（周是今天所在的周，月是昨天所在的月）和下一次自动生成的时刻。
- `GET /review/<week|month>/<期号>.html`：那一期的离线 HTML，只读；只认期号合法的 `.html`，不走符号链接，别的一律 404；和学习页一样带 `Content-Security-Policy: sandbox`。
- `POST /api/review/answer`（`{"kind", "label", "no", "text", "version"}`）：只改那一期 md 里第 `no` 题「回答：」之后到下一题之前那一段（每行缩进到「回答：」那一列，空行不留），临时文件换名写入；`version` 和文件现在的修改时间对不上就拒绝（409）；问题还是占位符的不能答；回答里不能有 `<!--`、`-->` 和占位符原文。写了且该期有 HTML，就运行 `review.py --render <md>` 重出 HTML；没重出成只在返回里说明，回答已保存。
- `POST /api/review/run`（`{"kind", "label"?}`）：运行 `review.py --week|--month --date <那一期的第一天>`，只做收集事实这一步（和 `/api/brief/run` 同一种跑法，最多 300 秒，同一时间只跑一个）。脚本重跑时保留不含占位符的块（含回答）；某个块只写了一部分、还带占位符时脚本会整块重置，这种情况接口先拒绝（409）。还没开始的期不生成。
- `POST /api/review/next`（`{"kind", "label"}`）：「让 Agent 写总结和三问」。那一期要已经有 md、且有块还没写；先确认中台有规则「写回顾」，在 `~/.agent-autoloop/issues/review-handoffs.json` 的锁里重读、已交给还在派或在写的不再交，记下交给时间、期号和写好的 detail，再发一次 `issues.check`。监看把一天内交给的作为 `review.next` 事件交给中台（id 是 `recap:回顾/<期号>@交给时间`），规则「写回顾」接。回执里有一行 `RESULT: REVIEW=<期号>` 或 `RESULT: BLOCKED=<原因>`。
- `python3 -m personal.agent_watch install` 照定时规则「周回顾」（没有就「月回顾」）的 Agent 和目录加规则「写回顾」（技能 `assistant-review`，在助理仓库，不在本仓；`parallel` 1），两条监看规则的 `emits.types` 加上 `review.next`。已经有的不改；中台没有这两条定时规则就什么都不加。
- 工作台不提交、不推送助理仓库：保存的回答和现在生成的文件留在助理仓库的工作区里，由下一次回顾（技能 `assistant-review`）一起提交。

## 健康（「助理」页的「健康」标签，`#/health`）

读助理目录（和今日简报同一个）的 `资料/raw/health/zepp-daily.csv`：每天一行的睡眠、心率、步数，由助理仓库的 `scripts/zepp_import.py` 从 Zepp 导出包生成。工作台只读这个文件，不写、不调用模型；更新数据要本人重新导出，再跑一次导入脚本。

- `GET /api/health/daily`（`personal/health.py`）：每天一行（旧的在前）、几个汇总数、几句结论、「保 7 争 8」两条线、数据截止日期。入睡和醒来时刻是相对醒来那天 0 点的分钟数（23:30 入睡是 -30）。
- 结论都是从这些数算出来的：达标晚数、入睡时间的早晚和波动、过 0 点才睡和 0 点前睡的平均时长（每组至少 3 晚才比）、最近 7 晚和之前 7 晚（每组至少 3 晚才比）、深睡和 REM 占比、静息心率、日均步数。
- `analysis`：同目录 `分析.md` 的正文（AI 在导入数据后写的解读，开头两行「数据截至：YYYY-MM-DD」「写于：YYYY-MM-DD」），有就原样给页面，显示在最上面的「分析」一块；数据截止日期比「数据截至」新就标 `outdated`，页面写明需要重写。工作台不写这个文件、不调用模型。
- 不满一整天的日子（导出当天；开始戴表、还没有睡眠记录的第一天）标 `partial`，不计入日均步数和静息心率，步数图里画成浅色。
- 数据比今天旧 2 天以上，页面顶部提示重新导出。文件不存在、是符号链接或在助理目录外，都按「还没有导入过」处理。
- 页面（`web/src/Health.tsx`，换算和图形几何在 `HealthParts.ts`）：四个数、结论、四张共用横轴的图（每晚睡了多久、几点睡几点醒、心率、步数，最多画最近 30 天）、每天的表。鼠标停在某一天，四张图同时标出那天，图上方一行字写那天的数。

## 自媒体（`#/media` 麦田、`#/content` 在做的内容）

对麦田（[mdldm-maitian](https://github.com/CzzzzzzJ/mdldm-maitian)，自媒体多平台发布中控）只读：只发 GET，不调用 `/api/action` 等写接口。麦田的口令只在后端用，不回给页面、不写日志、不进仓库。

- 连接配置按顺序找：工作台本机 `.personal/maitian.json`（`{"url": "http://127.0.0.1:8787", "key": "<口令>"}`）；麦田目录（默认 `/Volumes/Luochen/工作/开源/maitian`，环境变量 `MAITIAN_ROOT`）`config.json` 的 `web.port`、`web.key`；都没有就退回演示版 `http://127.0.0.1:8788` + 口令 `demo`，页面写「现在显示的是麦田演示数据」。
- `GET /api/media/maitian`：后端带 cookie `mt_key` 读麦田 `GET /api/overview`，整理成最近 6 期（期号、标题、计划/发布时间、按状态归组的平台，已填好和待你点发布都算「等你点发布」）、各平台粉丝最新数和近 7 天涨粉（最新一天减 7 天前那天，不满 7 天从最早一天算）、要你处理的健康问题（专用 Chrome 没开、某平台登录失效、成片目录读不到）。`state` 是 `ok`、`down`（连不上，带一行启动命令：演示版 `node src/demo.mjs`，真实版 `node src/hub.mjs daemon`）、`denied`（口令不对）或 `error`。
- `GET /media/maitian`：「打开麦田大屏 ↗」，302 到麦田带口令的入口，麦田写 cookie 后跳回它的首页。
- `GET /api/media/content`：助理目录 `事项/自媒体与内容/README.md` 里「## 在做的内容」下第一张表的每一行（按表头认「内容」「做到哪」「发布」三列），每格原样给，页面按 Markdown 显示；不写助理目录。
- `python3 -m personal.agent_watch install` 照「处理 issue」加规则「写学习文章」（Agent 用 `claude-feedback`，没有就用「处理 issue」的；目录是学习根目录；技能 `dbs-learning`，在 `~/.claude/skills`，不在本仓；`parallel` 1），两条监看规则的 `emits.types` 加上 `learning.next`。已经有的不改。

### 并行和模型

`python3 -m personal.agent_watch install` 对「处理 PR 反馈」和「处理 issue 留言」这两条规则（常量都在 `personal/agent_watch.py`）：

- **规则和事件**：没有的规则照「处理 issue」加上，两条监看规则的 `emits.types` 加上 `prs.feedback` 和 `issues.feedback`，`~/.claude/skills/handle-issue-feedback` 链到本仓。
- **并行**（`FEEDBACK_PARALLEL = 10`）：规则的 `parallel` 低于 10（或没写）就调到 10，已经更高的不动。中台里 `parallel` 的意思（agent-autoloop `kernel/rules.ts`、`kernel/decide.ts` 的 `enqueue` 和 `claimNext`，`docs/protocol.md`）：没写时整条规则是一个事项，一次一轮；写了就按对象（事件的 subject）分事项，同一对象一次一轮，这条规则（同一组，没写 `group` 时组就是规则名）最多同时这么多轮。同一组里的规则 `parallel` 要一致，所以规则写了和别的规则相同的 `group` 时 install 不改它，只提示手动改。
- **接单程序容量**：真正同时跑几个还受接单程序限制。它由 `~/.agent-autoloop/config.json` 的 `worker.capacity` 决定（agent-autoloop `host/config.ts`、`worker/worker.ts`），没写是 2，本机全部规则合起来算。要 10 个同时跑，把 `worker.capacity` 改成不小于 10 的数，再运行 `launchctl kickstart -k gui/$(id -u)/agent-autoloop`。这个文件里是密钥，install 不改它；容量低于 10 时 install 的输出会提示，页面顶部写「最多同时 2 个（接单程序容量 2，规则上限 10）」。
- **模型**（`FEEDBACK_AGENT = "claude-feedback"`，`FEEDBACK_MODEL = "claude-opus-5-5"`）：中台按 Agent 选模型（`rules.json` 里 `agents.<id>.model`，agent-autoloop `protocol/rules.ts`、`worker/worker.ts`），规则本身不能写模型。install 在 `agents` 里照「处理 issue」的 Agent 抄一份 `claude-feedback`、写上 `model`，把这两条规则的 `to` 从「处理 issue」的 Agent 改成它。「处理 issue」「审查 PR」的 Agent 和模型不动；`claude-feedback` 已经有了就不改（改过的模型照留），规则另外指定了别的 Agent 也不改。

改之前备份成 `rules.json.bak-<时间>`，没有改动就不备份、不写；改了哪些写在 install 的输出里。

## 桌面提醒

`python3 -m personal.agent_watch install` 还会把 `personal/autoloop_plugins/desktop/channel.ts` 装成 `~/.agent-autoloop/plugins/desktop/channel.ts`，覆盖中台自带的桌面通道（中台本身不改）。之后 Agent 的提问和结果、监看连续失败都交给 `personal/notify_open.py`：装了 `terminal-notifier` 时发可点的横幅；没装时弹一个带「打开工作台」按钮的对话框（macOS 自带的 `display notification` 点了没有动作）。点了先确认工作台在跑，没在跑就在后台启动 `python3 -m personal`（不注册常驻服务），能读了再打开 `#/q/<编号>` 或 `#/i/<owner>/<repo>/<编号>`。点没点、开了哪页记在 `.personal/notify.log`。`--issue` 只接受 `owner/repo#编号`；交给 terminal-notifier 的 `-execute` 命令每一段都用 `shlex.quote`。

装了新的通道要重启中台：`launchctl kickstart -k gui/$(id -u)/agent-autoloop`。不要用 `agent-autoloop service install` 重启，它会按默认值重写 `~/Library/LaunchAgents/agent-autoloop.plist`，丢掉手工加进 PATH 的目录（例如 `~/.npm-global/bin` 里的 `claude`）。

## React 开发

先启动本机服务，再运行：

```sh
cd web
npm_config_cache=/Volumes/Luochen/缓存/npm npm ci
npm run dev
```

前端依赖独立安装在本项目 `web/node_modules`，不依赖旧原型目录的符号链接。

开发地址 http://localhost:5174 ，代理 `/api` 到 8790。`npm run build` 生成 `personal/static/index.html` 单文件模板，修改前端必须同时提交产物。离线 `board.html` 在服务启动时生成，只用于浏览；不提供写操作和完整事件记录。

## 验证

```sh
python3 -m pip install -r requirements.txt
python3 -m pytest -q
cd web && npm ci && npm test && npm run build
git diff --exit-code -- personal/static/index.html   # 前端产物要和源码一致
```

测试使用临时数据目录与随机本机端口，GitHub 使用固定响应；覆盖空目录启动、通知同步和旧任务库导入、去重、跨站拒绝和脚本转义。

CI 在 PR 就绪时运行；不在合并后重复运行同一套检查。没有配置云部署。服务目前由启动命令运行，尚未安装开机常驻服务。
