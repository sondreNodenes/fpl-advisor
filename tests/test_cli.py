from fpl_advisor import api, cli
from tests.conftest import sample_advice


def test_format_advice() -> None:
    text = cli.format_advice(sample_advice())
    assert "predicting GW9" in text
    assert "(C)" in text and "(V)" in text
    assert "Note: test note" in text


def test_main_reports_errors(monkeypatch, capsys, fake_advisor) -> None:
    fake_advisor.error = api.NotFoundError("Team 1 not found (deleted or private)")
    monkeypatch.setattr(cli, "Advisor", lambda: fake_advisor)
    assert cli.main(["1"]) == 1
    assert "not found" in capsys.readouterr().err


def test_main_json(monkeypatch, capsys, fake_advisor) -> None:
    monkeypatch.setattr(cli, "Advisor", lambda: fake_advisor)
    assert cli.main(["5767275", "--json"]) == 0
    assert '"team_name": "Test FC"' in capsys.readouterr().out
