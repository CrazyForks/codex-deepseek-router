# codex-deepseek-router

[English](README.md) | 简体中文

一个很薄、很可靠的 Codex → DeepSeek 调度层：Codex 保持父 Agent 身份，
同时拥有两个原生 DeepSeek 子 Agent，并按任务类型、模态和推理方式在二者
之间正确选择。

```text
Codex Parent（父模型/Provider 不变）
        │
        ├─ 模态门   TEXT_ONLY / VISION_TRANSLATABLE / VISION_CRITICAL
        ├─ 敏感数据门  密钥留在本地
        ├─ 模型路由   deepseek_flash (deepseek-v4-flash) | deepseek_pro (deepseek-v4-pro) | 不委托
        ├─ 策略路由   FAST / REACT / SPEC / DEEP
        ├─ 明文交接   SubagentStart Hook：stage → claim → 注入 → 消费
        ▼
  DeepSeek 子 Agent → 原生 callback → 父 Agent 验证与整合
```

它**不是**：daemon、proxy、MCP Server、数据库、第二套 Codex CLI、自研
Agent runtime 或自动学习路由器。V1 只是一组受管文件（两个 Agent TOML、
一个交接 Hook、一个路由 Skill、一个管理脚本），安装全程事务化、可回滚。

## 快速开始

要求：Node.js/npm、Python 3.9+、Codex 桌面应用（至少启动过一次）、
DeepSeek API Key。

1. 为 Codex 全局安装管理 Skill：

```bash
npx skills add TheBlindM/codex-deepseek-router --skill codex-deepseek-router -g -a codex -y
```

这里运行的是开放的 [`skills` CLI](https://github.com/vercel-labs/skills)，
它负责从本仓库安装 Skill；本项目本身不需要发布或执行单独的 npm 包。

2. 重启 Codex 桌面应用并新建任务，然后说：

```text
请帮我安装并配置 codex-deepseek-router。
```

3. Skill 会先检查当前状态。缺少凭据时，Codex 会索要 DeepSeek API Key，
   并且只通过标准输入交给管理器。管理器随后以事务方式安装两个 Agent、
   模型目录、明文交接 Hook 和运行时路由 Skill。

4. 在 Codex 中运行 `/hooks`，审查并信任新 Hook，然后让 Codex 执行真实路由
   测试。Flash 与 Pro 必须分别通过。结果为 `ready` 后，重启应用并新建任务。

以后可以直接自然地说：

```text
用 DeepSeek 子 Agent 评审这个仓库。
```

### 从源码安装

开发或人工审查时，也可以克隆仓库并直接调用管理器：

```bash
git clone https://github.com/TheBlindM/codex-deepseek-router.git
cd codex-deepseek-router
python3 codex-deepseek-router/scripts/codex_deepseek_router.py status --json
```

配置 Key（只经 stdin，绝不进 argv、文件或聊天回显）：

```bash
printf '%s\n' '<你的key>' | python3 codex-deepseek-router/scripts/codex_deepseek_router.py setup --api-key-stdin --json
```

安装器会：

- 同时安装两个 Agent（`~/.codex/agents/deepseek-flash.toml` 与
  `deepseek-pro.toml`），DeepSeek Provider 只存在于这两个文件内部；
- 在 `~/.codex/models.json` 同时注册两个模型（不存在只装一个）；
- 把明文交接 Hook 合并进 `~/.codex/hooks.json`（不破坏你已有的其他
  Hook）；
- 安装 `use-deepseek-router` 运行时路由 Skill；
- **完全不修改 `config.toml`**——父模型、Provider、ChatGPT 登录保持原样；
- 任何一步失败都完整回滚。

然后在 Codex 里用 `/hooks` 审查并信任 Hook（安装器绝不伪造信任），再运行
真实验收：

```bash
python3 codex-deepseek-router/scripts/codex_deepseek_router.py test --json
```

`test` 对两个 Agent **分别**证明：原生 `spawn_agent` → DeepSeek 子 Agent
收到 staged 任务 → 返回随机 challenge marker → `state_*.sqlite` 线程元数据
为 `model_provider=deepseek`、正确的 model 与 agent_role。Flash 通过不代表
Pro 通过。安装后需要重启应用 / 新开任务。

Windows 的 Agent 模板通过环境变量 `DEEPSEEK_API_KEY` 认证：请设置为用户
环境变量并完全重启 Codex 桌面应用（管理脚本会把密钥存入 Credential
Manager，并在自己的 smoke 运行中注入环境）。

## 日常使用

Codex 始终是父 Agent，由它决策；`use-deepseek-router` Skill 提供决策合同：

- **先看模态**：纯文本直接交给 DeepSeek；可翻译的视觉输入由父 Agent 先
  看，写成 Visual Context Packet；关键视觉判断永远留在父 Agent。
- **Flash**（`deepseek_flash`）：搜索、枚举、日志、抽取、代码地图、大
  量阅读，以及只读的修改提案——它永不改文件，落地由父 Agent（或 Pro）完成。
- **Pro**（`deepseek_pro`）：根因、架构、并发、安全、跨模块推理、复杂
  评审与实现。
- **策略**：FAST / REACT / SPEC / DEEP，都带收敛约束。
- **升级**：Flash 返回 `ESCALATE_TO_PRO` 并附 Evidence Packet，Pro 从证据
  出发，不重新扫库。
- **不委托**：太琐碎、涉密、纯视觉的任务由父 Agent 自己完成。

委托只有一种方式（先 stage 再 spawn）：

```text
stage 任务 → spawn_agent(agent_type="deepseek_flash", fork_turns="none") → wait → 验证 → 整合
```

## 命令

```text
status      只读状态：运行时、Agent、模型目录、凭据、Hook
setup       全量安装（幂等、事务化、失败回滚）
test        真实双 Agent smoke（两个角色各自独立验收）
repair      父模型升级 / Codex 更新 / 配置漂移后恢复
disable     只摘除路由 Hook；保留凭据、模型目录与备份
uninstall   删除本项目拥有的全部内容；默认保留 API Key，
            只有 --remove-credential 才删除
doctor      环境与 handoff 状态诊断
```

所有命令支持 `--json` 与 `--codex-home`。退出码：`0` ready/configured，
`2` 需要人工处理（缺凭据、冲突等），`3` 超时，`1` 意外失败。

## 文档

- [references/architecture.md](codex-deepseek-router/references/architecture.md) — 组件结构
- [references/routing-policy.md](codex-deepseek-router/references/routing-policy.md) — Flash/Pro 与 FAST/REACT/SPEC/DEEP
- [references/multimodal.md](codex-deepseek-router/references/multimodal.md) — DeepSeek 永远看不到图片
- [references/compatibility.md](codex-deepseek-router/references/compatibility.md) — 已验证基线与边界
- [references/security.md](codex-deepseek-router/references/security.md) — 数据边界与密钥处理
- [docs/architecture.md](docs/architecture.md) — 设计决策
- [docs/troubleshooting.md](docs/troubleshooting.md) — 症状 → 修复
- [docs/eval.md](docs/eval.md) — 路由与策略评测
- [docs/upstream-reference-map.md](docs/upstream-reference-map.md) — 上游符号来源映射

## 开发

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

测试覆盖管理器生命周期与回滚、交接协议（claim/consume/quarantine/TTL/
跨角色隔离）、路由合同、schema 校验与凭据泄漏扫描。CI 跑 Windows、macOS、
Linux × Python 3.9/3.11/3.12。真实 DeepSeek 调用刻意不进 CI，请手动运行
`test --json`。

## 致谢

本项目建立在以下开源项目分享的实现与思考之上：

- [oil-oil/codex-deepseek-subagent](https://github.com/oil-oil/codex-deepseek-subagent)
  奠定了管理器 CLI、原子配置事务与回滚、系统凭据存储、Codex 桌面运行时
  发现和原生子 Agent 验收的基础。它清晰的 Skill-first 安装流程也直接启发了
  本 README 的快速开始设计。
- [Utopia-V/codex-deepseek-subagent](https://github.com/Utopia-V/codex-deepseek-subagent)
  提供了明文 `SubagentStart` 交接传输；本项目在此基础上扩展为双角色、类型化
  数据包、TTL 恢复和跨平台锁。
- [yjh051108/dsh-routing-suite](https://github.com/yjh051108/dsh-routing-suite)
  启发了有边界、任务感知的推理策略路由。本项目没有照搬 DSH 的运行时假设，
  而是将相关思想重新表达为 Codex 父 Agent 使用的 FAST / REACT / SPEC / DEEP
  决策合同。

感谢这些项目的作者与贡献者公开实现、实验结果和设计推理，让本项目得以站在
可靠的基础上继续演进。具体代码适配、来源映射与许可证说明见
[NOTICE.md](NOTICE.md) 和
[docs/upstream-reference-map.md](docs/upstream-reference-map.md)。

## 许可

MIT — 见 [LICENSE](LICENSE)。
