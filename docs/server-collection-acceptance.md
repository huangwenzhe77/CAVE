# M1 服务器验收记录（进行中）

日期：2026-11-18。服务器工作目录：`/home/ljj/CAVE`。基线为 `main@0b1bbc0`，本记录只覆盖 M1。

## 代码与环境

- 上游 ALFWorld 0.5.0，源码提交 `aaba6870f86c5be6a08a491f32a50b906227bc3e`；TextWorld 1.7.0，Python 3.11.6，PyYAML 6.0.3。
- 使用已有 `/home/ljj/.venvs/cave-alfworld` 环境；安装 `cave-mem` editable 和 WebShop 测试所需 `beautifulsoup4` 4.15.0、`requests` 2.34.2。
- 真实 ALFWorld reset 首次发现上游类需通过 `alfworld.agents.environment.get_environment()` 获取；已在 `cave/benchmarks.py` 修复。
- `~/.venvs/cave-alfworld/bin/python -m unittest discover -s tests -q`：48 项全部通过（含实时轨迹输出测试）。此前系统 Python 缺 `beautifulsoup4` 导致 1 项错误，专用环境补依赖后消除。

## ALFWorld 真实轨迹

- 配置：`configs/alfworld.server.yaml`、`configs/alfworld.server.json`、`configs/llm.server.json`。数据为 `json_2.1.1/valid_seen` 的 bowl/coffeetable gamefile；配置 SHA256 `06051b25e0f5dc09a210a903c627c24a3777919f9a8d99ecd381d59d8c6e0495`，gamefile SHA256 `e1330f34eb5fa1615d8e0814a867b0b3b87e4e2f829903928e7fe4b94d2d4e39`。
- Actor：OpenAI 兼容服务的 `gpt-5.6-terra`，temperature 0，最多 256 输出 token；服务返回 `usage.total_tokens`。`model_revision` 字段是当日服务配置标签，服务未提供不可变权重 revision，不能据此宣称权重已固定。密钥从 `/home/ljj/.config/cave-mem/teacher.env` 注入进程，不写入仓库或轨迹。
- 运行：`python -m cave collect --task configs/alfworld.server.json --llm-config configs/llm.server.json --run-id alfworld-connectivity-validseen --attempts 1 --seed 0 --output output/alfworld-connectivity-validseen.jsonl`，在同一进程环境内注入密钥。
- 轨迹 ID：`55693ecf6f1645bdaf03d43ecc4692ff`。实际结果 `truncated`，8 个步骤、8 次模型调用、21,629 个报告 token、8 次环境 step、约 41.9 秒；终局 `score=0`、`reason=step_budget_exhausted`，无 error，成本报告完整。
- 原始轨迹：`output/alfworld-connectivity-validseen.jsonl`。其中保留了原始观察、逐步动作/观察、可见事实、环境配置及 hash、actor 配置、seed、终局和成本。

## 指定动作前恢复

对同一轨迹分别运行：

```bash
~/.venvs/cave-alfworld/bin/python -m cave restore --trajectories output/alfworld-connectivity-validseen.jsonl --trajectory-id 55693ecf6f1645bdaf03d43ecc4692ff --t 1 --output output/alfworld-connectivity-validseen-t1.restore.json
~/.venvs/cave-alfworld/bin/python -m cave restore --trajectories output/alfworld-connectivity-validseen.jsonl --trajectory-id 55693ecf6f1645bdaf03d43ecc4692ff --t 2 --output output/alfworld-connectivity-validseen-t2.restore.json
```

| t | ready | 匹配检查 | 重放动作 | 新 actor 调用/token | 剩余环境步数 | 等级 |
| --- | --- | --- | ---: | --- | ---: | --- |
| 1 | true | 5/5 | 0 | 0 / 0 | 8 | observational |
| 2 | true | 9/9 | 1 | 0 / 0 | 7 | observational |

报告：`output/alfworld-connectivity-validseen-t1.restore.json`、`output/alfworld-connectivity-validseen-t2.restore.json`。两次均无 error；观察与可见事实对齐，仅证明可观察起点相同，不证明隐藏状态完全相同。未配置推理 token 总预算，因此剩余推理预算为 null。

## 终端逐步输出

`collect --trace` 在每一步完成后立即打印 `Thought`、`Action`、`Observation` 和推理 token，原始 JSONL 仍独立持久化。显示层不改变 actor、环境动作或恢复协议。

## 尚未验收

当前服务器未发现 WebShop 源码/数据目录或本地 3000 端口服务，尚无真实 WebShop 失败轨迹与恢复报告。用户本轮明确只要求先跑通 ALFWorld，因此本轮范围已完成；WebShop 留待后续验收。不得用 fixture 替代 WebShop 真实结果。M2 未开始。

## 十任务采集试跑与修正

默认配置已改为 `max_steps=20` 与 `gpt-6-sol`。早先 8 步、`gpt-5.6-terra` 的配置备份在 `configs/history/`；已保存的轨迹与恢复报告保持原样。

第一次试跑 `alfworld-history-sample-seed42-human-r01` 在第 5 个任务的 actor 请求期间被手动中断。前 4 个任务已写入 `output/alfworld-history-sample-seed42-human-r01.jsonl`：1 个 success（15 步），2 个因连续重复动作而 truncated（5、9 步），1 个用满 20 步 truncated。第 5 个任务只在终端输出了第 1 步，旧代码没有把中断时的部分轨迹写入 JSONL。这是 4/10 的试跑，不能算十任务完成。第 1 个任务 t=1、t=2 的前缀恢复均为 ready=true 且没有新 actor 调用，报告在 `output/alfworld-history-sample-seed42-human-r01-task1-t{1,2}.restore.json`。

试跑发现任务配置使用 `traj_data.json` 的人工标注文字，部分与游戏 `grammar` 的实际目标不同，例如第 5 个任务配置写“盛水的碗”，环境要求“洗净碗”。该批只能用于连通性与采集流程检查，不纳入正式记忆构造。现在保留 r01 原配置，另在 `configs/alfworld-history-sample-seed42/` 固定相同 10 个 gamefile，并以游戏文件中的目标文字生成任务。`scripts/run_alfworld_history_sample.sh` 已切换到该配置，默认写入新文件 `output/alfworld-history-sample-seed42-r02.jsonl`。该十任务运行尚未执行。

中断处理已改为在 Ctrl+C 时保存带 `outcome=error` 的部分轨迹、已完成步骤及不完整 token 用量标记；这项修正只对之后的运行生效。四类数据角色见 [数据划分说明](data-splits.md)。
