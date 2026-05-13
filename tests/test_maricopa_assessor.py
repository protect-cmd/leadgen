from __future__ import annotations

from scrapers.arizona.maricopa_assessor import (
    MaricopaAssessorClient,
    ParcelRecord,
    _owner_search_variants,
)


class FakeSession:
    def __init__(self, payloads: list[dict]):
        self.payloads = payloads
        self.requests: list[dict] = []

    def get(self, url: str, *, params: dict, timeout: int):
        self.requests.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse(self.payloads.pop(0))


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def _feature(**attrs):
    return {"attributes": attrs}


def test_owner_search_variants_split_multi_party_landlord_names():
    assert _owner_search_variants("GANTOUS INVESTMENTS LLC/JOHN SALEM") == [
        "GANTOUS INVESTMENTS LLC",
        "JOHN SALEM",
    ]


def test_match_owner_returns_single_match_for_one_parcel():
    session = FakeSession([
        {
            "features": [
                _feature(
                    APN_DASH="102-39-930",
                    OWNER_NAME="PHOENIX LEASED HOUSING ASSOCIATES IV LLLP",
                    PHYSICAL_ADDRESS="7750 W ENCANTO BLVD   PHOENIX  85035",
                    MAIL_ADDRESS="2905 NORTHWEST BLVD STE 150 PLYMOUTH MN USA 55441",
                    PHYSICAL_CITY="PHOENIX",
                    PHYSICAL_ZIP="85035",
                    JURISDICTION="PHOENIX",
                )
            ]
        }
    ])

    result = MaricopaAssessorClient(session=session).match_owner(
        "PHOENIX LEASED HOUSING ASSOCIATES IV LLLP"
    )

    assert result.status == "single_match"
    assert result.query_variant == "PHOENIX LEASED HOUSING ASSOCIATES IV LLLP"
    assert result.records == [
        ParcelRecord(
            apn="102-39-930",
            owner_name="PHOENIX LEASED HOUSING ASSOCIATES IV LLLP",
            physical_address="7750 W ENCANTO BLVD PHOENIX 85035",
            mailing_address="2905 NORTHWEST BLVD STE 150 PLYMOUTH MN USA 55441",
            physical_city="PHOENIX",
            physical_zip="85035",
            jurisdiction="PHOENIX",
        )
    ]


def test_match_owner_returns_ambiguous_for_multiple_parcels():
    session = FakeSession([
        {
            "features": [
                _feature(APN_DASH="126-06-173", OWNER_NAME="GANTOUS INVESTMENTS LLC", PHYSICAL_ADDRESS="1819 N 40TH ST A1 PHOENIX 85008"),
                _feature(APN_DASH="126-06-174", OWNER_NAME="GANTOUS INVESTMENTS LLC", PHYSICAL_ADDRESS="1819 N 40TH ST A3 PHOENIX 85008"),
            ]
        }
    ])

    result = MaricopaAssessorClient(session=session).match_owner("GANTOUS INVESTMENTS LLC")

    assert result.status == "ambiguous"
    assert len(result.records) == 2


def test_match_owner_returns_no_match_after_trying_variants():
    session = FakeSession([
        {"features": []},
        {"features": []},
    ])

    result = MaricopaAssessorClient(session=session).match_owner("DESERT VIEW LLC/JOHN OWNER")

    assert result.status == "no_match"
    assert result.records == []
    assert [request["params"]["where"] for request in session.requests] == [
        "OWNER_NAME LIKE '%DESERT VIEW LLC%'",
        "OWNER_NAME LIKE '%JOHN OWNER%'",
    ]
