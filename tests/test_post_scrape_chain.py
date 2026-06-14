import scripts.post_scrape_chain as chain


def test_chain_runs_steps_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(chain, "_flag", lambda: calls.append("flag"))
    monkeypatch.setattr(chain, "_normalize", lambda: calls.append("normalize"))
    monkeypatch.setattr(chain, "_backfill_rent", lambda cap: calls.append(f"rent:{cap}"))
    monkeypatch.setenv("RENT_BACKFILL_DAILY_CAP", "150")

    rc = chain.main([])

    assert rc == 0
    assert calls == ["flag", "normalize", "rent:150"]


def test_rent_off_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(chain, "_flag", lambda: calls.append("flag"))
    monkeypatch.setattr(chain, "_normalize", lambda: calls.append("normalize"))
    monkeypatch.delenv("RENT_BACKFILL_DAILY_CAP", raising=False)

    rc = chain.main([])

    assert rc == 0
    assert calls == ["flag", "normalize"]   # rent step skipped at cap 0


def test_chain_continues_when_a_step_fails(monkeypatch):
    calls = []

    def boom():
        raise RuntimeError("flag down")

    monkeypatch.setattr(chain, "_flag", boom)
    monkeypatch.setattr(chain, "_normalize", lambda: calls.append("normalize"))
    monkeypatch.setattr(chain, "_backfill_rent", lambda cap: calls.append("rent"))
    monkeypatch.setenv("RENT_BACKFILL_DAILY_CAP", "10")

    rc = chain.main([])

    assert rc == 1                          # non-zero because a step failed
    assert calls == ["normalize", "rent"]   # later steps still ran (fault-isolated)
