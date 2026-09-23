# stock-skill

A 股选股 Agent Skill。选强势股：五日线之上、强趋势，或者有逻辑的个股，或者有业绩预期的个股。

把本仓库放到智能体的 skills 目录后即可被发现：

```bash
git clone https://github.com/AnswerYas/stock-skill.git ~/.cursor/skills/stock-skill
```

Codex 可放到 `~/.agents/skills/stock-skill`。技能入口是仓库根目录的 `SKILL.md`。

趋势条件由脚本核对，不手算均线：

```bash
python3 scripts/screen.py --pool 30
python3 scripts/screen.py 600519
python3 scripts/screen.py --name 贵州茅台
```

脚本只使用 Python 标准库和公开行情接口。逻辑和业绩预期需要智能体另查公告或新闻，并带来源。筛选结果不是投资建议。
