from __future__ import annotations

from unittest.mock import MagicMock, patch

from scrapers.georgia.cobb_assessor import CobbAssessorClient, CobbParcelRecord


def _arcgis_response(features: list[dict]) -> dict:
    return {"features": [{"attributes": f} for f in features]}


def _make_feature(
    pin="01001001010",
    owner_nam1="SMITH JOHN",
    situs_addr="123 MAIN ST",
    owner_city="MARIETTA",
    owner_stat="GA",
    owner_zip="30060",
) -> dict:
    return {
        "PIN": pin,
        "OWNER_NAM1": owner_nam1,
        "SITUS_ADDR": situs_addr,
        "OWNER_CITY": owner_city,
        "OWNER_STAT": owner_stat,
        "OWNER_ZIP": owner_zip,
    }


def test_single_match_returns_single_match_status():
    client = CobbAssessorClient()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _arcgis_response([_make_feature()])
    with patch.object(client.session, "get", return_value=fake_response):
        result = client.match_owner("SMITH JOHN")
    assert result.status == "single_match"
    assert len(result.records) == 1
    assert result.records[0].situs_addr == "123 MAIN ST"
    assert result.records[0].pin == "01001001010"


def test_multiple_matches_returns_ambiguous_status():
    client = CobbAssessorClient()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _arcgis_response([
        _make_feature(pin="01001001010", situs_addr="123 MAIN ST"),
        _make_feature(pin="01001001020", situs_addr="456 OAK AVE"),
    ])
    with patch.object(client.session, "get", return_value=fake_response):
        result = client.match_owner("SMITH JOHN")
    assert result.status == "ambiguous"
    assert len(result.records) == 2


def test_no_matches_returns_no_match_status():
    client = CobbAssessorClient()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _arcgis_response([])
    with patch.object(client.session, "get", return_value=fake_response):
        result = client.match_owner("UNKNOWN LLC")
    assert result.status == "no_match"
    assert result.records == []


def test_http_error_returns_error_status():
    client = CobbAssessorClient()
    with patch.object(client.session, "get", side_effect=RuntimeError("timeout")):
        result = client.match_owner("SMITH JOHN")
    assert result.status == "error"
    assert "timeout" in result.error


def test_name_normalization_strips_special_chars():
    from scrapers.georgia.cobb_assessor import _normalize_owner_name
    assert _normalize_owner_name("HPA II BORROWER 2020-1 ML LLC") == "HPA II BORROWER 2020 1 ML LLC"
    assert _normalize_owner_name("  SMITH,  JOHN  ") == "SMITH JOHN"
