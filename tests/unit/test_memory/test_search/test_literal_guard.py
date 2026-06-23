from everos.memory.search.literal_guard import (
    contains_required_literal_terms,
    required_literal_terms,
)


def test_required_literal_terms_extracts_identifier_like_tokens() -> None:
    assert required_literal_terms("env176") == ["env176"]
    assert required_literal_terms("db_main 连接命令") == ["db_main"]
    assert required_literal_terms("make all x86") == ["x86"]
    assert required_literal_terms("release-v3.18.6R") == ["release-v3.18.6r"]


def test_required_literal_terms_ignores_plain_language() -> None:
    assert required_literal_terms("how optimize table") == []
    assert required_literal_terms("command login shell") == []


def test_contains_required_literal_terms_requires_all_anchors() -> None:
    terms = required_literal_terms("db_main env176")

    assert contains_required_literal_terms("env176 used db_main", terms)
    assert not contains_required_literal_terms("db_main only", terms)
