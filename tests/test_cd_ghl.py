"""Cosner Drake GHL payload mapping (no network). Pins the defendant->contact
field mapping, the cosner-drake-lead tag, and that situation is intentionally
omitted (its only subaccount option is post-judgment, wrong for these leads)."""
import services.cd_ghl as g


def _rec(**kw):
    base = dict(
        case_number="261100274020",
        defendant_name="Linda D Jones",
        defendant_address="8635 Cottage Gate Ln, Houston, TX 77088",
        creditor_name="Republic Finance LLC",
        phone="+12815550123",
        language_hint="english_likely",
        filing_date="2026-06-24",
        answer_deadline="2026-07-24",
        state="TX", county="Harris",
    )
    base.update(kw)
    return base


def test_split_name_first_last():
    assert g._split_name("Linda D Jones") == ("Linda", "D Jones")
    assert g._split_name("Rodriguez, Francisco") == ("Francisco", "Rodriguez")


def test_payload_core_fields_and_tags():
    p = g._build_payload(_rec())
    assert p["firstName"] == "Linda"
    assert p["phone"] == "+12815550123"
    assert p["address1"] == "8635 Cottage Gate Ln, Houston, TX 77088"
    assert "cosner-drake-lead" in p["tags"]
    assert p["locationId"] == g._LOCATION_ID


def test_payload_custom_fields_mapped_by_id():
    p = g._build_payload(_rec())
    by_id = {c["id"]: c["field_value"] for c in p["customFields"]}
    assert by_id[g._FIELD_IDS["debtor_name"]] == "Linda D Jones"
    assert by_id[g._FIELD_IDS["case_number"]] == "261100274020"
    assert by_id[g._FIELD_IDS["creditor"]] == "Republic Finance LLC"
    assert by_id[g._FIELD_IDS["answer_deadline"]] == "2026-07-24"
    assert by_id[g._FIELD_IDS["language_preference"]] == "English"
    # situation field is deliberately not sent
    assert "situation" not in g._FIELD_IDS


def test_spanish_language_hint_maps_to_spanish():
    p = g._build_payload(_rec(language_hint="spanish_likely"))
    by_id = {c["id"]: c["field_value"] for c in p["customFields"]}
    assert by_id[g._FIELD_IDS["language_preference"]] == "Spanish"


def test_missing_creditor_omitted():
    p = g._build_payload(_rec(creditor_name=None))
    ids = {c["id"] for c in p["customFields"]}
    assert g._FIELD_IDS["creditor"] not in ids
