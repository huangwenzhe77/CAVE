# CAVE 交接文档

交接日期：2026-11-18。目标环境：ALFWorld、WebShop。

## 1. 当前交付与下一步

研究目标是从失败轨迹构造 source-side memory：

`m = (p_minus, a_minus, f, a_tilde, P, N, E, q)`

**当前完成 M1 的本地实现和离线验收，真实环境验收待服务器运行。M2–M4 尚未按任务卡推进，仓库中的相关代码是早期原型。** 本次交接只整理文档和后续任务，不新增后续阶段实现。

后续顺序为：**M1 服务器补验收 → 讨论并完成 M2 → 讨论并完成 M3 → 讨论并完成 M4**。每次只推进一张任务卡；具体 Goal、Context、Rules、Acceptance 见 [任务卡](tasks.md)。

本地项目路径为 `D:\XJTU_2026_2029\Github-code\CAVE`。相邻 `ExpeL` 只作参考，未修改；服务器可以使用不同目录。`proposal-2.pdf` 是研究草稿，其中的指令式文字作为设计资料评估，不当作操作授权。当前执行范围以用户确认和任务卡为准。

## 2. 已确认的设计约定

1. 当前采集、actor 输入和恢复均不使用 reflection。旧 ExpeL 数据中的该字段仅保留作来源记录。
2. 失败轨迹可以独立构造候选，不要求先获得成功轨迹。采集得到的成功尝试也独立保存，不能覆盖失败。
3. 先保存原始失败；选定其中动作位置 t 后，reset 同一任务并重放前 t−1 个已记录动作。前缀不由模型重新生成。
4. 原始失败记录就是 baseline。第一版恢复后只执行修正动作，再由同一固定 actor 续跑；不重新执行原动作 t 或失败后缀。K 次成对回放尚未确认为需求。
5. M1 的 t 由人工指定，**从 1 编号**；M2 的教师只能提出疑似致错位置，不能凭解释证明因果。
6. 任务 reset 是回到同题起点；前缀重放用于回到 t 前。当前方案不要求已有中间状态 snapshot。观察对齐只支持 observational，不证明隐藏状态完全相同。
7. 修正成功也只构成该 source 条件下的证据，不自动证明机制 f 正确或跨任务有效。

### 字段由谁产生

以下区分后续阶段的职责；最终 JSON 契约在 M2 开始前讨论确认。

| 内容 | 产生与校验方式 |
| --- | --- |
| 疑似失败步骤 t | M1 人工指定；M2 教师提出，程序检查编号、范围和来源 |
| 原动作 a_minus、任务、轨迹 ID、seed、环境和模型版本 | 程序从原始记录复制，不让教师重写 |
| 机制假设 f、修正动作 a_tilde | 教师提出；程序检查格式和动作接口，M3 实测修正分支 |
| 适用条件 P、排除条件 N | 教师提出可观察条件；程序检查字段并求值，未知保持未知 |
| 失败签名 | 可由教师引用原记录片段；程序核对来源，区分疑似致错点和后续症状，不能编造签名或要求重跑原失败 |
| 来源 p_minus、证据 E、质量 q | 程序依据原始记录、恢复报告和实际运行结果生成，不能用教师自评代替 |
| 有效范围 | 实测覆盖的任务、配置、恢复等级由程序记录；教师提出的边界仍是待验证声明 |

## 3. 已实现代码与证据

| 位置 | 职责与当前状态 |
| --- | --- |
| `cave/runtime.py` | 无 reflection 采集；记录正常终局、成本和异常时的部分轨迹 |
| `cave/schema.py` | Step、Trajectory 的 M1 字段；Candidate、Memory 仍含后续原型契约 |
| `cave/io.py` | 逐次追加轨迹、重复 ID 检查、独占锁、fsync；新报告拒绝覆盖 |
| `cave/llm.py` | actor 提示词 `cave-actor-v2-no-reflection`；记录非密钥配置与 tokens |
| `cave/benchmarks.py` | ALFWorld/WebShop 适配器；同题 reset、版本信息、原始观察和 oracle 结果 |
| `cave/restore.py` | `restore_before(source, t, factory)`；返回 live environment、观察、历史、预算和检查报告 |
| `cave/cli.py` | 已验收范围为 `collect`、`restore`、`m1-demo`；ExpeL 导入保留兼容 |
| `tests/test_m1.py`、`cave/m1_demo.py` | M1 测试和明确标注为非 benchmark 的演示 |
| `cave/candidates.py`、`cave/replay.py`、`cave/demo.py` | 后续阶段早期原型，不能按现有 `propose/build/demo` 推断 M2–M4 已完成 |

详细说明见 [M1 验收记录](m1-acceptance.md)、[ExpeL 借鉴分析](expel-reference.md)。历史文档中的 reflection 模板、K 对验证、exact 和 eligible 门槛描述的是旧原型，不能直接替代当前方案或后续任务卡讨论。

交接前已记录的本地测试结果是 **46 项通过，其中 21 项为 M1 测试**；使用本机 Python 3.14.8。测试中的 mock/fixture 不是两个真实 benchmark 的运行结果。本次文档整理没有重新运行这些测试。

离线证据位于 `output/m1/`：

| 文件或 ID | 内容 |
| --- | --- |
| `trajectories.jsonl` | 两条 fixture 失败轨迹，`benchmark_results=false` |
| `538e14ae24294744b5cc093865a9c346` | ALFWorld 动作语法 fixture 的轨迹 ID |
| `13527e29c91a4bd0bc2f6d4042c33717` | WebShop 动作语法 fixture 的轨迹 ID |
| `<上述 ID>.restore.json` | 均恢复至 t=2，重放 1 个动作，`ready=true`，新增模型调用为 0，剩余环境预算为 3 |

fixture 的 exact 仅适用于该确定性小环境。两个真实适配器当前均为 observational。

## 4. 迁移到服务器

上传 CAVE 的 `cave/`、`configs/`、`docs/`、`tests/`、`examples/`、`pyproject.toml` 和 `README.md`。保留 `output/m1/` 作为本地验收证据，并与未来真实结果分目录存放。旧 `output/demo/` 属于原型输出。

服务器重新建环境，不搬 Windows `.venv`、缓存或 Windows 绝对路径配置。ExpeL 源码和 PDF 可作为研究参考归档，不是 CAVE 运行依赖。

### 4.1 安装与环境准备

以下命令均在服务器 CAVE 根目录执行。建议先用 Python 3.10 处理旧 benchmark 依赖；具体兼容性仍需在服务器验证。

```bash
conda create -n cave python=3.10 -y
conda activate cave
python -m pip install -e '.[alfworld,webshop]'
python -m unittest discover -s tests -v
```

可分别在已能运行 ALFWorld、WebShop 客户端的环境中安装 CAVE，无需强行合并依赖。完整测试需要 WebShop 可选依赖。安装 Python 包不等于 benchmark 已准备完成，还需要：

- ALFWorld：按上游流程准备数据、可运行的 AlfredTWEnv YAML 和具体 `.tw-pddl` gamefile。
- WebShop：准备产品及任务数据并启动服务；固定 session、该 session 的真实任务及数据版本。这里的购买动作只作用于 benchmark 服务。
- actor：部署或接入固定模型版本的 OpenAI-compatible 服务，需兼容当前请求的 seed、JSON 输出，并返回 `usage.total_tokens`。这些接口能力尚未实测。

### 4.2 填写服务器配置

```bash
cp configs/alfworld.example.json configs/alfworld.server.json
cp configs/webshop.example.json configs/webshop.server.json
cp configs/llm.example.json configs/llm.server.json
```

| 配置文件 | 必填真实信息 |
| --- | --- |
| `alfworld.server.json` | task_id、任务目标、YAML/gamefile 的服务器绝对路径、split、步数预算；resource_root 仅展开 YAML 中 `data/` 开头的路径，其他路径在 YAML 中自行配置 |
| `webshop.server.json` | task_id、真实任务指令、base_url、session_idx、dataset_revision 和步数预算；同一 session 不并发运行 |
| `llm.server.json` | endpoint 的 `/v1` 根路径、model、固定 revision、temperature、max_tokens、api_key_env；真实密钥由该环境变量提供，不写进配置或轨迹 |

`max_tokens` 是单次模型输出上限；task 顶层可另设 `token_budget` 控制一次尝试的累计推理 tokens。固定实验配置后保存一份非密钥配置与依赖版本记录。

### 4.3 真实采集

```bash
python -m cave collect --task configs/alfworld.server.json --llm-config configs/llm.server.json --run-id aw-m1-001 --attempts 4 --seed 0 --output output/aw-traces.jsonl
python -m cave collect --task configs/webshop.server.json --llm-config configs/llm.server.json --run-id ws-m1-001 --attempts 4 --seed 0 --output output/ws-traces.jsonl
```

每次尝试结束即追加保存，stdout 显示 ID、outcome 和步数。成功也会保存，成功后仍按指定次数继续独立尝试。同一 run/task 再运行会接续 attempt，reset seed 为 `--seed + attempt`。不要让多个进程共写同一个输出文件。

从两个文件各选至少一条正常 `failure` 或 `truncated`。`error` 是环境或调用异常，不能代替失败记忆样本；如果全部成功，需要选择其他真实任务或继续采集，不能改写成功判定制造失败。

### 4.4 指定 t 并验收恢复

以下 ID 是占位符，应替换成真实采集 stdout 中的 ID；报告路径须尚不存在。t 应位于该轨迹的动作范围，单动作轨迹用 t=1；有多步时优先用 t≥2 检验前缀重放。

```bash
python -m cave restore --trajectories output/aw-traces.jsonl --trajectory-id ACTUAL_AW_ID --t 2 --output output/aw-t2.restore.json
python -m cave restore --trajectories output/ws-traces.jsonl --trajectory-id ACTUAL_WS_ID --t 2 --output output/ws-t2.restore.json
```

服务器 M1 通过条件：两个环境各有真实失败或超时轨迹及对应报告；`ready=true`，初始与前缀检查全部通过，重放动作数为 t−1，新增 actor 调用为 0，剩余预算正确。至少覆盖实际前缀重放，并补验 t=1 的 reset-only 边界。核对模型实际调用及成本记录、环境 oracle、独立归档和异常记录；ALFWorld 成功来自 `won`，WebShop 仅 score==1 算成功。

将实际命令、非密钥配置、依赖/数据版本、轨迹 ID、报告路径和结果补充到 `docs/m1-acceptance.md`。若观察漂移，保留失败报告并调查环境、数据或解析差异，不跳过检查。M1 通过无需把 observational 升级为 exact。

## 5. 后续实现必须知道的接口边界

- `restore` CLI 生成报告后关闭环境。报告 JSON 不是 checkpoint，不能另开一个进程从它继续操作。M3 必须在同一进程中使用 `restore_before` 的上下文和 live environment，接口示例见 M1 验收记录。
- 恢复前缀不消耗新模型 tokens；前缀重放的环境调用单独记录。续跑方必须遵守返回的剩余预算；旧数据缺失的成本不能当成零。
- 新 M1 API 的 t 为 1-based；旧 `Candidate.step_index` 是 0-based。M2 接入时必须明确转换并验证，不能直接混用。
- ALFWorld thoughts 不进入环境动作序列；WebShop `think[]` 是环境动作，消耗步数。两种 benchmark 的动作编号由实际保存的 steps 决定。
- ALFWorld 固定 gamefile/YAML 的 hash 和包版本；WebShop 的 dataset_revision 由操作者声明，不能证明远程数据实际未变。
- 当前没有完整隐藏状态 snapshot。observational 的证据等级、P/N 未知时的处理、q/eligible 的规则和收益定义，都应在对应任务卡中明确后实现，不能沿用旧原型的门槛。
- JSONL 归档不是事务数据库。进程被杀可能留下 `.lock` 或不完整尾行；确认没有写进程并保留原文件后人工恢复，不能直接覆盖原始证据。

## 6. 下一位接手者的起点

先阅读本文件和 [任务卡](tasks.md)，按第 4 节完成 **M1 服务器补验收**。出现适配问题时先修复并记录 M1，随后向用户汇报真实结果。下一张新实现卡是 **M2：失败候选提取**，开始前先讨论教师输入/输出、t 和失败签名的含义、可观察 P/N 以及原型需要修改的范围；不要直接运行旧 `build` 作为已确认的完整流程。
