# 可选共生记忆工作流规格（v1.2）

状态：已实现行为规范，2026-10-23。架构与写入规则以 [DESIGN](../DESIGN.md)、
[ADR-001](decisions/001-unified-memory-workflows.zh.md) 和[安全契约](../references/safety_contract.md)为准。

## 定位与数据所有权

六切片是建立在通用证据/Context 内核上的可选工作流。已有 Vault 可直接 attach，不必调整目录。
原始 Markdown 由用户持有；版本化定义、变更日志、意图回执属于外部持久记录，不能当成缓存丢弃。

## 切片与载体

| 切片 | 模板位置 | 已实现能力 |
|---|---|---|
| Focus | `000_Inbox/FOCUS.md` | 当前关注、目标与边界的单文件模板，可由调用方检索使用 |
| Idea | `000_Inbox/idea.YYYY.md` | 带时间的灵感追加，默认 `#idea/seed` |
| Memo | `000_Inbox/memo.YYYY.md` | 带时间的事实备忘追加 |
| Todo | `000_Inbox/todo.YYYY.md` | 任务追加、新条目实体链接和标签分类；历史分诊可预览 |
| Contact | `300_Resources/305_Contact/` | 模板、唯一人物解析、交互日志、待办与项目线索查询 |
| Ownly | `WYQD/` | 物品、账户、快照、复盘、旅行等目录模板；通用 Markdown 索引 |

Focus 的优先使用依赖调用方协议，工具不会自动给所有模型注入最高优先级指令。Ownly 专用
业务运算、日历同步、主动提醒和金融数据脱敏不属于本次实现。任务正文可保留用户提供的
`[start:: ...] [end:: ...]`，CLI 不猜测时间块、不自动发送到日历。

## 年度流格式与追加

```markdown
# 📝 Memo 2026

## 2026-10

- 📌 [2026-10-23 13:31] 已确认接口定义
```

`todo` 使用 `- [ ] ⏳ 时间 内容 #todo/inbox`，完成的旧缓冲任务保留 `- [x]`。
月份是目标年度和当前月份；已有相应末节则追加条目，否则在末尾追加同名章节。
记录不在旧章节中间插入，不改写旧待办。BOM、换行和所有旧字节保持原样。

## 命令与策略

```bash
kr scaffold "/path/to/vault" --name personal
kr attach "/path/to/vault" --name personal --write-policy append-create
kr inbox append memo "内容" --vault personal --idempotency-key stable-request-1
kr inbox append todo "新任务" --vault personal --preview --json
kr contact append "姓名或精确相对路径.md" "交流记录" --vault personal
kr triage todo --vault personal --json
kr inbox drain --vault personal --json
```

scaffold 是显式初始化边界，默认 `off`，已有文件不覆盖，链接路径拒绝。运行时不创建父目录。
明确的 append 动词提交记录；`--preview` 只冻结计划。历史分诊是全文修改，必须在
`reviewed-edit` 下提交；`append-create` 不授权替换。独立授权和完整恢复原文见安全契约。

人物路径是稳定身份。唯一文件名、别名或唯一子串可作为选择器；同名/别名冲突返回候选，
禁止静默选择第一个。模板与链接文件不作为联系人。人工编辑发生在预览后，提交将因来源
哈希变化而拒绝。全文分诊不会隐式导入旧缓冲。

## 重试与导入

`workflow.*.json` 将幂等键和输入绑定到冻结计划。相同键重用原计划、目标和时间，不重复记录；
输入变化时拒绝。成功意味着索引刷新完成；`applied_pending_index` 明示已写入、索引待更新，
退出 2，并提供原 plan id 供续接。

旧 `Quick_Capture.md` 仅通过显式 drain 导入。来源路径、条目原文及相同原文的出现序号构成
稳定意图键；保持完成状态，原缓冲不清空、不搬移。中断后再次运行由回执去重。
旧日记迁移脚本只保留只读分析，不再改写或移动用户原文。
