# M1 验收与服务器运行说明

日期：2026-11-18。范围：只完成 M1 的实现与本地验证；真实环境按用户安排延后到服务器配置后运行。未进入 M2，不调用教师选择 t，不执行修正分支。

## 验收状态

| 项目 | 本轮结果 |
| --- | --- |
| 无 reflection 采集、按尝试归档、追加不覆盖 | 通过本地测试 |
| 任务/约束、配置、seed、观察/动作、成本、终局与异常记录 | 通过本地测试 |
| 只重放 t 前动作，返回可用环境、历史和剩余预算 | 通过本地测试及两个 fixture 演示 |
| 初始/前缀漂移、提前终止、预算耗尽、恢复等级 | 通过本地测试 |
| 原有 ExpeL 文本导入和其他原型回归 | 测试通过；不代表推进后续任务卡 |
| 真实 ALFWorld 失败/超时采集及恢复 | 未验收，待服务器安装运行时和数据 |
| 真实 WebShop 失败/超时采集及恢复 | 未验收，待服务器启动服务和配置数据版本 |
| 真实 actor 模型调用 | 未验收，待服务器模型配置 |

## 本地复现

在 CAVE 目录运行，基础代码要求 Python 3.10+；本机验证使用 `.venv` 的 Python 3.14.8。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m cave m1-demo --output-dir output/m1
```

测试共 46 项，其中 21 项为 M1 测试。M1 测试覆盖真实适配器的接口规则，但使用 mock 或 fixture，不代表真实 benchmark 已运行。

`output/m1/trajectories.jsonl` 保存两个明确标记 `benchmark_results=false` 的 fixture 失败尝试。每条 `<id>.restore.json` 都恢复到 t=2，仅重放一个动作，`ready=true`、新模型调用为 0、剩余环境动作预算为 3。fixture 的完整状态 token 可以支持 exact；真实 ALFWorld/WebShop 当前均为 observational。

重复运行 demo 会追加两条新轨迹，并创建新的报告，不覆盖此前结果。样例采用脚本 actor，推理 tokens 为真实的 0，不以假造模型成本代替。

## 记录与恢复契约

采集入口为 `collect_attempt`；CLI 为 `collect`。

- 每次尝试独立 UUID、UTC 时间、run_id、attempt 和 reset seed。CLI 重复同一 run/task 时接续 attempt，reset seed 为 `--seed + attempt`。
- 保存环境固定信息、actor 非密钥配置、任务及约束、初始观察/事实/终局信息、每步动作/thoughts/观察/事实/终局信息、每步 tokens 和 actor seed。
- ALFWorld 保留原始底层观察，WebShop 保留 HTML；格式化后的 actor 观察单独保存。环境计步及 last_action 也属于可见包装器状态。
- success/failure/truncated/error 分开记录。reset、模型、环境 step、state token 或 close 异常时保留已有步骤及错误阶段；环境 step 报错会注明该次调用后的状态不确定。
- 已知的模型输出错误仍计入服务报告的 tokens；若服务失败或不提供 usage，则标记成本不完整，不能把已记录的 0 当作真实零成本。错误不是可用于失败记忆的正常环境失败。
- 可选 task 顶层 `token_budget` 限制累计推理 tokens；超过时保存未执行提案并停止。一次模型调用可能超过剩余预算，实耗如实记录，不截成预算值。默认不限制 tokens。
- 采集 JSONL 逐次追加并 fsync，ID 重复、已有坏行或文件锁冲突会报错。非正常杀进程可能留下 `.lock` 或部分尾行，须确认无写进程并保留原件后人工处理；本实现不是事务数据库，也不支持多个采集进程共写同一文件。

恢复入口为 `restore_before(source, t, factory)`，t 从 **1** 编号，t=1 只 reset。M1 人工给 t，尚不评价该步骤是否致错。已有失败记录是 baseline；恢复检查只覆盖 t 前，绝不重新执行 t 和失败后缀。

```python
from cave.benchmarks import create_environment
from cave.io import load_trajectories
from cave.restore import restore_before

source = load_trajectories("output/aw-traces.jsonl")["ACTUAL_TRAJECTORY_ID"]
with restore_before(source, t=2, factory=create_environment) as point:
    if not point.report["ready"]:
        raise RuntimeError(point.report["errors"])
    current_observation = point.observation
    recorded_prefix = point.history
    remaining_budget = point.report["remaining_budget"]
    # point.environment stays live within this context.
```

返回环境处于 t 执行前；恢复新产生的模型调用/tokens 均为 0。剩余环境预算是源总预算减前缀动作数，剩余推理预算扣除源前缀已有用量；前缀重放工具调用单独记录。未配置的 tokens 上限及旧导入中缺失的成本保持 null，不推断。后续续跑调用者须遵守返回的剩余预算。

report 的 `ready` 只表示达到可用的观察对齐起点，不证明隐藏状态完全一致。只有后端声明完整状态契约，且源记录和当前完整状态 token 相等时才标 exact。内置两个真实环境无此能力。WebShop 的 dataset_revision 是操作者声明，不能自动证明远程数据未变；原始 HTML 变化也会导致严格对齐检查失败。

CLI `restore` 保存报告并关闭环境，报告不能当作跨进程 checkpoint。恢复失败也保存报告，退出码 1；非法参数或输出已存在直接报错。后续修正流程需在同一进程使用上面的 live API。

## 服务器运行

可把 CAVE 源码和配置模板放到 Linux 服务器，不需要上传本机 `.venv` 或 Windows 路径。建议为旧 benchmark 依赖创建独立 Python 3.10 环境；具体版本兼容性仍须在服务器验证。

```bash
conda create -n cave python=3.10 -y
conda activate cave
python -m pip install -e '.[alfworld,webshop]'
python -m unittest discover -s tests -v
```

安装依赖后，按 ALFWorld 官方流程下载数据，准备可运行的 AlfredTWEnv YAML 和具体 `.tw-pddl` gamefile；按 WebShop 官方流程准备产品/任务数据并启动本地服务。CAVE 不负责安装或启动这两个上游项目。可以分别使用它们已有的运行环境安装 CAVE；模型服务也可独立部署。

准备三个实际配置文件：

| 文件 | 需要填写 |
| --- | --- |
| `configs/alfworld.server.json` | 复制 example 后填写真实 task_id/goal、YAML 绝对路径、gamefile、split 和资源根目录；resource_root 仅解析 YAML 内 `data/` 开头的路径，其他数据路径应在 YAML 中写成绝对路径 |
| `configs/webshop.server.json` | 复制 example 后填写真实 session、该 session 的任务描述、base_url、产品及任务数据版本；同 session 不并行运行 |
| `configs/llm.server.json` | 复制 llm.example 后填写 endpoint/model/revision、temperature、max_tokens、api_key_env；密钥只放环境变量，服务需支持请求中的 seed 字段、JSON 输出及 usage.total_tokens |

然后分别运行真实采集。示例的步数上限来自各 task 配置；环境错误或模型错误不能代替所需的正常失败/超时样本。

```bash
python -m cave collect --task configs/alfworld.server.json --llm-config configs/llm.server.json --run-id aw-m1-001 --attempts 4 --seed 0 --output output/aw-traces.jsonl
python -m cave collect --task configs/webshop.server.json --llm-config configs/llm.server.json --run-id ws-m1-001 --attempts 4 --seed 0 --output output/ws-traces.jsonl
```

采集 stdout 输出每条 ID/outcome/steps。从各文件选择一条 `failure` 或 `truncated`，人工指定其范围内的 t，再恢复；`--output` 必须为新报告路径。

```bash
python -m cave restore --trajectories output/aw-traces.jsonl --trajectory-id ACTUAL_AW_ID --t 2 --output output/aw-t2.restore.json
python -m cave restore --trajectories output/ws-traces.jsonl --trajectory-id ACTUAL_WS_ID --t 2 --output output/ws-t2.restore.json
```

若所选轨迹只有一个动作，应选 t=1。服务器真实验收需检查：每个环境各有真实失败或超时轨迹，报告 `ready=true`、前缀检查全部通过、恢复动作数为 t−1、新 actor 调用为 0、预算正确；保存实际配置和依赖版本。结果按 observational 如实报告；出现漂移需调查原因，不能跳过检查或改成 exact。
