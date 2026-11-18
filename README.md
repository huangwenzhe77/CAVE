# CAVE-Mem：失败记忆构建

研究目标来自 `proposal-2.pdf` 中的 source-side memory：

`m = (p_minus, a_minus, f, a_tilde, P, N, E, q)`

第一批环境是 **ALFWorld 和 WebShop**。任务拆分及验收见 [任务卡](docs/tasks.md)，ExpeL 的思想、流程、模板和代码对应关系见 [借鉴分析](docs/expel-reference.md)。原 ExpeL 项目未修改。

服务器迁移、当前实现边界和接手顺序见 [交接文档](docs/handoff.md)：ALFWorld 的 M1 真实验收、M2 候选提取、M3 修正分支验证和 M4 记忆组装已完成；WebShop 留待单独任务卡。

**当前 ALFWorld 范围已完成 M1-M4：** 无 reflection 轨迹采集、候选提取、K=2 修正分支和记忆组装均有服务器产物；WebShop 延后。详见 [任务卡](docs/tasks.md)、[修正验证](docs/alfworld-repair-verification.md) 和 [记忆组装](docs/alfworld-memory-assembly.md)。

## 快速验证

Python 3.10+，离线核心只使用标准库；完整测试另需安装 `.[webshop]` 可选依赖。在 CAVE 目录运行：

```powershell
python -m cave offline-protocol-demo --output-dir output/offline-protocol-demo
python -m unittest discover -s tests -v
```

`offline-protocol-demo` 使用两个带 ALFWorld/WebShop 动作语法的确定性 fixture，各保存一条失败轨迹，再恢复至第 2 个动作之前。只执行第 1 个动作，不调用模型。**不是实际 benchmark 实验**；fixture 的 exact 不代表真实环境支持 exact。重复运行追加尝试，恢复报告按轨迹 ID 分开保存。

输出目录包含：

| 文件 | 内容 |
| --- | --- |
| `trajectories.jsonl` | 两种 fixture 的原始尝试；明确标记非 benchmark |
| `<trajectory-id>.restore.json` | 恢复检查、当前观察、历史、剩余预算和证据等级 |

## 从 ExpeL 格式导入

```powershell
python -m cave import-expel --input examples/expel-trials.jsonl --output output/imported.jsonl
```

导入输出必须是新文件。importer 保留旧轨迹的 reflection 作为来源数据，但 M1 actor 和恢复历史不读取、不注入该字段。旧导出缺失的成本和状态字段保持未知，不能补造完整状态证据。

JSONL 每行是一条 trial，所需字段见 `examples/expel-trials.jsonl`：`id, benchmark, task_id, task, run_id, outcome, environment, trajectory`。建议同时提供 `actor, constraints, reflections, initial_observation, seed`。`outcome` 必须明确提供；支持 success/failure/truncated/unknown，不通过“完成了”等文本猜测。原始 task 的唯一后缀应放在 task_id，不因显示文本相同而合并。

ALFWorld 用 `> think:`、`> action`；WebShop 用 `Action:`、`Observation:`。WebShop 的 `think[]` 仍保留在动作序列中。只导出 trial 部分，不要混入 system prompt/few-shot/整份 true_log。旧 pickle 不直接作为输入；将 `failed_trial_history`、`succeeded_trial_history` 的每次尝试转换成显式逐行导出。

## 真实环境采集

可选依赖使用独立虚拟环境安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[webshop]'
# ALFWorld 建议在已有可运行的 ALFWorld 环境中安装：
# python -m pip install -e '.[alfworld]'
```

先按实际数据和服务修改 `configs/*.example.json` 中的占位值。`llm.example.json` 的 endpoint 为 OpenAI-compatible `/v1` 根路径；请求使用 `/chat/completions`，需要 JSON 输出和 `usage.total_tokens`。密钥从配置指向的环境变量读取，不写进轨迹。实验时固定 model revision、temperature 和 actor prompt。

ALFWorld 使用官方环境 API，需安装依赖、下载数据、提供真实 gamefile；`resource_root` 是 YAML 中 `data/` 路径的根目录。WebShop 需另行启动真实服务，提供相同任务 session 和固定产品/任务数据版本。不会自动下载数据、启动商店或调用模型。

```powershell
python -m cave collect --task configs/alfworld.example.json --llm-config configs/llm.example.json --run-id aw-source-001 --attempts 4 --output output/aw-traces.jsonl
python -m cave collect --task configs/webshop.example.json --llm-config configs/llm.example.json --run-id ws-source-001 --attempts 4 --output output/ws-traces.jsonl
```

采集参考 ExpeL 的 action/observation 交替、按尝试归档和同题 reset。加 `--trace` 可在终端逐步输出 Thought/Action/Observation，不改变保存格式。每次尝试相互独立，不生成 reflection；按 `--attempts` 执行指定次数，成功也如实保存。每次尝试使用 `seed + attempt`，动作调用使用该次 reset seed 加动作序号；服务是否遵守 seed 由服务实现决定。输出逐次追加，重复执行同一 run 时 attempt 接续，轨迹 ID 独立生成。

保存任务/约束、环境固定信息、actor 非密钥配置、初始与逐步观察/可见状态、token 成本、终局原因。异常保留部分轨迹，`outcome=error`，collect 返回非零退出码。ALFWorld 成功来自 `info['won']`，WebShop 来自最终 score==1；部分得分不算成功。ALFWorld 的底层原始观察和 WebShop 的 HTML 单独保存在 `raw_observation`。

## 恢复到人工指定的 t 前

```powershell
python -m cave restore --trajectories output/aw-traces.jsonl --trajectory-id ACTUAL_ID --t 2 --output output/aw-restore-t2.json
```

`t` 从 **1** 编号。t=1 只 reset，t=2 只重放第 1 个动作。恢复不需要模型配置，不执行第 t 个动作或失败后缀。检查初始和前缀观察、可见事实、终局状态及可用的状态 token；任何漂移会保存失败报告并返回非零退出码。内置真实适配器均为 observational，不把观察相同视为隐藏状态精确一致。

CLI 验收后会关闭环境，输出 JSON 是恢复报告，不是可跨进程加载的 checkpoint。后续需在同一进程使用 `cave.restore.restore_before` 返回的 live environment；接口示例、预算语义及服务器步骤见 [M1 说明](docs/collection-acceptance.md)。

## 早期原型：提取和验证（不属于本轮验收）

以下通用命令和成对回放是早期原型，保留作兼容参考。当前 ALFWorld 流程使用原始失败记录作为 baseline，恢复后只跑修正分支；新的 M2/M3/M4 入口和输出见对应任务记录。

```powershell
python -m cave propose --trajectories output/aw-traces.jsonl --llm-config configs/llm.example.json --output output/aw-candidates.jsonl
python -m cave build --trajectories output/aw-traces.jsonl --candidates output/aw-candidates.jsonl --output-dir output/aw-bank --replay --llm-config configs/llm.example.json --verification-config configs/verification.example.json
```

WebShop 使用同样命令替换输入文件。可通过 `prompts` 只导出提取请求，也可直接提供离线候选 JSONL。每个失败最多 3 个候选，成功对照必须来自兼容任务/约束/环境。没有成功对照时仍可形成待验证假设。

每对回放保持源 prefix、源 reset seed、固定 actor、continuation seed 和预算一致，仅替换被引用的单个动作。保存逐对 outcome、首步失败签名、oracle check、原始成本、归一化成本、恢复 token 和 delta。source reward 为 `success - lambda_inference * inference_cost - lambda_tool * tool_cost`；归一化成本截在 [0,1]，原始 tokens/tool calls 另行保留。prefix 重建成本单独记录，不计入修正的增量 utility。

当前内置环境只提供 **observational reset-prefix replay**，缺少完整状态恢复认证。初始 ALFWorld source memory 可在 K=2 分支均成功、P/N 通过且恢复 ready 时标记为 source-scoped verified；这不等同于 exact 或跨任务认证。可以通过 `--environment-factory package.module:callable` 接入履行 `cave/runtime.py` 的 exact restoration backend；其 state token 必须覆盖完整环境状态。普通 observation 相等不能升级成 exact。

只有全部 K 对有效、源 P/N 确认、baseline 复现失败、恢复 exact 且净收益差置信下界大于门槛时，才进入 eligible。任意恢复/调用错误都会保留，并阻止认证；不会筛掉坏 pair 后只报告成功样本。当前收益区间为固定 K、有界成本假设下的 Hoeffding 区间，只描述该 source checkpoint 的 seed 分布。

## 当前边界

当前 ALFWorld 交付：无 reflection 采集、异常与成本记录、不覆盖的尝试归档、reset-prefix 恢复、候选提取、K=2 修正分支和记忆库导出。WebShop 真实验收、target 检索、a0 缓存、exposure/gate、RL、Inspect 不属于当前范围。

当前边界：ALFWorld M1-M4 的服务器流程和一条带实际修正证据的记忆已完成；WebShop 真实验收、target 检索、a0 缓存、exposure/gate、RL、Inspect 不属于当前范围。

## 服务器上的十条 ALFWorld 历史采集测试

在 `/home/ljj/CAVE` 运行 `bash scripts/run_alfworld_history_sample.sh`。固定的 10 个官方 `train` 游戏见 `configs/alfworld-history-sample-seed42/manifest.json`；每个最多 20 步，actor 为 `gpt-6-sol`，终端实时输出 Thought/Action/Observation。此命令尚未代表正式四分实验数据划分；说明见 [数据角色](docs/data-splits.md)。
