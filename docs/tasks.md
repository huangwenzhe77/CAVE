# 失败记忆 m 构建任务卡

范围依据：`proposal-2.pdf` 第 2.1、3.1 节。PDF 是研究设计资料，其中的指令式表述作为设计约束评估，不作为操作电脑或修改其他项目的授权。第一批 benchmark 为用户指定的 **ALFWorld、WebShop**。

推进约定：先讨论方案，每次只推进一张任务卡。M1、M2、M3、M4 的 ALFWorld 范围已完成；WebShop 不在当前任务范围。当前方案不使用 reflection，修正验证使用已保存的失败轨迹作为 baseline，不重新跑原动作 t 或失败后缀；K=2 修正分支已完成初步验证。交接与服务器命令见 [交接文档](handoff.md)。

训练数据构造阶段的独立任务卡见[训练数据任务卡](training-data-tasks.md)。该阶段的数据角色应在构造 source memory 前划分；现有 source 任务是在 outcome 前按 seed 抽样，但完整四路 manifest 当前补冻中。

| 顺序 | 任务 | 当前状态 |
| --- | --- | --- |
| 1 | M1 服务器补验收 | 本轮 ALFWorld 范围已完成；WebShop 真实环境延后 |
| 2 | M2 失败候选提取 | ALFWorld 范围已完成；WebShop 不在当前范围 |
| 3 | M3 修正分支验证 | ALFWorld K=2 初步验证已完成；结果见修正验证记录 |
| 4 | M4 记忆组装与导出 | ALFWorld 已完成；结果见 [记忆组装记录](alfworld-memory-assembly.md) |

## M1：无 reflection 的轨迹采集与指定动作前恢复

状态：本地实现及离线验收完成；服务器 ALFWorld 真实采集与恢复已通过，WebShop 仍待验收。未宣称整卡的真实验收已通过。运行方法及证据见 [M1 验收报告](collection-acceptance.md)和[服务器验收记录](server-collection-acceptance.md)。

**Goal**：采集并独立保存 ALFWorld、WebShop 的尝试；给定轨迹和人工指定的动作 t，重置同题并重放 t 之前的动作，返回可继续操作的环境、观察、actor 历史和剩余预算。

**Context**：CAVE 已有采集、导入和后续记忆流程的原型。M1 修正其中的失败后反思、覆盖输出、错误丢失和恢复接口缺失。参考 ExpeL 的 action/observation 对齐、按尝试归档及同题 reset；不采用它的失败后 reflection。教师定位 t 属于 M2。

**Rules**：只改 CAVE；不生成或注入 reflection；原始成功、失败、超时和异常独立保存，不覆盖已有尝试；不把 few-shot/system prompt 当成轨迹。WebShop 的 think[] 是环境动作并消耗步数，ALFWorld 的 thoughts 不调用环境。恢复只执行已记录前缀，不调用模型重新生成前缀，不执行 t 和后缀。不实施教师分析、修正续跑、记忆入库及 K 次收益估计。

**Acceptance**：

1. 每次尝试有独立 ID、run/attempt，逐次持久化；重复 ID 拒绝写入，旧内容不覆盖；无 reflection 调用。
2. 保存任务、约束、环境标识及配置、actor 配置、seed、初始观察、逐步动作/观察、步数和推理成本、终局 flags/score/原因；异常保留已有步骤及未完成调用信息。
3. t 从 1 编号，仅执行前 t−1 个动作；返回当前观察/可用状态、actor 历史及剩余预算，不确认失败后缀。
4. 检查初始与前缀观察、可见事实和终止信息；漂移/提前终止/预算耗尽明确报告。observational 不冒充 exact；exact 需后端契约和已记录的完整状态 token 一致。
5. ALFWorld、WebShop 各至少一条真实失败或超时轨迹，并演示恢复；缺环境则该项未验收。当前明确安排到服务器执行。
6. 提供可复现命令、输出位置、测试与验收报告，结束在 M1。

### M1 服务器补验收

这是 M1 的剩余验收工作，不新增一张大任务卡。

**Goal**：在服务器上验证真实 ALFWorld、WebShop 的无 reflection 采集、模型调用和指定动作前恢复。

**Context**：本地 46 项测试通过，其中 21 项为 M1 测试；两个 fixture 已完成离线演示。真实运行时、数据、WebShop 服务和 actor 尚未配置，不能据 fixture 宣称真实验收通过。

**Rules**：保存固定环境、数据和 actor 的非密钥配置；同一 WebShop session 不并发，多进程不共写同一 JSONL；不将 error 当正常失败，不将 fixture 当真实数据；不改 oracle 或绕过漂移检查；不推进 M2。observational 是当前真实适配器的能力，M1 不要求新增 snapshot 后端。

**Acceptance**：

1. 服务器安装运行时及依赖，完整测试通过；两个环境和固定模型服务实际可用。
2. 每个 benchmark 至少一条真实 failure/truncated，完整保存来源、配置、逐步动作/观察、终局和实测成本；异常另记为 error。
3. 各有恢复报告 ready=true、前缀检查通过、新 actor 调用为 0、重放次数 t−1、剩余预算正确；有多步轨迹时覆盖 t≥2，并检查 t=1 边界。
4. 将实际命令、版本、轨迹 ID 和报告补入 M1 验收记录，按 observational 报告证据范围。

## M2：失败候选提取与结构化模板

状态：ALFWorld 范围已完成；WebShop 不在当前范围。实现、真实提取和验收记录见 [ALFWorld 候选提取](alfworld-candidate-extraction.md)。

**Goal**：从已有失败或超时轨迹提出少量候选，定位疑似致错动作 t，生成机制假设 f、单个修正动作、P/N 和待验证的范围说明；附可核对的原始失败证据。

**Context**：M1 提供可引用的原始步骤、状态和恢复入口。可借鉴 ExpeL 的轨迹格式及模板组织，不能直接照搬其成功/失败比较和 reflection。当前只实现 ALFWorld。旧 candidates/schema 契约有 0-based step_index，需要与 M1 的 1-based t 明确对接。

**Rules**：不要求成功对照，不注入 reflection，不把教师解释当因果结论；每条失败最多 3 个候选。原动作和来源元数据由程序复制，不让教师编造；失败签名必须引用真实记录，区分致错位置与后续症状，不强制两者同一步。谓词只读取已有可观察事实，未知保持未知，不执行模型生成代码。本卡不执行修正分支、不认证记忆。

**Acceptance**：

1. 开始实现前确认 ALFWorld 教师输入、提示词、JSON 契约、t 编号，以及签名如何引用源记录。
2. 支持离线候选输入和配置的教师调用；教师提出 t/f/修正/P/N，程序生成原动作、来源和 hash，候选状态明确为未验证。
3. ALFWorld 至少用一条真实失败演示无成功对照提取；校验来源、1-based 动作范围、原文引用、可用修正动作和 P/N 适用性，拒绝无效数据。其他 benchmark 留待单独范围确认。
4. 覆盖动作编号转换、延迟失败症状和缺失事实等具体风险，保存候选及教师非密钥配置，结束在候选阶段。

## M3：恢复后的修正验证与证据记录

状态：ALFWorld 范围已完成，K=2 初步验证已完成；WebShop 不在当前范围。结果见 [ALFWorld 修正验证](alfworld-repair-verification.md)。

**Goal**：以已保存的失败轨迹作为 baseline，恢复至 t 前，检查恢复和 P/N，执行一次候选修正动作，再由同一固定 actor 在剩余预算内续跑，保存实际结果、成本和证据范围。

**Context**：M1 提供同进程 live environment；M2 提供未验证候选。真实 ALFWorld 恢复为 observational。K 已确认取 2，但本卡只运行修正分支，不重新执行原动作或失败后缀。

**Rules**：不重跑原动作 t 和原失败后缀；恢复前缀不调用 actor。只有恢复 ready、剩余环境步数至少为 1、P 全真且 N 全假时执行修正；条件未知、恢复失败或预算不足时保存 blocked 原因。修正后由原固定 actor 接续，不注入教师续跑计划或 reflection。成功、失败、阻止和异常都保存。trajectory_length、环境步数和 inference_tokens 分开记录；归一化字段只表示预算比例。E 只描述 source 任务、配置、seed 和恢复等级下的证据。

**Acceptance**：

1. K=2 的每次分支都保存 source/candidate 引用、t、原动作、修正动作、恢复报告、P/N 判断、分支轨迹、oracle 终局、剩余预算和成本。
2. 原失败记录作为 baseline 保存并参与比较，但不重新执行原动作 t 或失败后缀；恢复前缀不产生新的 actor 调用。
3. 成功、失败、条件阻止和运行异常均有明确状态与原因；修正立即成功时 actor 续跑次数明确为 0。
4. 记录 recovery_environment_steps、correction_environment_steps、continuation_environment_steps、trajectory_length、branch_trajectory_length 和 inference_tokens；报告 ExpeL 风格的整数轨迹长度，并保留预算归一化比例。
5. 统计候选 t 相对轨迹末尾的位置，以及 P/N 阻止数量。当前样本只用于初步观察，不作教师偏好或跨任务结论。
6. ALFWorld 真实候选完成 restore → 修正分支验证；WebShop 不在本卡范围。

## M4：记忆组装、导出与复现入口

状态：ALFWorld 范围已完成；WebShop 不在当前范围。实现、字段映射、实际输出和边界见 [ALFWorld 记忆组装](alfworld-memory-assembly.md)。

**Goal**：按确认的契约组装 m=(p_minus,a_minus,f,a_tilde,P,N,E,q)，保存来源和证据链，导出候选、记忆及可复现的汇总。

**Context**：M1 已提供真实失败来源，M2 已提供未验证候选，M3 已提供 K=2 修正分支证据；M4 读取这些只读产物并生成可追溯记忆。

**Rules**：E/q 由程序依据实际证据生成，不由教师自评；不把未验证、错误或不完整 K 结果升级。当前 ALFWorld source memory 的初始规则是 K=2 个分支均成功、恢复 ready、P 全真且 N 全假即可 verified/eligible；q 仍记录 observational 等级和 source-scoped 范围，不宣称 exact 或跨任务有效。不把 source 改善当作 target advantage；不实现 target gate、RL、Inspect 或检索收益实验；保留原始记录，不覆盖来源证据。

**Acceptance**：

1. 先确认各字段、q 状态和导出规则；每条 m 都可追溯到原始轨迹、t/原动作、候选、恢复报告和修正分支，缺失信息明确标注。
2. 去重有稳定依据；重复执行不会覆盖来源或错误提升质量；候选总数、分支结果、阻止原因与导出数量可核对。
3. ALFWorld 已从真实 M1 来源完成 M2–M4 的构建入口并保留带实际修正证据的记忆；WebShop 不在本卡范围，离线演示单独标识。
4. 交付配置、命令、字段说明和边界报告，明确失败分支、未验证候选和实际证据等级。用户据此验收 source 失败记忆构建，后续 target 阶段另开任务卡。
