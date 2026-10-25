import pandas as pd

from fpl_advisor import ingest, model, pipeline


def _patch(monkeypatch, tmp_path, new_snapshot, model_exists):
    path = tmp_path / "model.joblib"
    if model_exists:
        path.touch()
    trained = []
    monkeypatch.setattr(model, "MODEL_PATH", path)
    monkeypatch.setattr(ingest, "snapshot_latest", lambda *a, **k: new_snapshot)
    monkeypatch.setattr(ingest, "download_past_seasons", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.features, "load_history", lambda *a: "history")
    monkeypatch.setattr(
        pipeline.features, "build_features", lambda df: pd.DataFrame({"season": ["2026-27"]})
    )
    monkeypatch.setattr(model, "train", lambda df: "fitted")
    monkeypatch.setattr(model, "save", lambda fitted, p, **meta: trained.append(fitted))
    return trained


def test_skips_training_when_nothing_new(monkeypatch, tmp_path) -> None:
    trained = _patch(monkeypatch, tmp_path, new_snapshot=None, model_exists=True)
    assert pipeline.run() is False
    assert trained == []


def test_trains_on_new_snapshot_or_missing_model(monkeypatch, tmp_path) -> None:
    trained = _patch(monkeypatch, tmp_path, new_snapshot=tmp_path, model_exists=True)
    assert pipeline.run() is True
    trained = _patch(monkeypatch, tmp_path / "x", new_snapshot=None, model_exists=False)
    assert pipeline.run() is True
    assert trained == ["fitted"]
