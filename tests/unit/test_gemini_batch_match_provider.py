import pytest


def test_build_gemini_batch_request_uses_key_and_gemini_contents_shape():
    from app.services.gemini_batch_match_provider import build_gemini_batch_request

    payload = build_gemini_batch_request(
        request_key="request-0001",
        profile_text="Senior Python engineer targeting remote backend roles.",
        jobs=[
            {
                "application_id": "app-1",
                "title": "Backend Engineer",
                "company": "Acme",
                "location": "Remote",
                "workplace_type": "remote",
                "description": "Build APIs with Python.",
            }
        ],
    )

    assert payload["key"] == "request-0001"
    assert payload["request"] == {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": payload["request"]["contents"][0]["parts"][0]["text"]}],
            }
        ]
    }


def test_build_gemini_batch_request_prompt_contains_profile_and_application_ids():
    from app.services.gemini_batch_match_provider import build_gemini_batch_request

    payload = build_gemini_batch_request(
        request_key="request-0002",
        profile_text="Candidate profile text goes here.",
        jobs=[
            {
                "application_id": "app-alpha",
                "title": "Platform Engineer",
                "company": "ExampleCo",
                "location": "New York",
                "workplace_type": "hybrid",
                "description": "Own cloud platform reliability.",
            },
            {
                "application_id": "app-beta",
                "title": "Data Engineer",
                "company": "DataCo",
                "location": None,
                "workplace_type": None,
                "description": "Build data pipelines.",
            },
        ],
    )

    prompt = payload["request"]["contents"][0]["parts"][0]["text"]

    assert "Candidate profile text goes here." in prompt
    assert "app-alpha" in prompt
    assert "app-beta" in prompt
    assert "top-level JSON object" in prompt
    assert '"results"' in prompt
    assert "exactly one result per application_id" in prompt


@pytest.mark.asyncio
async def test_gemini_batch_match_provider_runtime_methods_require_api_wiring():
    from app.services.gemini_batch_match_provider import GeminiBatchMatchProvider

    provider = GeminiBatchMatchProvider()

    with pytest.raises(RuntimeError, match="Gemini batch submit API wiring is required"):
        await provider.submit(requests=[], display_name="batch")
    with pytest.raises(RuntimeError, match="Gemini batch poll API wiring is required"):
        await provider.poll(provider_batch_id="batch-id")
    with pytest.raises(RuntimeError, match="Gemini batch output API wiring is required"):
        await provider.fetch_output(provider_batch_id="batch-id")
