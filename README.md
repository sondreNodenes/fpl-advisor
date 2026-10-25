# fpl-advisor

Fantasy Premier League advisor. Paste an FPL team URL or ID and get predicted points for every player
for the next gameweek, plus recommended transfers (0 to 2, net of the 4-point hit), captain,
vice-captain and the best starting XI.

> Work in progress. See `PROGRESS.md` for status.

## What it does

_TBD (Phase 4)._

## Architecture

_TBD (Phase 6)._

## Run locally

```bash
uv sync
make test
make lint
```

## Use from Claude (MCP server)

`fpl_advisor/mcp_server.py` exposes four tools over stdio: `get_team(team)`,
`predict_player(name)`, `recommend_transfers(team)` and `captain_options(team)`.
It needs local data and a model first (`python -m fpl_advisor.ingest && python -m fpl_advisor.model`).

Claude Code:

```bash
claude mcp add fpl-advisor -- uv run --directory /path/to/fpl-advisor python -m fpl_advisor.mcp_server
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "fpl-advisor": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/fpl-advisor", "python", "-m", "fpl_advisor.mcp_server"]
    }
  }
}
```

Then ask, for example, "Who should team 5767275 captain this week?".

## Deploy

_TBD (Phase 6)._

## Results

_TBD (Phase 2). All numbers will be produced by `python -m fpl_advisor.evaluate`._

## Limitations

_TBD._
