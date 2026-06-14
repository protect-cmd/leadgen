import scripts.verify_dncscrub as v


def test_reports_api_inactive_without_login(monkeypatch):
    monkeypatch.delenv("DNCSCRUB_LOGIN_ID", raising=False)
    report = v.check()
    assert report["api_configured"] is False
    assert report["mode"] == "local_files_only"


def test_reports_api_active_with_login(monkeypatch):
    monkeypatch.setenv("DNCSCRUB_LOGIN_ID", "abc")
    monkeypatch.setattr(v.dnc_service, "_api_verdicts", lambda phones: {"6155551234": "callable"})
    report = v.check(test_phone="6155551234")
    assert report["api_configured"] is True
    assert report["test_verdict"] == "callable"
