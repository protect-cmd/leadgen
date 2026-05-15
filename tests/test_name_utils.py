from __future__ import annotations

import pytest
from services.name_utils import parse_name, split_tenants


class TestParseName:
    def test_last_comma_first(self):
        assert parse_name("JOHNSON, MARY") == ("MARY", "JOHNSON")

    def test_last_comma_first_middle(self):
        # Middle name stripped
        assert parse_name("JOHNSON, MARY ANN") == ("MARY", "JOHNSON")

    def test_last_comma_first_initial(self):
        # Middle initial stripped
        assert parse_name("LILLY, BRETT L") == ("BRETT", "LILLY")

    def test_last_comma_first_initial_dot(self):
        assert parse_name("LILLY, BRETT L.") == ("BRETT", "LILLY")

    def test_first_last(self):
        assert parse_name("JOHN SMITH") == ("JOHN", "SMITH")

    def test_first_middle_last(self):
        # Middle name stripped
        assert parse_name("BRETT L LILLY") == ("BRETT", "LILLY")

    def test_first_middle_initial_dot_last(self):
        assert parse_name("BRETT L. LILLY") == ("BRETT", "LILLY")

    def test_single_token(self):
        assert parse_name("JOHN") == ("", "")

    def test_empty_string(self):
        assert parse_name("") == ("", "")

    def test_whitespace_only(self):
        assert parse_name("   ") == ("", "")

    def test_lowercase_preserved(self):
        # parse_name does not uppercase — callers handle casing
        first, last = parse_name("john smith")
        assert first == "john"
        assert last == "smith"


class TestSplitTenants:
    def test_single_person(self):
        assert split_tenants("JOHN SMITH") == ["JOHN SMITH"]

    def test_three_tokens_not_split(self):
        # Could be first+middle+last — not split
        assert split_tenants("BRETT L LILLY") == ["BRETT L LILLY"]

    def test_four_tokens_split(self):
        assert split_tenants("AVONTE THOMAS ASHANTE JOHNSON") == [
            "AVONTE THOMAS",
            "ASHANTE JOHNSON",
        ]

    def test_four_tokens_with_initial_not_split(self):
        # Token[1] is a single char (middle initial) → treat as single person
        assert split_tenants("JOHN A SMITH DOE") == ["JOHN A SMITH DOE"]

    def test_empty_string(self):
        assert split_tenants("") == [""]
