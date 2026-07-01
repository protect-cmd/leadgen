from scrapers.antibot import BLOCK_STATUSES, ProbeResult, detect_vendor, looks_blocked


def test_cloudflare_by_header_and_body():
    assert detect_vendor(headers={"CF-Ray": "abc-123"}) == "cloudflare"
    assert detect_vendor(headers={"Server": "cloudflare"}) == "cloudflare"
    assert detect_vendor(status_code=503, body_text="Just a moment...") == "cloudflare"


def test_perimeterx_by_cookie_and_body():
    assert detect_vendor(cookies={"_px3": "x", "session": "y"}) == "perimeterx"
    assert detect_vendor(body_text="Please Press & Hold to continue") == "perimeterx"


def test_akamai_and_datadome_cookies():
    assert detect_vendor(cookies={"_abck": "x"}) == "akamai"
    assert detect_vendor(cookies={"datadome": "x"}) == "datadome"


def test_datadome_precedence_over_akamai_when_both_present():
    # DataDome can ride alongside Akamai-style cookies; DataDome is the real wall.
    assert detect_vendor(cookies={"datadome": "x", "ak_bmsc": "y"}) == "datadome"


def test_kasada_bare_429_and_header():
    assert detect_vendor(status_code=429, body_text="") == "kasada"
    assert detect_vendor(headers={"x-kpsdk-ct": "tok"}) == "kasada"


def test_set_cookie_header_blob_is_inspected():
    # Cookies sometimes arrive only in the Set-Cookie header, not a parsed dict.
    assert detect_vendor(headers={"Set-Cookie": "_px=abc; Path=/"}) == "perimeterx"


def test_no_fingerprint_returns_none():
    assert detect_vendor(status_code=200, headers={"Server": "nginx"}, body_text="<table>") is None


def test_looks_blocked_on_status_and_on_200_challenge():
    assert looks_blocked(status_code=403) is True
    assert all(looks_blocked(status_code=s) for s in BLOCK_STATUSES)
    # 200 but a Cloudflare challenge body -> still blocked.
    assert looks_blocked(status_code=200, body_text="Just a moment...") is True
    # Genuine empty-but-clean page -> not blocked (real "quiet day").
    assert looks_blocked(status_code=200, headers={"Server": "nginx"}, body_text="") is False


def test_vendor_fronting_a_200_is_not_a_block():
    # Regression: example.com-style 200 behind Cloudflare with real content.
    # detect_vendor names the vendor, but looks_blocked must be False.
    headers = {"CF-Ray": "x", "Server": "cloudflare"}
    body = "<html><body>Example Domain</body></html>"
    assert detect_vendor(headers=headers, body_text=body) == "cloudflare"
    assert looks_blocked(status_code=200, headers=headers, body_text=body) is False


def test_probe_result_summary_readable():
    blocked = ProbeResult(host="h", reachable=True, status_code=403,
                          server="cloudflare", vendor="cloudflare", blocked=True, body_len=512)
    assert "BLOCKED by cloudflare" in blocked.summary()
    fronted = ProbeResult(host="h", reachable=True, status_code=200,
                          server="cloudflare", vendor="cloudflare", blocked=False, body_len=600)
    assert "fronted by cloudflare" in fronted.summary()
    down = ProbeResult(host="h", reachable=False, error="timeout")
    assert "UNREACHABLE" in down.summary()
