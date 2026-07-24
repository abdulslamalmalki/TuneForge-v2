"""Machine-readable LLM-interface rulebook endpoint."""


def test_interface_contract_is_provider_neutral_and_versioned(client):
    response = client.get("/api/v1/interface-contract")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "1.0"
    assert body["model_provider_status"] == "not_connected"
    assert body["provider_abstraction_status"] == "available"
    assert body["connected_provider_ids"] == []
    assert body["deterministic_fallback_status"] == "required"
    assert body["natural_language_routing_status"] == "limited_deterministic"
    assert body["validation_failure_action"] == ("discard_model_draft_use_deterministic_packet")
    assert body["statement_policy"] == {
        "text": "exact_accepted_claim_text",
        "claim_cardinality": "one_statement_per_claim",
        "citation_policy": "complete_retrieved_passage_id_set",
        "ordering": "claim_type_then_claim_id",
    }


def test_interface_contract_is_documented_in_openapi(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/interface-contract"]["get"]
    assert operation["tags"] == ["interface contract"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/InterfaceContractOut")


def test_question_router_api_exposes_the_rule_without_generating_an_answer(client):
    response = client.get(
        "/api/v1/interface/route",
        params={"question": "What is Curve Optimizer?"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "version": "1.0",
        "kind": "setting_lookup",
        "original_query": "What is Curve Optimizer?",
        "routed_value": "Curve Optimizer",
        "rule": "what_is",
        "question_id": None,
        "transformed": True,
    }
