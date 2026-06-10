from pipeline.queue_builder import _is_fireable_contact


def test_fireable_contact_excludes_searchbug_review_statuses():
    assert _is_fireable_contact({"searchbug_status": "phone_found"}) is True
    assert _is_fireable_contact({"searchbug_status": None}) is True
    assert _is_fireable_contact({"searchbug_status": "name_mismatch"}) is False
    assert _is_fireable_contact({"searchbug_status": "ambiguous"}) is False
