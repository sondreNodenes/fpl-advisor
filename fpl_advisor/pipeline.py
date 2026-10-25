"""Scheduled job: snapshot new gameweeks, then retrain if anything changed.

Usage: ``python -m fpl_advisor.pipeline`` (the Cloud Run job entrypoint).
"""

from __future__ import annotations

import logging

from fpl_advisor import api, features, ingest, model

log = logging.getLogger(__name__)


def run() -> bool:
    """Returns True if a new model was trained."""
    client = api.FPLClient(cache_dir=ingest.CACHE_DIR, ttl_seconds=600)
    new_snapshot = ingest.snapshot_latest(client, ingest.DATA_DIR)
    past = ingest.download_past_seasons(list(ingest.DEFAULT_PAST_SEASONS), ingest.DATA_DIR)
    if new_snapshot is None and model.MODEL_PATH.exists():
        log.info("No new gameweek; model is current")
        return False
    df = features.build_features(features.load_history(ingest.DATA_DIR))
    fitted = model.train(df)
    seasons = sorted(df["season"].unique())
    model.save(fitted, model.MODEL_PATH, seasons=seasons, past_season_files=len(past))
    log.info("Trained on %s; saved %s", ", ".join(seasons), model.MODEL_PATH)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
