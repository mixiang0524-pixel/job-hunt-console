# Job Hunt Console · 求职投递台

一个本地优先、可迁移的求职 Skill：将岗位信息整理成静态投递台，围绕可追溯的个人经历匹配岗位要求，并为选定岗位准备简历草稿。

**投递由使用者自己完成。** 页面不代投、不后台联网、不定时运行；投递状态只由使用者操作。模型读取任务包并输出判读结果，Python 脚本负责验证引用、去重、缓存和计分，不直接调用模型 API。

公开仓库只包含通用实现、空白模板和明确虚构的样例，没有真实求职者资料、招聘数据集、抓取日志或历史投递状态。

## 能做什么

- 按职责和要求切分 JD，生成逐块判读任务，保留拒绝理由与不确定项。
- 对相同公司、岗位和城市进行保守去重，疑似重复仅标记。
- 从显式选定的访谈材料建立经历档案，区分事实、能力评定和责任边界。
- 校验原文、行号及 SHA-256 引用；用 AND/OR、能力上限和未知区间计算匹配度。
- 生成自包含的 `投递台.html` 与 `简历编辑器.html`，支持人工筛选、状态管理和本地保存。
- 为选定岗位导出可编辑 Markdown、引用记录与 PDF 草稿。

## 先运行虚构演示

需要 Python 3.10 或更高版本。建议使用虚拟环境，在仓库根目录运行：

```bash
python -m pip install -r requirements.txt
python examples/build_demo.py --output-dir demo-output
```

打开 `demo-output/out/投递台.html`，即可查看两条明确虚构的岗位。其中一条有已确认的能力依据，另一条能力未知，用于演示“待补充”与分数范围。`demo-output/out/简历编辑器.html` 是空白编辑器，可手工填写并通过浏览器打印为 PDF。

此命令只复制仓库内的虚构夹具、计算分数和生成本地页面，不抓取岗位、不调用模型。输出目录必须为空，避免覆盖已有工作。运行另一次演示时请换一个输出目录。

## 作为 Skill 使用

Skill 在 [`.agents/skills/job-hunt-console/`](.agents/skills/job-hunt-console/)。将整个目录交给支持本地 Skill 的助手；入口是 [`SKILL.md`](.agents/skills/job-hunt-console/SKILL.md)，权威规则是 [`references/spec.md`](.agents/skills/job-hunt-console/references/spec.md)。

Python 命令也可独立执行。以下命令均在仓库根目录运行，`workspace/` 用来保存个人工作数据，默认不会被 Git 收录。

```bash
python .agents/skills/job-hunt-console/scripts/init_config.py --dir workspace
```

初始化会创建配置和空白档案，并列出尚未填写的项目。先与使用者确认城市、岗位、模型和数据来源，再填写 `workspace/config.json`。模型标识由实际执行判读的宿主决定；没有 API 密钥字段，也没有内置模型服务。

新用户默认使用 `scoring.match_engine = "evidence"`。`--example` 提供完全虚构的旧版 `blocks` 配置，供理解历史兼容格式，不能作为本人的经历直接使用。

## 使用自己的岗位与经历

可以复用已合法取得的本地岗位记录，不必先运行抓取。`examples/fictional/out/rows.json` 展示最小岗位格式；实际材料应放在被忽略的 `workspace/` 中，样例目录只保留虚构内容。

1. 在配置中显式列出要登记的 UTF-8 `.md` / `.txt` 来源，运行 `sources`。
2. 根据岗位样本生成能力目录任务，在配置的模型宿主完成判读，再应用结果。
3. 根据已有材料生成访谈任务，只追问缺口；事实经本人确认后，才能显式应用确认状态。
4. 提取 JD 要求并匹配。任务包的 `brief.md` 和 manifest 是该轮结果的契约，不手工猜测结果格式。
5. 选定岗位后准备简历，人工检查措辞、数字与责任边界，最后检查 PDF。

常用入口：

```bash
python .agents/skills/job-hunt-console/scripts/evidence_workflow.py --config workspace/config.json sources
python .agents/skills/job-hunt-console/scripts/evidence_workflow.py --config workspace/config.json market-task
python .agents/skills/job-hunt-console/scripts/evidence_workflow.py --config workspace/config.json interview-task
python .agents/skills/job-hunt-console/scripts/evidence_workflow.py --config workspace/config.json requirements-task
python .agents/skills/job-hunt-console/scripts/score.py --config workspace/config.json
python .agents/skills/job-hunt-console/scripts/build_console.py --config workspace/config.json
python .agents/skills/job-hunt-console/scripts/resume_export.py --help
```

这些命令是分阶段入口，不是可直接连续执行的无人值守脚本。生成任务包后，先完成并应用该轮判读，再推进下一阶段。每个命令的 `--help` 列出实际参数。

## 可选的真实抓取

本仓库提供抓取调用入口，不打包第三方抓取器。依赖与接口见 [第三方说明](THIRD_PARTY_NOTICES.md)。将兼容的抓取脚本相对路径填入 `config.crawl.scraper` 后，才可人工触发：

```bash
python .agents/skills/job-hunt-console/scripts/daily.py --config workspace/config.json
```

抓取一轮后停止并诊断。补抓需要使用者批准，只针对缺失 ID；遇到风控码 `31` / `37`、访问频繁或登录失效立即停止。禁止自动全量重跑。

真实站点抓取、登录态与外部模型服务没有在此公开包的离线测试中完成联网验收。招聘数据有效性和模型判断质量需要针对实际使用场景检查。

## 测试

```bash
python -m unittest discover -s .agents/skills/job-hunt-console/scripts -p "test_*.py"
```

测试使用虚构事实与临时目录，覆盖引用失效、重复要求、AND/OR、能力未知、争议事实、缓存、导出与切块。离线演示验证页面生成链路；这些检查不代表真实求职效果或业务验收。

## 数据与限制

- 没有依据的能力是未知，不能作为 0 分或“不会”处理。
- 匹配分用于解释与排序，不是录用概率；薪资不进入能力匹配分。
- 不完整或未映射的能力要求保持可见，不伪造完整总分。
- 浏览器本地保存与 HTML 备份需要使用者管理；手工修改简历不自动确认事实。
- 配置、档案、原始 JD、生成页面和来源记录可能包含私人信息，只放在个人工作目录；公共样例只能使用虚构内容。
- 不包含真实用户数据集，也没有“零误判”等真实市场准确率承诺。

## 目录与许可证

```text
.agents/skills/job-hunt-console/
  SKILL.md          Skill 入口
  references/       公开规格与运行时数据契约
  scripts/          本地工作流与虚构测试
  templates/        空白配置、示例与页面模板
examples/
  build_demo.py     离线演示入口
  fictional/        明确虚构的岗位与经历夹具
```

本项目使用 [MIT License](LICENSE)。第三方抓取器的来源、许可证和未随包提供的内容见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
