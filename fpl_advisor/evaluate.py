"""Time-ordered evaluation against baselines. Writes ``RESULTS.md``.

Usage: ``python -m fpl_advisor.evaluate``

Each test season is predicted by a model trained only on earlier seasons. Scoring is per
player per gameweek (double gameweeks summed), on players with a fixture that gameweek and
at least one earlier gameweek this season, so every method is scored on the same rows.
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from fpl_advisor import features, ingest, model
from fpl_advisor.api import POSITIONS

METHODS = {
    "last3": "Last-3-GW avg",
    "ep_next": "FPL ep_next",
    "form_model": "Form-only model",
    "full_model": "Full model",
}
GW_KEYS = ["season", "player_id", "round"]


def ep_next_baseline(data_dir: Path) -> pd.DataFrame:
    """FPL's ``ep_next`` as saved in our own snapshots, taken before that gameweek's deadline."""
    frames = []
    for folder in sorted((data_dir / "snapshots").glob("gw=*")):
        path = folder / "players.parquet"
        if (folder / ingest.DONE_MARKER).exists() and path.exists():
            p = pd.read_parquet(path, columns=["season", "player_id", "ep_next_gw", "ep_next"])
            frames.append(p.rename(columns={"ep_next_gw": "round"}))
    if not frames:
        return pd.DataFrame(columns=[*GW_KEYS, "ep_next"])
    return pd.concat(frames).dropna(subset=["round"]).drop_duplicates(GW_KEYS, keep="last")


def gameweek_table(
    df: pd.DataFrame, preds: dict[str, np.ndarray], ep_next: pd.DataFrame
) -> pd.DataFrame:
    """Collapse fixture rows to player-gameweek rows with every method's prediction."""
    rows = df.assign(**preds)
    agg = {
        "actual": (features.TARGET, "sum"),
        "minutes": ("minutes", "sum"),
        "element_type": ("element_type", "first"),
        "min_3": ("min_3", "first"),
    }
    agg |= {name: (name, "sum") for name in preds}
    if "xp" in rows:
        agg["community_xp"] = ("xp", "first")  # repeated per fixture, already per gameweek
    gw = rows.groupby(GW_KEYS).agg(**agg).reset_index()
    gw = gw.merge(ep_next.astype({"round": int}), on=GW_KEYS, how="left")

    # Last-3-GW baseline on gameweek totals (a double gameweek counts as one gameweek).
    gw = gw.sort_values(GW_KEYS).reset_index(drop=True)
    prev = gw.groupby(["season", "player_id"])["actual"].shift(1)
    gw["last3"] = (
        prev.groupby([gw["season"], gw["player_id"]])
        .rolling(3, min_periods=1)
        .mean()
        .reset_index(level=[0, 1], drop=True)
    )
    return gw[gw["last3"].notna()]


def _mean_spearman(part: pd.DataFrame, col: str) -> float:
    rhos = [
        spearmanr(g["actual"], g[col]).statistic
        for _, g in part.groupby("round")
        if len(g) > 2 and g[col].nunique() > 1 and g["actual"].nunique() > 1
    ]
    return float(np.mean(rhos)) if rhos else np.nan


def score(gw: pd.DataFrame, methods: list[str]) -> pd.DataFrame:
    """MAE and mean per-gameweek Spearman, per position and overall."""
    out = []
    groups = [("All", gw)] + [(POSITIONS[t], gw[gw["element_type"] == t]) for t in POSITIONS]
    for label, part in groups:
        for m in methods:
            valid = part[part[m].notna()]
            if valid.empty:
                continue
            out.append(
                {
                    "position": label,
                    "method": m,
                    "n": len(valid),
                    "mae": float((valid["actual"] - valid[m]).abs().mean()),
                    "spearman": _mean_spearman(valid, m),
                }
            )
    return pd.DataFrame(out)


def evaluate_season(df: pd.DataFrame, test_season: str, ep_next: pd.DataFrame) -> dict:
    train = df[df["season"] < test_season]
    test = df[df["season"] == test_season]
    full = model.train(train, features.FEATURES)
    form = model.train(train, features.FORM_FEATURES)
    preds = {
        "full_model": full.predict(test[features.FEATURES]),
        "form_model": form.predict(test[features.FORM_FEATURES]),
    }
    gw = gameweek_table(test, preds, ep_next)
    methods = [m for m in METHODS if gw[m].notna().any()]
    active = gw[gw["min_3"] > 0]
    return {
        "season": test_season,
        "train_seasons": sorted(train["season"].unique()),
        "gw": gw,
        "scores": {"active": score(active, methods), "all": score(gw, methods)},
        "ep_rows": int(gw["ep_next"].notna().sum()),
    }


def markdown_table(res: pd.DataFrame, value: str) -> str:
    table = res.pivot_table(index="position", columns="method", values=value, sort=False)
    table = table[[m for m in METHODS if m in table]].rename(columns=METHODS)
    table = table.loc[[p for p in ["All", *POSITIONS.values()] if p in table.index]]
    header = ["Position", *table.columns]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for pos, row in table.iterrows():
        cells = ["" if pd.isna(v) else f"{v:.3f}" for v in row]
        lines.append("| " + " | ".join([str(pos), *cells]) + " |")
    return "\n".join(lines)


def verdict(res: pd.DataFrame) -> list[str]:
    """Plain statements on whether the full model beats each baseline (All positions)."""
    overall = res[res["position"] == "All"].set_index("method")
    lines = []
    for base in ("last3", "ep_next", "form_model"):
        if base not in overall.index:
            continue
        mae_ok = overall.loc["full_model", "mae"] < overall.loc[base, "mae"]
        rho_ok = overall.loc["full_model", "spearman"] > overall.loc[base, "spearman"]
        word = {(True, True): "beats", (False, False): "does NOT beat"}.get(
            (mae_ok, rho_ok), "partly beats"
        )
        lines.append(
            f"- Full model {word} {METHODS[base]}: MAE "
            f"{overall.loc['full_model', 'mae']:.3f} vs {overall.loc[base, 'mae']:.3f}, "
            f"Spearman {overall.loc['full_model', 'spearman']:.3f} vs "
            f"{overall.loc[base, 'spearman']:.3f}."
        )
    return lines


def community_xp_check(gw: pd.DataFrame) -> list[str]:
    """Rank correlation on nailed starters who played 90: a pre-deadline forecast can't be high."""
    if "community_xp" not in gw or gw["community_xp"].isna().all():
        return []
    nailed = gw[(gw["min_3"] >= 85) & (gw["minutes"] >= 90)]
    xp, full = _mean_spearman(nailed, "community_xp"), _mean_spearman(nailed, "full_model")
    return [
        "",
        "### Why the community dataset's xP is not used as a baseline",
        "",
        f"On nailed starters who also played 90 minutes (n = {len(nailed)}), its mean",
        f"per-gameweek Spearman with actual points is {xp:.3f} (full model: {full:.3f}).",
        "Goals and assists among 90-minute starters are too random for any pre-deadline",
        "forecast to rank that well, so the column most likely contains post-deadline",
        "information. It is excluded; `ep_next` is measured from our own snapshots instead.",
    ]


def write_results(evals: list[dict], path: Path) -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    lines = [
        "# Results",
        "",
        f"Generated by `python -m fpl_advisor.evaluate` on {date.today()} (commit `{commit}`).",
        "Do not edit by hand.",
        "",
        "- Unit: one player in one gameweek (double gameweeks summed). Rows: players with a",
        "  fixture that gameweek and at least one earlier gameweek in the same season.",
        "- `active`: played in at least one of their last 3 gameweeks (known before the deadline).",
        "- Spearman is computed within each gameweek, then averaged. MAE lower is better,",
        "  Spearman higher is better.",
        "- Model predictions here have no injury information; in the app they are multiplied",
        "  by FPL's `chance_of_playing_next_round`.",
        "- `FPL ep_next` is only available for gameweeks where a snapshot was taken before",
        "  the deadline (the API does not keep history), so its column fills in over time.",
        "- The current season only includes players with minutes this season (others are",
        "  not downloaded), so its `all` population is smaller than for past seasons.",
    ]
    for ev in evals:
        for population in ("active", "all"):
            res = ev["scores"][population]
            n = int(res.loc[res["position"] == "All", "n"].max())
            lines += [
                "",
                f"## Test season {ev['season']}, {population} players (n = {n})",
                "",
                f"Trained on {', '.join(ev['train_seasons'])}. Gameweeks with an `ep_next` "
                f"snapshot: {ev['ep_rows']} player-gameweeks.",
                "",
                *verdict(res),
                "",
                "MAE",
                "",
                markdown_table(res, "mae"),
                "",
                "Spearman rank correlation",
                "",
                markdown_table(res, "spearman"),
            ]
        lines += community_xp_check(ev["gw"])
    text = "\n".join(lines) + "\n"
    path.write_text(text)
    return text


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ingest.DATA_DIR)
    parser.add_argument("--out", type=Path, default=Path("RESULTS.md"))
    parser.add_argument("--test-seasons", nargs="*", help="default: last two seasons in data")
    args = parser.parse_args(argv)

    df = features.build_features(features.load_history(args.data_dir))
    seasons = sorted(df["season"].unique())
    tests = [s for s in (args.test_seasons or seasons[-2:]) if s != seasons[0]]
    ep_next = ep_next_baseline(args.data_dir)
    evals = [evaluate_season(df, s, ep_next) for s in reversed(tests)]
    print(write_results(evals, args.out))


if __name__ == "__main__":
    main()
