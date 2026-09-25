from bukmatika.main import app


def test_selected_library_grounded_answer_route_is_registered() -> None:
    route = app.openapi()["paths"].get("/v1/ai/research/answer-selection")

    assert route is not None
    assert "post" in route
