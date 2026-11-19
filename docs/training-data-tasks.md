# 训练数据构造任务卡

范围：ALFWorld target 训练数据。source 失败记忆 M1-M4 已完成；WebShop 不在本阶段。PDF 草稿是研究设计资料，实际执行以本任务卡和用户确认的范围为准。

推进约定：一次只执行一张任务卡。source/target/validation/test 的划分应先于 source memory 构造；当前 source 任务已完成构造，因此如实保留其既有抽样历史，不声称四路 manifest 在 M1 前已冻结。

| 顺序 | 任务 | 状态 |
| --- | --- | --- |
| T1 | 冻结 target 采样方案并审计 ALFWorld checkpoint 恢复能力 | 部分完成：划分规则和现有 adapter 审计已完成；逐游戏 manifest、target 采样协议和底层精确状态探测待完成 |
| T2 | 采集 Silent/Remind 随机干预数据 | 未开始 |
| T3 | 生成标签、质量检查并导出训练数据 | 未开始 |

## T1：冻结四路数据角色与恢复能力审计

**Goal**：固定 source、target-train、validation、test 的任务归属及 target checkpoint/记忆采样协议；确认 ALFWorld reset-prefix 回放是否足以支持同一 checkpoint 的配对干预，并据此冻结后续采集设计。

**Context**：已有十条 source ALFWorld 任务在运行前从官方 `train` 中按 seed 42 抽取，不按成败选择；M1-M4 及真实 ALFWorld source 运行已完成。`valid_seen` 中的 bowl/coffeetable game 已做 M1 connectivity smoke，应从 validation 排除。`valid_unseen` 尚未用于 CAVE 设计或采集。真实服务器恢复结果为 observational，详见 [服务器验收记录](server-collection-acceptance.md)。

**Rules**：source 十条任务及其 source memory 固定，不移入 target；target-train 只从官方 `train` 中选取，并排除 source 的整个 parent template group；validation 使用 `valid_seen`，排除既有 smoke template group；test 保留 `valid_unseen`，不得用于调方案。不得按已观察到的 source/target outcome 重新分组。source P/N 与修正动作中的物体名可能是字面值，不得直接把 source 字符串匹配当成跨任务适用性；需要显式绑定与验证。只重放前缀、观察相同或 seed 相同都不能单独证明完整状态相同；没有完整状态 token 的记录不得标记 exact。不得把 M3 的 K=2 当成 target 实验样本量。

**Acceptance**：

1. 服务器上生成不可覆盖的 `configs/alfworld-study/splits.json`，其中每个 task 记录官方 split、相对 gamefile、task type、gamefile 与轨迹元数据 hash；记录各 role 数量、各官方 split 的 inventory hash、数据根目录及输入 manifest/exclusion 文件 hash。
2. 验证 source 与 target-train 的 task/template 无交叉；既有 valid_seen smoke 模板不进入 validation；test 全部来自 `valid_unseen`。清单生成不读取任务 outcome。
3. 记录恢复能力证据和决策。当前代码/真实服务器证据表明：ALFWorld 可 reset 同题并重放动作前缀，但 `AlfworldEnvironment.state_token()` 返回 `None`，检查只覆盖观察和可见事实；因此现有后端是 observational，不能声称精确 checkpoint clone 或配对反事实。固定 gamefile/seed 且 `domain_randomization=false` 让确定性重建有实现可能；TextWorld 1.7.0 的底层 `TextWorldEnv.copy()` 也提供候选实现路径，但 ALFWorld 包装层没有对 CAVE 暴露完整状态 clone。要升级为 exact，需覆盖底层 TextWorld 状态、随机数状态、ALFWorld wrapper 状态和预算计数，并做独立分支恢复与续跑比较。T2 默认采用 checkpoint 级随机化独立 arms，分配概率预先记录；只有新后端通过完整状态对照测试后才可切换配对方案。
4. 在采集 target outcome 之前冻结 checkpoint 选择和每点至多一条记忆的选择规则，包括 source 物体/动作如何绑定到 target 可观察事实；冻结干预前 `x/a0` 字段、Silent/Remind 分配概率和 seed 计划、actor/记忆库版本、预算及效用定义。不存在的 target 数据不得伪造 checkpoint ID 或 memory ID。
5. 保存 manifest、实际数据路径与版本、运行命令及检查摘要；结束在 T1，不开始 intervention 采集。

### T1 当前执行结果

- 已固定角色规则：source 为现有十条 seed-42 ALFWorld 任务；target-train 为官方 `train` 中排除 source template groups 后的所有合格游戏；validation 为排除旧 smoke group 后的 `valid_seen`；test 为 `valid_unseen`。
- 当前 adapter 的能力审计已完成：真实服务器 M1 的 t=1/t=2 恢复分别匹配 5/5 与 9/9 个可观察检查，但等级均为 observational。精确状态探测尚未执行；TextWorld 底层有 `copy()`，ALFWorld 可能通过包装层扩展支持完整 clone，当前代码没有完整 token，暂不能用于 paired counterfactual。
- 新增 manifest 生成器和 fixture 测试。当前 Windows 工作区没有 ALFWorld 数据目录或运行时，服务器 SSH 连接也未成功；用户选择稍后自行在服务器运行。因此本轮无法生成逐游戏 manifest 或检查完整状态 clone。T1 的第 1、2、4、5 项尚未全部验收；checkpoint/记忆采样、source 到 target 的变量绑定、actor 和效用协议还需结合实际记忆库冻结。须在服务器运行下列命令后补齐本节及 `configs/alfworld-study/splits.json`：

```bash
python -m cave.alfworld_data_splits \
  --data-root /home/ljj/alfworld-data \
  --source-manifest configs/alfworld-history-sample-seed42/manifest.json \
  --exclusions configs/alfworld-study/validation-exclusions.json \
  --output configs/alfworld-study/splits.json
```

## T2：采集 Silent/Remind 随机干预数据

**Goal**：在冻结的 target checkpoints 上，采集 Silent 和 Remind 两组数据，用于估计提醒对动作和终局效用的影响。

**Context**：T1 的四路数据角色规则已冻结，逐游戏 manifest 生成后才可开始采集。现有 ALFWorld 恢复为 observational；M4 提供 source memory，target checkpoint 和 target 侧干预轨迹尚未实现。

**Rules**：每个 checkpoint 在干预前冻结无记忆动作 `a0` 和特征 `x`。Silent 执行缓存的 `a0`；Remind 提供一条预先选定的 memory，再由相同冻结 actor 重新采样动作。当前默认每个 checkpoint 随机进入一个 arm，不伪造同 checkpoint 的配对差值；仅在完整状态恢复通过验收后才允许两臂克隆配对。两臂使用相同工具规则与续跑预算，记录实际 propensity。M3 source correction replay 不等同于 target Remind。

**Acceptance**：

1. 所有 target trials 都引用 T1 manifest 中的 target task，记录 checkpoint、`a0`、`x`、memory ID、arm、`b(z|x)`、seed、固定 actor/环境配置、全量轨迹、终局和成本。
2. Silent 与 Remind 均有样本，arm 分配遵循预注册随机化规则；错误、截断、blocked 均保留。
3. 当前 observational 路径只输出 randomized single-arm cohort 字段；不输出个体级 paired `D/H`。
4. 输出可复现的采集清单和汇总，结束于 trial 数据。

## T3：生成标签、质检与导出

**Goal**：把 T2 原始 trials 转成可训练、可审计的数据集，并验证 split 隔离及标签来源。

**Context**：T2 提供 intervention trials；M4 提供 memory 来源、P/N 与 source evidence。gate/RL 模型尚未进入本阶段。

**Rules**：成功标签来自环境 oracle；教师不得编造 outcome、cost、propensity 或配对标签。`rho` 表示 memory 在 target 状态下是否有效且适用，`e` 表示干预前 `a0` 是否暴露对应失败机制；语义判断需保存证据与来源。只有合规精确配对数据才能生成个体级 `D=R(1)-R(0)` 和 `H=I{Y(0)=1,Y(1)=0}`。错误、未知和预算截断不可合并为普通失败；训练抽样或过采样保留原始 propensity。本卡不训练 gate 或 RL。

**Acceptance**：

1. 导出字段可追溯到 target trial、memory、source evidence 和 T1 split；包括 `x/a0`、`rho/e`、arm、propensity、oracle outcome、成本及数据质量状态。
2. 只对符合精确配对契约的数据生成 `D/H`；独立 arm 数据不生成个体配对标签。
3. 自动检查重复、缺字段、split 泄漏、无效 propensity、配对状态不一致和标签来源，导出稳定可复现。
4. 报告 split、arm、`rho/e` 组合及异常/未知数量；结束于数据集导出，不开始训练。
