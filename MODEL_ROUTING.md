# Codex CLI 模型路由

本项目用一个只读路由器把模型判断分配给 GPT-5.6 Luna、Terra 和 Sol。它不替代
`notebook.py`，不直接写 `data/math_notebook.db`，也不改变判题、验证和推荐质量门。

## 模型分工

| 任务 | 默认路线 | 自动升级 |
|---|---|---|
| 标签、推荐复核 | Luna / low | 含图升 Terra；歧义升 Sol |
| 高质量题简化验证 | Luna / medium | 冲突、缺项、证明、复杂图形升 Sol |
| 文字/照片判题、复习判定、苏格拉底引导 | Terra / medium | 证据不清或复杂推导升 Sol |
| 完整验证、题目修复、生成题 | Sol / high | 不再循环升级 |
| 争议题最终裁决 | Sol / xhigh | 无法确定则保持未验证 |

路由结果的 `status` 不是 `complete`，或模型/逐项置信度低于任务阈值时，路由器最多
自动升级一次到 Sol。Sol 仍不能确定时，命令以退出码 `3` 停止，不产生可提交载荷。

## 安装 CLI profiles

```powershell
python -X utf8 -B <skill-dir>\scripts\codex_task_router.py install-profiles --json
```

该命令创建：

- `%USERPROFILE%\.codex\math-fast.config.toml`：`gpt-5.6-luna / low`
- `%USERPROFILE%\.codex\math-standard.config.toml`：`gpt-5.6-terra / medium`
- `%USERPROFILE%\.codex\math-expert.config.toml`：`gpt-5.6-sol / high`

已有同名但内容不同的文件不会被覆盖；确认后才可使用 `--force`。

## 查看路线

```powershell
python -X utf8 -B <skill-dir>\scripts\codex_task_router.py route `
  --task verify-simplified --json

python -X utf8 -B <skill-dir>\scripts\codex_task_router.py route `
  --task grade-photo --has-image --risk ambiguous_visual --json
```

可用任务：`tag`、`recommend`、`verify-simplified`、`grade-text`、`grade-photo`、
`review`、`tutor`、`verify-full`、`repair`、`adjudicate`、`generate`。

风险标记：`visual`、`ambiguous_visual`、`answer_conflict`、`incomplete`、`proof`、
`complex_diagram`、`multiple_cases`、`generated`。

## 执行任务

简化验证：

```powershell
python -X utf8 -B <skill-dir>\scripts\codex_task_router.py run `
  --task verify-simplified `
  --input data\audits\<批次>\manifest.json `
  --out data\audits\<批次>\codex-decisions.json `
  --json
```

输出可直接交给：

```powershell
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py `
  prepare-review-batch data\audits\<批次>\codex-decisions.json `
  --out-dir data\audits\<批次>\reviews --json
```

照片判题必须先运行 `photo-preflight`，然后把每个 `preview_path` 分别作为 `--image`：

```powershell
python -X utf8 -B <skill-dir>\scripts\codex_task_router.py run `
  --task grade-photo `
  --input data\grade-inputs\evidence.json `
  --image data\grade-inputs\preview-1.jpg `
  --out data\grade-inputs\grade-result.json `
  --json
```

错误或部分正确时会额外生成 `grade-result.analysis.json`，它可以直接交给
`grade-preview`，确认后再执行 `grade-commit`。正确或证据不清时不会生成错题分析文件。

推荐复核输出中的 `items` 可由现有 `assign-recommendations` 质量门复核并保存。生成题始终
带 `verification_status=unverified`，不得直接推荐或标记为已验证。

## 安全和审计

- 每次调用先本地运行 `doctor` 和对应的 `agent-context`。
- 输入 JSON 由本地程序压缩后通过 stdin 发送，模型不再启动 shell 读取文件。
- Codex 固定使用 `--ephemeral --sandbox read-only --output-schema`。
- 模型正文不写入路由审计；审计只记录任务、模型、推理强度、耗时、状态和置信度。
- 审计位于 `data/audits/codex-cli-routing/`，并明确记录 `database_modified=false`。
- 最终写库只能通过现有 `grade-commit`、`verify-review-batch`、
  `assign-recommendations` 或 `annotate`。

## 配置与测试

- 路由配置：`<skill-dir>/assets/codex-model-routing.json`
- 输出 Schema：`<skill-dir>/assets/codex-schemas/`
- 路由器：`<skill-dir>/scripts/codex_task_router.py`
- 回归测试：`tests/test_codex_task_router.py`

```powershell
python -X utf8 -B -m unittest tests.test_codex_task_router -v
```
