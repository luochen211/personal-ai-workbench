# 微信插件：初始化

装上以后，工作台会多出这几样，全部只读：

- 收件箱的「微信」范围：私聊待回、群里 @我、待收转账等漏看提醒。
- 「新需求」抽屉会读你登记过的微信群，把群里的需求拟成 issue 草稿，你确认后才创建。
- `wechat articles` 命令：查你订阅的公众号推过哪些文章。

插件不会发微信消息，也不读你没登记过的群。聊天原文只留在本机的数据目录里（权限 600），不写日志，不进仓库。

下面按顺序做。命令都在工作台仓库的根目录运行。

## 1. 准备读取工具

插件不带读微信库的代码，要你自己准备一个本机读取工具，并遵守它的许可。插件对它的要求是：

- 工具目录里要有 `scripts/decrypt_all_dbs.py`。用 `--mode incremental` 运行时，它把微信数据增量解密到它自己的私密明文库，不碰微信界面；读不到微信源库时退出码是 2。
- 工具目录里要有 `scripts/vault_cli.py`，提供 `pending`（漏看检查）、`history`（某个会话的消息）、`status`（明文库在哪）这几个子命令，都支持 `--format json`。

第一次使用前，先按读取工具自己的说明取密钥，再做一次全量解密。确认它的 `status` 能报出明文库：

```sh
<python> <工具目录>/scripts/vault_cli.py status --format json   # 要看到 "exists": true
```

## 2. 准备 Python

找一个读取工具能用的 Python（建议单独建一个虚拟环境），要装好或自带这几样：

| 用途 | 需要 |
|-|-|
| 解密、漏看检查 | `pycryptodome` |
| 读公众号文章（解压推送内容） | `zstandard` |
| 解析消息和推送 | `pyexpat`（标准库自带；有些 Homebrew 的 Python 缺它） |

检查一下：

```sh
<python> -c "import Crypto, zstandard, pyexpat; print('ok')"
```

## 3. 安装插件

```sh
python3 -m personal.plugins install wechat --vault-dir <工具目录> --python <上一步的 python>
```

安装会做这几件事：

- 在数据目录（默认 `.personal/`）写 `wechat.json`，写明读取工具在哪、用哪个 Python。这个文件已经有的话不会改。
- 把中台规则「微信拟 issue」加进 `~/.agent-autoloop/rules.json`：改之前先校验，再备份原文件。没装中台就跳过这一步，装好中台后再运行一次 install 即可。
- 输出里出现「注意：…」，说明配置有问题，按提示改 `wechat.json` 再装一次。

工作台在运行时不用重启。

`wechat.json` 可选的几项：`since`（漏看检查看多久，默认 `7d`）、`interval_minutes`（后台多久刷新一次，默认 15）、`decrypt_timeout`（解密超时，默认 900 秒）、`pending_timeout`（漏看检查超时，默认 300 秒）。

## 4. 打开完全磁盘访问

后台进程要读微信的数据目录，需要「完全磁盘访问」权限：

1. 打开 系统设置 → 隐私与安全性 → 完全磁盘访问。
2. 把第 2 步那个 Python 加进去。虚拟环境里的 python 多半是符号链接，要加的是它指向的真实文件：

```sh
python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" <上一步的 python>
```

还没开权限时，收件箱的「微信」范围会提示去哪里开。开好以后点「一键读取」，会立即重试。

## 5. 登记要读的群

「新需求」只读你登记过的会话。登记就等于把这个会话加进白名单：

```sh
python3 scripts/issue_draft.py projects                                     # 看能开 issue 的项目 id
python3 -m personal.plugins wechat map --chat <群名> --project <项目 id>     # 群名和微信里显示的一样
python3 -m personal.plugins wechat mappings                                 # 看已登记的
python3 -m personal.plugins wechat unmap --chat <群名>
```

一个会话第一次被读时，只看最近 7 天的消息，以后从上次读到的地方接着读。

## 6. 有第二个微信号时（可选）

不在默认微信号里的会话，要登记在 `~/.local/state/wechat-issues/accounts.json` 里（权限 600，路径可用环境变量 `WECHAT_ISSUES_ACCOUNTS` 换）：

```json
{"accounts": {"wechat2": {"chats": ["群名"], "decrypted_dir": "<这个号的明文库>", "home": "<只给这个号用的 HOME 目录>", "python": "<可选>"}}}
```

- `home` 是给这个号单独准备的一个目录，里面放这个号的读取工具配置、密钥和明文库，放法照读取工具找这些文件的位置。刷新这个号时，插件把 `home` 当作 HOME 来运行读取工具，两个号的数据就不会互相覆盖。
- `chats` 里列出的会话只从这个号读，其余会话都读默认号。

## 7. 确认装好了

```sh
python3 -m personal.plugins list                          # wechat（微信）：已装
curl -s http://127.0.0.1:8790/api/wechat | head -c 200    # "enabled": true，过一会儿 "has_data": true
python3 personal/plugins/wechat/extract.py --dry-run      # 只读不存：打印读到几条消息、能拟几条草稿
python3 -m personal.plugins wechat articles --limit 5     # 最近的公众号文章
```

`--dry-run` 也会问模型。默认用的是不带工具、不存会话记录的 `claude -p`，要换别的模型，设环境变量 `NEEDS_MODEL_COMMAND`（见 `scripts/needs_common.py`）。

## 停用和删除

```sh
python3 -m personal.plugins uninstall wechat           # 停用，并删掉中台规则；数据都留着，重新 install 就回来
python3 -m personal.plugins uninstall wechat --purge   # 连数据一起删，删了回不来
```

两种都不碰读取工具本身和它的明文库。`--purge` 具体删哪些，见 [RUN.md](../../../RUN.md#微信插件)。
