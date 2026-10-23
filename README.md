# KnowledgeRouter CLI (`kr`)

### 🧠 共生记忆 CLI —— 面向 AI Agent 的本地数据行囊与共生外脑
> **Turn any Obsidian Vault into an Agent-Ready Symbiotic Memory Backpack — without moving a single note.**  
> *在不改变任何笔记习惯、不重构目录的前提下，把现有 Obsidian 库变成任何 Agent 都能即插即用、安全读写、携带漫游的共生记忆体。*

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python: >=3.10](https://img.shields.io/badge/Python->=3.10-blue.svg)](pyproject.toml)
[![Tests: 100% Passing](https://img.shields.io/badge/Tests-150%2B%20Passing-brightgreen.svg)](tests/)
[![Architecture: Local--First](https://img.shields.io/badge/Architecture-Local--First-orange.svg)](docs/symbiotic-memory-vault-spec.zh.md)
[![Dependencies: Pure Stdlib](https://img.shields.io/badge/Dependencies-Pure%20Stdlib-blueviolet.svg)](scripts/)

---

## 🌟 核心理念：为什么需要“共生记忆”？ (Why Symbiotic Memory?)

当前以 Muse、Notion AI、Mem 为代表的 Personal AI Agent 正在将用户的**核心认知资产**（人生目标、价值观、情感反思、社交人脉、生活足迹）逐步圈禁在封闭的云端数据库中。你与 AI 交互越深入，迁移成本就越高，平台锁定越深。

**KnowledgeRouter CLI（共生记忆 CLI）** 旨在打破这一困境：

* **数据主权（Data Sovereignty）**：本地纯文本 Markdown 是唯一物理真理源（SSOT）。拒绝任何平台绑架与云端黑盒。
* **人机等权（Human-Agent Parity）**：你在 Obsidian 中看到的是优美的排版、双链与 Dataview 动态看板；Agent 看到的是标准化的时间流与结构化上下文。双方互为主客体，共同演进同一份外脑。
* **数据行囊（Bring Your Own Memory）**：模型是流水的算力，记忆是永恒的资产。今天用 Claude，明天用 Cursor，后天换本地开源模型，只需挂载你的背包，任何 AI 都能秒级理解你。
* **零污染安全防线（Zero-Pollution Safe Capture）**：采用 **SHA-256 乐观锁** 与 **Windows 排他文件并发控制**，杜绝并发覆盖人类笔迹；绝不随意建立碎片文件污染知识库。

---

## 🏛️ 双层共生架构与 6 大认知切片 (Architecture & The 6 Slices)

高频的人机交互最忌讳“一事一文件”引发的碎文件膨胀。KnowledgeRouter 采用经过实战检验的 **“时间流式收件箱（Stream Tier）+ 实体档案（Entity Tier）”** 双层共生架构：

```text
SymbioticVault/
├── 000_Inbox/                      # 📥 核心缓冲与时间流式收件箱 (Stream Tier)
│   ├── FOCUS.md                    # 🎯 切片 1: Focus (单一动态锚点，全局核心关注)
│   ├── idea.2026.md                # 💡 切片 2: Idea (年度巧思灵感流，#idea/seed)
│   ├── memo.2026.md                # 📝 切片 3: Memo (年度事实备忘流)
│   └── todo.2026.md                # ✅ 切片 4: Todo (年度行动与日程流，带日历时间块)
│
├── 300_Resources/305_Contact/      # 👥 切片 5: Contact (实体人脉卡片库，内嵌动态 Dataview)
│   └── [[人名]].md                 
│
├── WYQD/ (或 Ownly/)               # 💎 切片 6: Ownly (原生对齐 Ownly 资产/物品/足迹数据库)
│   ├── Objects/                    # 硬件设备、心爱物品清单与维保
│   ├── Accounts/ & Snapshots/      # 账户资产配置与净值快照 (脱敏规划)
│   ├── Reviews/                    # 决策复盘与回溯
│   └── Trips/ & Trip Visits/       # 旅行足迹、行程路线与体验消费
│
├── 100_Project/                    # 📁 进行中交付项目 (Focus 战役的承接实体)
└── 200_Area/                       # 📁 长期关注领域 (学术研究 / 个人传记 / 技术积累)
```

| 认知切片 | 物理载体 | 核心职责与规范 |
| :--- | :--- | :--- |
| 🎯 **Focus (目标)** | `000_Inbox/FOCUS.md` | **单一动态锚点**。只保留 1 份，记录季度北极星（North Star）、核心战役（Active Battlegrounds）与反向清单（Not-To-Do List），作为 Agent 推演的最高约束。 |
| 💡 **Idea (巧思)** | `000_Inbox/idea.YYYY.md` | **年度灵感流**。按年分库、按月分节（`## YYYY-MM`），捕获思维火花与顿悟（格式：`- 💡 [时间] 想法 #idea/seed`）。成熟后提炼入 `100_Project/`。 |
| 📝 **Memo (备忘)** | `000_Inbox/memo.YYYY.md` | **年度事实流**。按年分库，记录关键事实与技术备忘（格式：`- 📌 [时间] 事实`），保持高信噪比。 |
| ✅ **Todo (行动)** | `000_Inbox/todo.YYYY.md` | **年度日程与行动流**。带时间块 `[start:: ...] [end:: ...]`，原生兼容 `ical-plugin-pro` 自动同步至 Google Calendar。 |
| 👥 **Contact (人脉)** | `300_Resources/305_Contact/` | **人脉实体卡片**。一人一卡，内嵌 Dataview 动态查询全库关联待办，记录交往脉络与合作画像。 |
| 💎 **Ownly (所有权)** | `WYQD/` 或 `Ownly/` | **原生打通 Ownly 1.2.3**。记录物品设备（Objects）、账户快照（Accounts）与旅行足迹（Trips），Router 负责语义索引与预算唤回，绝不破坏原有格式。 |

---

## ⚡ 3 分钟快速上手 (Quick Start)

### 0. 安装方式 (Installation)

你可以选择全局 CLI 安装（推荐）或免安装直接运行：

```bash
# 方式 A (推荐)：pip 安装，获得全局 `kr` 与 `knowledge-router` 命令
pip install -e .
# 或直接从 GitHub 远端安装
pip install git+https://github.com/liuh886/knowledge-router.git

# 方式 B (免安装)：零依赖，直接用 Python 标准库运行
python scripts/knowledge_router.py --help
```

### 1. 生产/初始化一个共生记忆 Vault
无论是一个全新目录，还是希望规范化现有知识库，只需一行命令：
```bash
# 生成标准 6 大切片结构并自动建立只读检索索引
kr scaffold "D:/MyVault" --name personal
```
> **安全保证**：在已有知识库上运行时具备**非侵入性保护**，已有文件自动跳过，绝不覆盖已有内容。

### 2. 流式闪念捕获 (Stream Capture)
无需打开庞大的 Obsidian 客户端，随时在命令行或通过 Agent 快速落盘：
```bash
# 捕获巧思灵感 (自动落入 000_Inbox/idea.2026.md 当月章节)
kr inbox append idea "关于共生记忆背包的想法 #idea/seed"

# 记录待办日程
kr inbox append todo "对齐数据接口规范 #todo/inbox"

# 记录重要事实
kr inbox append memo "KnowledgeRouter 状态目录已解耦独立"
```

### 3. 人脉协同与雷达 (Contact Operations)
```bash
# 向联系人卡片追加交互记录 (支持姓名、别名或子串模糊匹配)
kr contact append "张一帆" "已确认实验场地数据采集格式"

# 查看联系人画像及 Dataview 动态关联待办
kr contact show "张一帆"

# 查看全局人脉协同雷达与活跃度
kr contact radar
```

### 4. 认知唤回与 Token 预算裁剪 (Budgeted Recall)
```bash
# 全库带出处行号的高信噪比搜索 (纯只读)
kr search "数据行囊" --limit 5 --json

# 核心杀手级功能：严格按 Token 预算提取 Context 简报 (避免打爆上下文)
kr brief "ESG30" --budget-tokens 1500 --json
```

---

## 🔌 与外部 Agent 协同 (Bring Your Own Memory)

KnowledgeRouter CLI 遵循标准 Agent Skill 协议设计。你可以直接把它作为一个 Skill 加载到任意 Agent（如 **Codex、Antigravity、Claude Code、Cursor、Windsurf、Ductor**）中。

### Agent System Prompt / Rules 配置模板
只需将以下说明加入你的 Agent 系统提示词（或 `.cursorrules` / Claude Instructions）：

```markdown
你是一个具备本地 Obsidian 共生记忆能力的智能助手。
本机已安装 KnowledgeRouter CLI (`kr` / `python scripts/knowledge_router.py`)：

1. 了解背景：在执行重大决策前，优先执行 `brief "FOCUS"` 或 `search "<topic>"` 获取带出处的权威事实；
2. 捕获灵感：当与用户碰撞出有价值的巧思时，调用 `inbox append idea "<内容> #idea/seed"`；
3. 记录待办：当用户产生待办时，调用 `inbox append todo "<任务> #todo/inbox"`；
4. 记录人脉：当涉及关键人物沟通时，调用 `contact append "<姓名>" "<事实>"`；
5. 安全约束：绝不绕过 CLI 直接读写物理 Markdown 文件。
```

---

## 🛡️ 双重解耦设计与安全契约 (Decoupled & Safe)

```
                     ┌──────────────────────────────────────────────┐
                     │            任意宿主 Agent / CLI 用户           │
                     └──────────────────────┬───────────────────────┘
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │        KnowledgeRouter CLI (无重依赖)         │
                     │  - 纯 Python 3.10+ 标准库 (零 pip 依赖)       │
                     │  - 独立状态根: ~/.knowledge-router/vaults     │
                     │  - 环境变量覆盖: KNOWLEDGE_ROUTER_STATE_ROOT  │
                     └──────────────────────┬───────────────────────┘
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     ▼                                             ▼
        【解耦模式: 无 Obsidian 运行】                   【协同模式: 配合 Ductor / Obsidian】
       - 在无头服务器、VSCode 或终端运行               - Obsidian 打开即享双链图谱与 Dataview
       - 外部 SQLite FTS5 全文索引可随时销毁重建       - Ductor 配置环境变量直接作为记忆引擎
```

1. **与 Obsidian 解耦**：纯 CommonMark / GFM 标准，不依赖 Obsidian App 运行，不依赖其私有 API 或社区插件。
2. **与 Ductor 解耦**：默认拥有独立的状态根目录 `~/.knowledge-router/vaults`；在未配置环境变量且独立目录未建立时，自动平滑兼容 `~/.ductor/vaults`，确保既有系统零中断。
3. **物理安全**：Windows 下采用排他文件锁与 `FILE_SHARE_READ` 独占控制，写入前校验 SHA-256 哈希，杜绝并发覆盖。

---

## 📖 命令行手册速查 (CLI Cheat Sheet)

| 命令模块 | 子命令 | 功能说明 |
| :--- | :--- | :--- |
| **脚手架** | `scaffold <path>` | 初始化生产标准的共生记忆 6 大切片结构，并自动挂载索引 |
| **挂载与状态** | `attach <path>` | 零侵入将已有 Vault 接入注册表并建立 FTS5 索引 |
| | `status` | 查看当前 Vault 挂载、索引代数与维护状态 |
| **收件箱流** | `inbox append todo <text>` | 追加任务至 `000_Inbox/todo.YYYY.md`（带日历时间块） |
| | `inbox append memo <text>` | 追加事实备忘至 `000_Inbox/memo.YYYY.md` |
| | `inbox append idea <text>` | 追加灵感火花至 `000_Inbox/idea.YYYY.md` |
| **人脉管理** | `contact append <target> <text>` | 向 `305_Contact/` 下的人物卡片追加交互日志 |
| | `contact show <target>` | 查看联系人详情、关联项目及 Dataview 任务 |
| | `contact radar` | 打印全局人脉协同雷达与活跃度 |
| **检索与唤回**| `search <query>` | 纯只读全局全文/语义检索，返回行号与出处校验和 |
| | `brief <topic> --budget-tokens N`| 按 Token 预算限制提取高信噪比上下文简报 |
| **治理与巡检**| `triage todo` | 扫描收件箱任务并进行多维标签/实体对齐 |
| | `review` | 巡检孤立笔记、断链与到期维护项，生成外部台账 |
| | `converge` | 将全库散乱的断链收敛为统一创建计划 |

---

## 📚 延伸阅读与架构文档

* 📑 **[共生记忆 Vault 规格说明书 (Symbiotic Memory Spec)](docs/symbiotic-memory-vault-spec.zh.md)**：权威的数据模型、Schema 契约与切片生命周期定义。
* 📐 **[系统架构设计规范 (System Design)](DESIGN.md)**：分层边界、读写流程与比较并交换（CAS）机制。
* 🛡️ **[安全与变更契约 (Safety Contract)](references/safety_contract.md)**：物理文件防破坏与权限安全准则。
* 🤖 **[Agent Skill 接口协议 (SKILL.md)](SKILL.md)**：面向 AI 智能体的标准命令调用规范。

---

## 📄 License

[MIT License](LICENSE) © KnowledgeRouter contributors
