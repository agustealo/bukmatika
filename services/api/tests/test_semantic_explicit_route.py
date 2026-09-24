from bukmatika.research.routes import router


def test_semantic_search_is_a_separate_explicit_route() -> None:
    paths = {route.path for route in router.routes}

    assert "/v1/research/search" in paths
    assert "/v1/research/semantic-search" in paths
    assert "/v1/research/search" != "/v1/research/semantic-search"
