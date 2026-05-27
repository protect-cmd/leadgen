from scripts.enrich_supabase_green_a_searchbug import should_persist_hit


def test_should_persist_only_phone_hits_with_strong_address_match():
    assert should_persist_hit({"status": "phone_found", "address_match": "exact"})
    assert should_persist_hit({"status": "phone_found", "address_match": "same_street"})
    assert should_persist_hit({"status": "phone_found", "address_match": "near_street"})
    assert not should_persist_hit({"status": "phone_found", "address_match": "different"})
    assert not should_persist_hit({"status": "no_phone", "address_match": "near_street"})
