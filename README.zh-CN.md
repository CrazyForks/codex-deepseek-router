# codex-deepseek-router

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

要求：Python 3.9+、Codex 桌面应用（至少启动过一次）、DeepSeek API Key。

```bash
git clone https://github.com/<your-org>/codex-deepseek-router.git
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

## 致谢与许可

管理器、凭据与事务设计源自
[oil-oil/codex-deepseek-subagent](https://github.com/oil-oil/codex-deepseek-subagent)；
明文交接传输源自
[Utopia-V/codex-deepseek-subagent](https://github.com/Utopia-V/codex-deepseek-subagent)；
推理策略路由灵感来自
[yjh051108/dsh-routing-suite](https://github.com/yjh051108/dsh-routing-suite)。
详见 [NOTICE.md](NOTICE.md)。

MIT — 见 [LICENSE](LICENSE)。
