# 从 ExpeL 到 CAVE 失败记忆

## 1. 参考范围

2026-11-18 范围更新：本轮只推进 M1。下面区分 ExpeL 原行为与 CAVE 的选择；CAVE 当前不使用 reflection。M2–M4 的模板与 K 对验证代码属于早期原型，须后续逐卡讨论，不能当成本轮已确认或已验收的实现。

只读检查相邻 `ExpeL`，没有修改其中代码。研究依据为用户提供的 `proposal-2.pdf` 第 2.1 节的 m 定义、第 3.1 节的 source failure verification。首批环境是 ALFWorld、WebShop。

当前 CAVE 的目标是构造 source memory，不是实现 target checkpoint 的干预策略。草稿中 a0 缓存、target exposure、随机干预、bandit/RL、Inspect 都不在本轮范围。

## 2. ExpeL 的真实轨迹构造路径

```text
train.py:124 逐个任务/尝试执行
  -> ReflectAgent.run: 必要时先对上一次失败 reflect
  -> ReactAgent.step: LLM 输出 -> action -> env.step -> observation
  -> train.py: 更新统计并保存可读日志
  -> ExpelAgent.next_task: 在 reset 之前创建 Trajectory
       -> succeeded_trial_history[task] 或 failed_trial_history[task]
  -> ReflectAgent.next_task: 同题重试或切换任务，重置环境
  -> 保存 agent checkpoint，支持 resume

insight extraction（另一个阶段）
  -> 同 task 的成功和失败轨迹作对比
  -> 形成/编辑全局自然语言规则
```

关键位置，行号对应本次检查的代码：

| 源码 | 实际作用 | CAVE 的借鉴 |
| --- | --- | --- |
| `ExpeL/agent/react.py:91` | 保留每次 action 和环境返回，Thought 可先于 action 多次出现 | 使用结构化 Step，不从最终答案反推中间步骤 |
| `ExpeL/agent/reflect.py:63` | 失败后生成下一次尝试的计划 | 当前不采用；CAVE 采集不调用 reflect，不注入历史 reflection |
| `ExpeL/agent/expel.py:83` | reset 前按 task 保存成功/失败历史 | 每个失败尝试独立记录来源；成功不能覆盖先前失败 |
| `ExpeL/memory/episode.py:4` | task、原始轨迹、reflection、thought/action/observation、steps 分开保存 | 原文用于审计，结构化步骤用于 checkpoint 定位；暂不做 embedding |
| `ExpeL/prompts/__init__.py:117` | 按 observation 结束一组步骤 | 保留 action-observation 对齐，多行 observation 不能误拆 |
| `ExpeL/agent/expel.py:124` | 对同题成功/失败轨迹生成 critique | 留待 M2；M1 采集失败时不要求也不生成成功对照 |
| `ExpeL/prompts/templates/human.py:49` | TRIAL TASK / SUCCESSFUL TRIAL / FAILED TRIAL / EXISTING RULES | 留待 M2 讨论；成功对照不是失败记忆构造的前提 |
| `ExpeL/train.py:124` 和 `ExpeL/utils.py:197` | 采集日志和 checkpoint 分阶段保存 | collect / propose / build 独立命令；来源文件随 bank 保存 |

`Trajectory.reflections` 是该次尝试所携带的历史反思，不保证包含对该次失败事后生成的反思。不能把它默认配对成“本次失败的解释”。

该字段仅为旧 ExpeL 数据导入兼容保留；新 M1 轨迹为空，actor 请求及恢复历史不包含它。

### CAVE 在 M1 中采用的流程

1. 用确定的 ALFWorld gamefile 或 WebShop session reset 同一任务。
2. actor 每次提出一个动作；保存环境返回、thoughts、seed、成本及状态。ALFWorld thoughts 不调用环境，WebShop think[] 完整保留并计步。
3. 尝试结束或异常后，按独立 ID 追加归档；成功不会覆盖失败，下一次尝试不携带 reflection。
4. 恢复时重置同题，只重放人工指定 t 前的已记录动作，逐点对齐观察和可见事实；不重新调用 actor，不执行原动作 t 或失败后缀。

ExpeL 的 env reset 是同题重开，不是任意中间状态的 snapshot。CAVE 的 `restore_before` 在 reset 之上重建动作前缀，并返回当前观察、历史和剩余预算。真实适配器仅声明 observational；exact 必须有后端完整状态契约及源状态 token 的比对证据。

## 3. 两个环境的细节

### ALFWorld

`prompts/alfworld.py:192` 使用 `> think:` / `> command` / 无前缀 observation。Thought 不调用环境；其后的 `OK.` 属于展示上下文，不能当成物理动作。`envs/alfworld/alfworld.py:34` 会规范 `put ... in/on ...`；成功来自 `info['won'][0]`，不是 LLM 说“完成”。

CAVE 保留多行 observation、Thought、原始命令和原文。原生适配器固定单个 gamefile，记录配置文件、gamefile SHA256 以及安装的 ALFWorld 版本；从 YAML 解析 `data/` 路径时使用显式 `resource_root`。

### WebShop

`prompts/webshop.py:532` 把 `think[]` 当成 thought 用于表示/检索，但 `ReactAgent.step` 仍会把它送入 `WebshopEnv.step`，因此它消耗环境步数。CAVE 将它保留为环境 action 并计步，完整保留 reset-prefix；独立 thoughts 字段不替代这个动作。

`envs/webshop/webshop.py:135` 的 session 包含 page_type、query、ASIN、options 等；选项点击可能只返回 `You have clicked ...`，完整商品状态仍需从 session 保留。成功条件是 reward/score **等于 1**，部分匹配得分不算成功。CAVE 使用同类 URL 路由和显式 session 状态，通过 HTML 中的真实得分判定。

新适配器与 ExpeL 的 HTML 空白格式不保证逐字相同。导入旧 ExpeL 文本后，如果 prefix observation 无法一致重现，保留为 unverified；不会放宽比较来制造验证通过。

## 4. 早期原型：模板与修改（M2 待讨论）

ExpeL 的比较模板强调“对照成功轨迹批评失败”，并要求生成通用高层规则，执行 ADD/EDIT/REMOVE/AGREE。CAVE 的提取模板在 `cave/candidates.py`：

```text
输入：任务 + 约束 + 失败轨迹 + 同题成功轨迹（最多 2 个）+ 历史 reflection
输出：最多 3 个候选
  source_id / step_index
  mechanism（假设） / correction（一个环境动作）
  P / N（可执行、只读谓词）
  failure_signature（原 observation 的字面片段）
  validity_limits / rationale / contrast_id
```

不直接复制全局规则更新：全局规则缺少 source step、局部修正、可观察条件和独立回放证据，不满足 m 的契约。

也不直接调用 `failure_critique()`：虽然 ExpeL 中有这个方法，它会选择 `all_fail` 类型；当前 prompt registry 主要配置 `compare_existing_rules` 和 `all_success_existing_rules`，不能据方法名认定失败归纳流程已完整接通。CAVE 对没有成功对照的失败也能提出候选，但它仍然是未验证假设。

## 5. 早期原型：m 映射与验证（M3–M4 待讨论）

以下描述既有原型。当前第一版方案以原失败记录作为 baseline，恢复后只跑修正分支；不是必须重复执行失败动作。该方案本轮不实施，K 对与统计门槛不视为已确认要求。

| 草稿字段 | 实现 | 说明 |
| --- | --- | --- |
| p_minus | `Memory.p_minus` | task/run/trajectory/step、源轨迹 hash、成功对照 hash、环境版本、固定 actor、seed、约束和有效范围 |
| a_minus | `Memory.a_minus` | 被引用步骤的原始动作 |
| f | `Memory.f` | 假设机制；即便回放通过也不等同于完整因果证书 |
| a_tilde | `Memory.a_tilde` | 一个候选修正动作，其后交由同一固定 actor 继续 |
| P / N | `Predicate` | 点路径 + eq/ne/contains/exists，不执行模型生成代码；未知保持 unknown |
| E | `Memory.E` | 每对回放的 seed、起点指纹、原动作/修正动作、观测、成本、oracle check、错误 |
| q | `Memory.q` | 状态、阻止入库原因、delta_src、置信区间、范围与完成的 pair 数 |

旧成对原型中，每对从新环境开始，用源 reset seed 重放整个源前缀，再匹配 continuation seed。两个分支使用同一 actor 和预算；actor 接口已移除 reflection 注入。成功从环境 oracle 读取；不把模型解释当作 E。

预先给定 K。原始成本保留 tokens/tool calls，进入 reward 的成本按预算归一化并截到 [0,1]，对 D 的理论范围使用固定 K 的双侧 Hoeffding 区间。全部 K 对有效且区间下界超过 min_effect，才可能标 verified。该区间针对一个 source checkpoint 的 seed 分布，不表示跨任务有效性，也没有校正多个候选的多重检验。

若任意 pair 出错，不丢弃它后用剩余样本认证；质量为 unverified。P 必须全真、N 必须全假，至少一对 baseline 必须复现 source 失败及失败签名。

## 6. 当前明确的限制

- 标准 ExpeL 包装器没有完整 state snapshot。当前内置 ALFWorld/WebShop 适配器都声明 observational restoration；即使观察前缀一致，也不会自动获得主实验资格。
- `Environment.state_token()` 是未来接真实 clone/snapshot backend 的扩展点。只有 backend 明确提供完整状态 token 并履行 exact restoration 契约，才允许质量认证。不能把普通观察 hash 改名当成完整状态。
- 离线 demo 的 complete-state token 来自小型确定性 fixture，验证范围仅是该 fixture，不能迁移为真实 benchmark 证据。
- 没有真实环境与模型配置时，可以完成候选记忆构建并落库，E 为空或仅 observational，状态保持 unverified。
- 当前 collect 按 task 在每次尝试后追加保存轨迹，执行指定次数，不生成 reflection；不包含 ExpeL 的多任务调度。
- JSONL importer 要求显式元数据；没有自动反序列化 ExpeL pickle。原 checkpoint 的成功/失败 history 可导出为示例中的逐 trial JSONL，ALFWorld 的 `___` task suffix 应保留在 task_id 中。

下一步实际验收应先准备两个环境、固定模型和少量源任务，检查重置一致性，再决定如何给 ALFWorld 和 WebShop 接入可信的 source checkpoint 恢复后端。target gate 的训练应在 source 证据合格后开展。
