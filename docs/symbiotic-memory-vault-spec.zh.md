# 《共生记忆 Vault 规格说明书》 (Symbiotic Memory Vault Specification)

> **产品标识**：共生记忆 CLI / KnowledgeRouter CLI (`kr`)  
> **文档版本**：v1.1.0 (实战落地重构版)  
> **状态**：正式规范（Active Spec）  
> **对齐原则**：100% 尊重并沉淀本机在 Obsidian、Ductor 与 pwsh 中的成熟实战经验，消除虚构冗余，实现跨 Agent 漫游与独立产品化。

---

## 1. 产品愿景与设计宪法 (Philosophy & Core Principles)

### 1.1 为什么需要“共生记忆”？
当前 Personal AI Agent（如 Muse、Notion AI、Mem.ai）将用户的个人认知资产（人生愿景、灵感火花、社交人脉、财务与生活足迹）逐步圈禁在专有的云端黑盒数据库中。用户与 AI 交互越多，迁移成本越高，平台锁死越深。

**共生记忆（Symbiotic Memory）** 旨在打破这一困境：
* **数据主权（Data Sovereignty）**：本地纯文本 Markdown 是唯一物理真理源（SSOT），拒绝云端绑定。
* **人机等权（Human-Agent Parity）**：人类在 Obsidian 中看到优美的排版、双链与 Dataview 动态看板；Agent 看到的是标准化的时间流与结构化上下文。双方互为主客体，共同维护同一份外脑。
* **行囊漫游（Portable Backpack）**：用户携带自己的记忆行囊，无论接入 Claude、Cursor、OpenAI 还是本地开源模型，插上即用；模型是流水的算力，记忆是永恒的资产。

### 1.2 核心宪法原则

1. **Markdown First & SSOT**：物理 Markdown 文件是系统唯一的持久化真理源。任何向量数据库、全文索引（FTS5）、拓扑图缓存，皆为外部可随时销毁重建的“投影（Disposable Projections）”。
2. **双重解耦，自由协同 (Decoupled & Co-existent)**：
   * **与 Obsidian 解耦**：纯文本规范，不依赖 Obsidian App 运行，无专有插件硬依赖；但在 Obsidian 中打开时能获得原生的双链、标签与图谱体验。
   * **与 Ductor 解耦**：状态路径独立自持（默认 `~/.knowledge-router/vaults`），不硬编码特定主控系统；既可作为通用 Skill 被任意 Agent 独立调用，也可作为知识引擎无缝接入 Ductor。
3. **安全写入与零污染 (Zero-Pollution Safe Capture)**：
   * 采用 **文件排他锁与 SHA-256 乐观锁（Optimistic Concurrency Control）**，杜绝并发覆盖人类修改。
   * 采用 **年度流式收件箱优先** 与 **按月分节追加**，严禁 Agent 随地建碎文件造成索引碎片化。

---

## 2. 双层共生架构与 6 大认知切片 (The Two-Tier Architecture & 6 Slices)

在经过高强度实战检验后，共生记忆 Vault 摒弃了“一事一文件”的碎化设计，采用 **“时间流式收件箱（Stream Tier）+ 实体档案（Entity Tier）”** 的双层共生架构：

```text
SymbioticVault/
├── 000_Inbox/                      # 📥 核心缓冲与时间流式收件箱 (Stream Tier)
│   ├── FOCUS.md                    # 🎯 切片 1: Focus (单一动态锚点，全局核心关注)
│   ├── idea.YYYY.md                # 💡 切片 2: Idea (年度巧思灵感流)
│   ├── memo.YYYY.md                # 📝 切片 3: Memo (年度事实备忘流)
│   └── todo.YYYY.md                # ✅ 切片 4: Todo (年度行动与日程流，带日历时间块)
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
├── 100_Project/                    # 📁 进行中交付项目 (Focus 的落地承接实体)
└── 200_Area/                       # 📁 长期关注领域 (Academic / Research / Biography 等)
```

---

### 切片 1：`Focus` —— 单一动态锚点 (Active Focus Ledger)

* **物理位置**：`000_Inbox/FOCUS.md`（全局唯一锚点，绝不分散建立多个过期文件）。
* **语义定位**：最高优先级认知罗盘。定义“我当前人生阶段的北极星、本月最重要的战役、以及坚决不碰的边界”。Agent 进行全局权衡与建议时，优先注入此文件作为核心约束。
* **文件结构**：
  ```markdown
  # 🎯 Active Focus

  ## 🧭 North Star (当前季度核心方向)
  > 保持轻量与专注：完成 KnowledgeRouter CLI 产品化，打通与 Ownly 的认知链接，确立本地数据主权。

  ## ⚔️ Active Battlegrounds (核心战役)
  - [ ] [[100_Project/2605_Ownly|Ownly 项目协同]]：打通本地资产与行程感知
  - [ ] [[KnowledgeRouter CLI]]：完成独立 Skill 发布与跨端接入验证
  - [ ] [[2601_ESG30|ESG30]]：推进学术文献与 dMRV 报告沉淀

  ## 🚫 Not-To-Do List (边界与反向清单)
  - 暂不开发复杂的 Electron GUI 界面，克制功能蔓延，守住 CLI 纯粹性；
  - 拒绝将任何核心生活/财务事实迁移至闭源云端。
  ```
* **维护规则**：人类与 Agent 在深度对话后轻量原地迭代更新，保持单文件轻盈。

---

### 切片 2：`idea` —— 年度巧思灵感流

* **物理位置**：`000_Inbox/idea.YYYY.md`（按年分库，按月分节）。
* **语义定位**：捕获闪念、思维火花、哲学顿悟、对话启发。
* **格式规范**：
  ```markdown
  # 💡 Idea 2026

  ## 2026-10

  - 💡 [2026-10-23 13:31] 我今天在使用 Muse 时突然意识到，个人数据必须保留在自己手上。围绕 Obsidian 打造一个面向 AI 的 Agent-ready 数据背包，实现数据主权与共生记忆。 #idea/seed
  - 💡 [2026-10-23 13:32] 这个概念也可以叫做“共生记忆”，与 AI 共同演进同一份外脑。 #idea/seed
  ```
* **生命周期**：幼苗期标记 `#idea/seed`；当推演成熟演化为一个明确项目时，移入 `100_Project/` 展开。

---

### 切片 3：`memo` —— 年度事实备忘流

* **物理位置**：`000_Inbox/memo.YYYY.md`（按年分库，按月分节）。
* **语义定位**：客观事实、规则配置、关键临时备忘、重要背景信息。
* **格式规范**：
  ```markdown
  # 📝 Memo 2026

  ## 2026-10

  - 📌 [2026-10-23 16:15] KnowledgeRouter 外部状态目录独立为 ~/.knowledge-router/vaults，支持通过 KNOWLEDGE_ROUTER_STATE_ROOT 环境变量灵活覆盖。
  - 📌 [2026-10-20 10:30] Clash Verge 核心端口在 TUN 模式下自动分配，本地脚本注意避开死代理端口。
  ```
* **沉淀规则**：高价值长期技术规范，后续由 Agent 或人工整理归档至 `200_Area/` 或 `300_Resources/`。

---

### 切片 4：`todo` —— 年度行动清单与日程流

* **物理位置**：`000_Inbox/todo.YYYY.md`（按年分库，按月分节）。
* **语义定位**：行动项与具体日程。原生携带时间块，无缝支持自研 `ical-plugin-pro` 插件向 GitHub Gist 推送并同步 Google Calendar。
* **格式规范**：
  ```markdown
  # 📋 Todo 2026

  ## 2026-10

  - [ ] ⏳ 2026-10-25 18:00 完成共生记忆 CLI 规范评审并提交代码库 #todo/inbox
  - [ ] ⏳ 2026-10-26 15:00 跟 PTTEP 线上对齐数据接口规范 #todo/inbox [start:: 2026-10-26T15:00:00+08:00] [end:: 2026-10-26T16:00:00+08:00]
  ```
* **状态标签**：`#todo/inbox`, `#todo/next`, `#todo/ing`, `#todo/done`。

---

### 切片 5：`contact` —— 实体人脉卡片库

* **物理位置**：`300_Resources/305_Contact/[[人名]].md`。
* **语义定位**：真实人脉网络与合作画像。卡片内嵌 Dataview 动态查询，自动反向聚合全库任务。
* **文件规范**：
  ```markdown
  ---
  name: "张一帆"
  aliases: ["Yifan", "老张"]
  relationship: "collaborator"
  tags:
    - contact/tech
  ---

  # 张一帆

  ## 📋 关联待办 (Dynamic Dataview)
  ```dataview
  TASK
  FROM "000_Inbox"
  WHERE !completed AND contains(text, this.file.name)
  ```

  ## 🪵 交互流 (Interaction Timeline)
  - 2026-10-18 14:30: 讨论天津实验场地的数据采集方案，明确由对方提供原始波形。
  - 2026-09-12 10:00: 初次沟通项目接口标准，约定采用标准 JSONL 协议。
  ```

---

### 切片 6：`ownly` —— 资产、物品与经历数据库

* **物理位置**：`WYQD/`（或 `Ownly/`）。
* **语义定位**：**原生对齐现有的 Ownly 1.2.3 规范**。涵盖人类在物理与数字世界的完整所有权与体验花费。
* **子目录结构**：
  * `Objects/`：重要设备与硬件资产（记录型号、购买时间、维保与体验）；
  * `Accounts/` & `Snapshots/`：脱敏后的账户结构与财务净值快照；
  * `Reviews/`：周期性决策复盘；
  * `Trips/` & `Trip Visits/` & `Trip Legs/`：旅行足迹、城市生活路线与经历档案。
* **协同机制**：共生记忆 CLI 提供原生索引、双链关联与预算检索，绝不修改或破坏 Ownly 原生数据格式。

---

## 3. CLI 交互协议契约 (The CLI Protocol)

为消除冗余并提供极其干净的命令面，CLI 规范严格统一为以下核心接口：

### 3.1 流式收件箱操作 (Stream Appends)
基于底层独占文件锁（`inbox.lock`），自动定位当前年份并维护月度标题：
```bash
# 记录待办 (支持自动注入 Dataview 日历时间块)
kr inbox append todo "梳理产品规格书 #todo/inbox"

# 记录备忘
kr inbox append memo "记录关键架构决策事实"

# 记录巧思
kr inbox append idea "关于共生记忆背包的想法 #idea/seed"
```

### 3.2 联系人协同操作 (Contact Operations)
基于 `ContactResolver` 别名/子串模糊匹配，维护人际上下文：
```bash
# 追加交互事实至联系人卡片
kr contact append "张一帆" "天津场地实验数据已接收完毕"

# 查询联系人画像与关联待办
kr contact show "张一帆"

# 查看人脉协同雷达总览
kr contact radar
```

### 3.3 认知唤回与治理 (Recall & Triage)
```bash
# 全库全文与语义检索
kr search "共生记忆" --limit 5 --json

# 按 Token 预算精准获取指定项目/人脉的上下文简报
kr brief "ESG30" --budget-tokens 1500 --json

# 待办实体对齐与多维分诊
kr triage todo --year 2026

# 断链诊断与收敛
kr review --json
kr converge --json
```

---

## 4. 双重解耦机制 (Decoupling Specification)

### 4.1 与 Obsidian 解耦
* 纯标准 CommonMark / GFM 语法；
* 所有的检索、追加、修改完全由外部 Python 进程原子执行，无论 Obsidian 是否运行，CLI 均 100% 可用。

### 4.2 与 Ductor 解耦
* **状态路径独立**：解除原有硬编码的 `~/.ductor/vaults`；
  * 默认产品路径：`~/.knowledge-router/vaults`；
  * 环境变量覆盖：`KNOWLEDGE_ROUTER_STATE_ROOT`；
  * CLI 参数指定：`--state-root <path>`。
* **独立 Skill 分发**：作为独立 Skill（包含 `SKILL.md` + scripts），可在 Codex、Cursor、Claude Code 或无头环境独立安装使用；在 Ductor 中则只需配置环境变量即可作为底层知识引擎协同。

---

## 5. 后续演进路线

1. **Step 1 (已就绪)**：本规格书正式归档，确立权威 SSOT。
2. **Step 2 (架构解耦)**：修改 `scripts/registry.py`，将默认状态路径由 `~/.ductor/vaults` 改造为 `~/.knowledge-router/vaults`，增加环境变量识别。
3. **Step 3 (脚手架交付)**：实现 `kr scaffold` 命令，支持为全新 Vault 生成上述“三大流式收件箱 + FOCUS.md + 305_Contact + WYQD/Ownly”的标准模板结构。
4. **Step 4 (Skill 包装)**：提供开箱即用的通用 Agent `SKILL.md` 与提示词配方。
