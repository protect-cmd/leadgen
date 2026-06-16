from services import daily_scheduler as ds


def _by_name():
    return {j.name: j for j in ds.SCHEDULED_JOBS}


def test_post_scrape_jobs_registered_after_scrapers():
    jobs = _by_name()
    assert "ists_harris" in jobs
    assert "post_scrape_chain" in jobs
    last_scraper = max((j.hour, j.minute) for n, j in jobs.items()
                       if n in {"texas", "tennessee", "arizona",
                                "ohio_franklin_raw", "ohio_hamilton"})
    assert (jobs["ists_harris"].hour, jobs["ists_harris"].minute) > last_scraper
    assert ((jobs["post_scrape_chain"].hour, jobs["post_scrape_chain"].minute)
            > (jobs["ists_harris"].hour, jobs["ists_harris"].minute))


def test_chain_job_points_at_the_chain_script():
    jobs = _by_name()
    assert jobs["post_scrape_chain"].script_name == "../scripts/post_scrape_chain.py"
    assert jobs["ists_harris"].script_name == "run_ists_harris.py"


def test_ists_franklin_scheduled_between_harris_and_chain():
    jobs = _by_name()
    assert "ists_franklin" in jobs
    assert jobs["ists_franklin"].script_name == "run_ists_franklin.py"
    assert jobs["ists_franklin"].args == ()  # real upsert, no --dry-run
    harris = (jobs["ists_harris"].hour, jobs["ists_harris"].minute)
    franklin = (jobs["ists_franklin"].hour, jobs["ists_franklin"].minute)
    chain = (jobs["post_scrape_chain"].hour, jobs["post_scrape_chain"].minute)
    assert harris <= franklin < chain


def test_scheduled_scrapers_do_not_pipe_enrichment():
    # Phase 5.2: enrichment is operator-driven via /lists; scrapers scrape only.
    for j in ds.SCHEDULED_JOBS:
        if j.name in {"arizona", "ohio_hamilton"}:
            assert "--pipe" not in j.args, f"{j.name} still pipes inline enrichment"
