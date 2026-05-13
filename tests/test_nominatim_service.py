from __future__ import annotations

from unittest.mock import patch

from services.nominatim_service import NominatimResult, geocode_street_cobb


def _fake_hit(city: str = "Marietta", postcode: str = "30060") -> list[dict]:
    return [{"address": {"city": city, "postcode": postcode}}]


def _fake_miss() -> list[dict]:
    return []


def test_geocode_returns_city_and_postcode():
    with patch("services.nominatim_service.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = _fake_hit("Marietta", "30066")
        result = geocode_street_cobb("4555 JAMERSON FOREST PKWY")
    assert result is not None
    assert result.city == "Marietta"
    assert result.postcode == "30066"


def test_geocode_returns_none_on_no_hit():
    with patch("services.nominatim_service.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = _fake_miss()
        result = geocode_street_cobb("NONEXISTENT ROAD NOWHERE")
    assert result is None


def test_geocode_returns_none_on_http_error():
    with patch("services.nominatim_service.requests.get", side_effect=RuntimeError("timeout")):
        result = geocode_street_cobb("123 MAIN ST")
    assert result is None


def test_geocode_uses_addressdetails_and_county_suffix():
    """Verify the request encodes the county+state context."""
    with patch("services.nominatim_service.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = _fake_hit()
        geocode_street_cobb("100 TEST LN")
    call_params = mock_get.call_args[1]["params"]
    assert "Cobb County" in call_params["q"]
    assert call_params["addressdetails"] == 1
