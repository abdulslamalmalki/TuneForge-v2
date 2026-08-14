from app.domain import assistant_retrieval


def test_setting_hint_is_searched_before_question_noise(monkeypatch, db_session):
    calls = []

    def fake_search(_session, query):
        calls.append(query)
        return []

    monkeypatch.setattr(assistant_retrieval, "search_approved", fake_search)

    assistant_retrieval.find_approved_matches(
        db_session,
        "Is SoC Voltage safe to change?",
        setting_hint="SoC Voltage",
    )

    assert calls[0] == "SoC Voltage"
    assert "SoC Voltage safe change" in calls
