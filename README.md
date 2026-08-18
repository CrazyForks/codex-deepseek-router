<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="codex-deepseek-router：Codex 保持父 Agent，按任务把工作路由到 DeepSeek Flash 或 Pro，并验证原生回调">
</p>

<p align="center">
  <img src="./assets/icon.png" width="180" alt="codex-deepseek-router 图标">
</p>

<p align="center"><strong>简体中文</strong> · <a href="README.en.md">English</a></p>

<p align="center">
  <a href="https://github.com/TheBlindM/codex-deepseek-router/actions/workflows/ci.yml"><img src="https://github.com/TheBlindM/codex-deepseek-router/actions/workflows/ci.yml/badge.svg" alt="CI 状态"></a>
</p>

> 将 DeepSeek 真正地接入 Codex 中。Codex 做总指挥，自动选择 Flash / Pro，
> 把便宜的大量阅读交给 Flash，把难问题交给 Pro，并且最终由 Codex 验收。

## 先看结果

| 主控不变 | 双 Agent 分工 | 结果可证明 |
| --- | --- | --- |
| 不修改 `config.toml`，父模型、Provider 和 ChatGPT 登录保持原样 | Flash 负责快速只读探索，Pro 负责深度推理与实现 | callback、线程数据库元数据、随机 challenge marker 三重验收 |

这不是 daemon、proxy、MCP Server 或第二套 Agent runtime。它是一组受管的
Codex 原生配置：两个 Agent、一个模型目录、一个明文交接 Hook、一个运行时
路由 Skill 和一个事务化管理器。

## 快速开始

要求：Node.js/npm、Python 3.9+、至少启动过一次的 ChatGPT Desktop（Codex）
或已经安装 Codex CLI，以及 DeepSeek API Key。

### 1. 安装 Plugin

#### ChatGPT Desktop（推荐）

只安装了 ChatGPT Desktop 的用户不需要在系统终端运行 `codex`。打开 Desktop
中的 Codex，新建任务，然后直接发送：

```text
请安装这个插件：
https://github.com/TheBlindM/codex-deepseek-router
```

Codex Agent 会检查仓库中的 `.agents/plugins/marketplace.json` 并发起安装；出现
插件确认页面时点击 **Install plugin**。安装完成后用 `⌘Q`（macOS）或完全退出
应用（Windows），重新打开并新建任务。

仅安装 Desktop 时，系统终端出现 `command not found: codex` 属于正常情况；
这不影响 Agent 在 Desktop 中安装插件。也可以在 Plugins 页面找到
**DeepSeek Router** 后手动点击安装。

#### Codex CLI

只有在系统终端执行 `codex --version` 成功时才使用下面的命令：

```bash
codex plugin marketplace add TheBlindM/codex-deepseek-router
codex plugin add codex-deepseek-router@deepseek-router
```

这里的 `deepseek-router` 是仓库提供的 Marketplace 名称，定义在
`.agents/plugins/marketplace.json`，不是需要用户自行替换的占位符。

Plugin 会同时提供管理 Skill、路由 Skill 与原生 Hook，不需要手动写入全局
`~/.codex/hooks.json`。

#### 更新或卸载

| 环境 | 更新 | 卸载 |
| --- | --- | --- |
| ChatGPT Desktop | 把上面的 GitHub 地址再次交给 Codex Agent，并要求“更新并重新安装这个插件”；或在 Plugins 页面卸载后重新安装 | 在 Plugins → Installed 中打开插件并选择卸载 |
| Codex CLI | 依次运行 `codex plugin marketplace upgrade deepseek-router` 和 `codex plugin add codex-deepseek-router@deepseek-router` | 运行 `codex plugin remove codex-deepseek-router@deepseek-router` |

无论使用哪种方式，安装或更新后都应完全重启 Desktop/CLI，并打开新任务，
让新的 Skill、Hook 和工具生效。更多通用说明见
[OpenAI Plugins 文档](https://learn.chatgpt.com/docs/plugins)。

### 2. 在 Codex 中完成配置

重启 Codex、打开新任务，然后说：

```text
请帮我安装并配置 codex-deepseek-router。
```

Skill 会先检查状态。缺少凭据时，Codex 会索要 API Key，并只通过标准输入
交给管理器；密钥不会进入命令参数、配置文件或聊天回显。

### 3. 审查并验收

1. 重启 Codex 或打开新任务，在原生 Plugin Hook UI 中 Review/Trust。
2. 让 Codex 运行真实路由测试；Flash 与 Pro 必须分别通过。
3. 若当前版本没有自动显示 Review Prompt，再在交互式 CLI 使用 `/hooks`。

以后可以直接说：

```text
用 DeepSeek 子 Agent 评审这个仓库。
```

## 它如何工作

```text
用户任务
   │
   ├─ 模态门：TEXT_ONLY / VISION_TRANSLATABLE / VISION_CRITICAL
   ├─ 敏感数据门：密钥与敏感内容留在 Codex
   ├─ 模型路由：Flash / Pro / 不委托
   └─ 策略路由：FAST / REACT / SPEC / DEEP
            │
            ▼
stage → SubagentStart Hook → DeepSeek 子 Agent
            │
            ▼
原生 callback → 元数据与 marker 验证 → Codex 整合
```

### 谁来做什么

| 路由目标 | 适合 | 边界 |
| --- | --- | --- |
| `deepseek_flash` | 搜索、枚举、日志、抽取、代码地图、大量阅读 | 只读；输出修改提案，不直接改文件 |
| `deepseek_pro` | 根因、架构、并发、安全、复杂评审和跨模块实现 | 可写工作区；负责需要深度推理的落地 |
| Codex 父 Agent | 琐碎任务、敏感内容、关键视觉判断、最终验证与整合 | 始终保留主控权 |

Flash 可以返回带 Evidence Packet 的 `ESCALATE_TO_PRO`；Pro 从已有证据继续，
不重新扫描整个仓库。FAST / REACT / SPEC / DEEP 为有边界的决策合同，不是
额外的模型或运行时。

## 安装内容与安全边界

管理器会：

- 同时安装 `deepseek-flash.toml` 与 `deepseek-pro.toml`；
- 在 `~/.codex/models.json` 同时注册两个模型；
- 由 Plugin 提供 `skills/` 与 `hooks/hooks.json`；Hook 通过 `PLUGIN_ROOT`
  定位文件，不依赖 cwd 或用户绝对路径；
- setup 只配置凭据、Agent、模型目录与显式路由所需的本地运行时；
- 使用系统凭据库保存 Key，并在任何步骤失败时完整回滚；
- 永远不修改父任务的 `config.toml`，也不伪造 Hook 信任状态。

macOS 通过同一个 Python 进程身份调用 Security.framework 读写 Keychain；
`status`/`doctor` 只检查条目是否存在，不解密 Key，也不会为一次状态检查
重复触发钥匙串授权。所有面向用户的回复跟随用户当前使用的语言。

DeepSeek 子 Agent 只接收文本。截图、图片和视频必须先由 Codex 转成文字事实；
关键视觉判断不会委托。Windows Agent 通过用户环境变量
`DEEPSEEK_API_KEY` 认证，设置后需要完全重启 Codex。

## 管理命令

| 命令 | 作用 |
| --- | --- |
| `status` | 只读检查运行时、Agent、模型目录、凭据与 Hook |
| `setup` | 幂等、事务化地安装全部组件 |
| `test` | 分别执行 Flash 与 Pro 的真实原生派发验收 |
| `repair` | 在父模型升级、Codex 更新或配置漂移后恢复 |
| `migrate` | 精确移除旧 Skill-first 全局 Hook，不触碰其它 Hook |
| `disable` | 记录停用意图；Plugin Hook 由 Codex/Plugin 管理 |
| `uninstall` | 删除本项目拥有的内容；默认保留 API Key |
| `doctor` | 诊断环境、Hook 信任与 handoff 状态 |

所有命令支持 `--json` 与 `--codex-home`。退出码：`0` 表示
ready/configured，`2` 表示需要人工处理，`3` 表示超时，`1` 表示意外失败。

<details>
<summary><strong>从源码安装与手动验收</strong></summary>

```bash
git clone https://github.com/TheBlindM/codex-deepseek-router.git
cd codex-deepseek-router
python3 scripts/codex_deepseek_router.py status --json
```

只通过 stdin 配置 Key：

```bash
printf '%s\n' '<你的key>' | python3 scripts/codex_deepseek_router.py setup --api-key-stdin --json
```

完成 Codex 原生 Plugin Hook 审查（若未出现提示，再用 CLI `/hooks`）后运行真实验收：

```bash
python3 scripts/codex_deepseek_router.py test --json
```

`test` 会分别证明两个角色使用正确的 `model_provider`、model 与 agent role，
并验证每个子 Agent 返回独立的随机 marker。Flash 通过不代表 Pro 通过。

</details>

## 文档

- [架构](references/architecture.md) ·
  [路由策略](references/routing-policy.md) ·
  [多模态边界](references/multimodal.md)
- [兼容性](references/compatibility.md) ·
  [安全设计](references/security.md) ·
  [故障排查](docs/troubleshooting.md)
- [设计决策](docs/architecture.md) · [评测](docs/eval.md) ·
  [上游来源映射](docs/upstream-reference-map.md)

## 开发与验证

```bash
python3 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

测试覆盖管理器生命周期与回滚、交接协议、跨角色隔离、路由合同、schema
校验和凭据泄漏扫描。CI 在 Windows、macOS、Linux 上覆盖 Python
3.9/3.11/3.12；真实 DeepSeek 调用不进入 CI。

## 致谢

- [LINUX DO](https://linux.do/)
- [oil-oil/codex-deepseek-subagent](https://github.com/oil-oil/codex-deepseek-subagent)
  奠定了管理器、事务回滚、系统凭据、运行时发现和原生验收的基础。
- [Utopia-V/codex-deepseek-subagent](https://github.com/Utopia-V/codex-deepseek-subagent)
  提供了明文 `SubagentStart` 交接传输的基础实现。
- [yjh051108/dsh-routing-suite](https://github.com/yjh051108/dsh-routing-suite)
  启发了有边界、任务感知的推理策略路由。

感谢这些项目的作者与贡献者公开实现、实验和设计推理。精确的代码适配、来源
映射与许可证说明见 [NOTICE.md](NOTICE.md) 和
[上游来源映射](docs/upstream-reference-map.md)。

## 许可

MIT — 见 [LICENSE](LICENSE)。
