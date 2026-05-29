from types import SimpleNamespace

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
    assert "allowed_application_ids" in prompt
    assert "Copy it exactly" in prompt
    assert "Do not invent, shorten, normalize, or replace application_id" in prompt
    assert "top-level JSON object" in prompt
    assert '"results"' in prompt
    assert "exactly one result per application_id" in prompt


class _FakeBatchesClient:
    def __init__(self, *, create_job=None, get_jobs=None) -> None:
        self.create_job = create_job or SimpleNamespace(name="batches/provider-123")
        self.get_jobs = list(get_jobs or [])
        self.created_calls = []
        self.get_calls = []

    def create(self, *, model, src, config=None):
        self.created_calls.append({"model": model, "src": src, "config": config})
        return self.create_job

    def get(self, *, name):
        self.get_calls.append(name)
        return self.get_jobs.pop(0)


@pytest.mark.asyncio
async def test_gemini_batch_match_provider_submit_creates_inline_batch():
    from app.services.gemini_batch_match_provider import GeminiBatchMatchProvider

    batches = _FakeBatchesClient()
    provider = GeminiBatchMatchProvider(
        client=SimpleNamespace(batches=batches),
        model="gemini-test-model",
    )

    provider_batch_id = await provider.submit(
        requests=[
            {
                "request_key": "request-0001",
                "profile_text": "Senior Python engineer.",
                "jobs": [{"application_id": "app-1", "title": "Backend Engineer"}],
            }
        ],
        display_name="batch-match-test",
    )

    assert provider_batch_id == "batches/provider-123"
    assert len(batches.created_calls) == 1
    created = batches.created_calls[0]
    assert created["model"] == "gemini-test-model"
    assert created["config"]["display_name"] == "batch-match-test"
    assert created["src"][0]["metadata"] == {"request_key": "request-0001"}
    assert created["src"][0]["config"]["response_mime_type"] == "application/json"
    schema = created["src"][0]["config"]["response_json_schema"]
    assert schema["properties"]["results"]["items"]["properties"]["application_id"]["enum"] == [
        "app-1"
    ]
    prompt = created["src"][0]["contents"][0]["parts"][0]["text"]
    assert "Senior Python engineer." in prompt
    assert "app-1" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("JOB_STATE_RUNNING", (False, False, None)),
        ("JOB_STATE_SUCCEEDED", (True, False, None)),
        ("JOB_STATE_FAILED", (True, True, "provider error")),
        ("JOB_STATE_CANCELLED", (True, True, "provider error")),
        ("JOB_STATE_EXPIRED", (True, True, "provider error")),
        ("JOB_STATE_PARTIALLY_SUCCEEDED", (True, True, "provider error")),
    ],
)
async def test_gemini_batch_match_provider_poll_maps_job_state(state, expected):
    from app.services.gemini_batch_match_provider import GeminiBatchMatchProvider

    batches = _FakeBatchesClient(
        get_jobs=[SimpleNamespace(state=state, error=SimpleNamespace(message="provider error"))]
    )
    provider = GeminiBatchMatchProvider(
        client=SimpleNamespace(batches=batches),
        model="gemini-test-model",
    )

    status = await provider.poll(provider_batch_id="batches/provider-123")

    assert (status.ready, status.failed, status.error) == expected
    assert batches.get_calls == ["batches/provider-123"]


@pytest.mark.asyncio
async def test_gemini_batch_match_provider_fetch_output_parses_inline_responses():
    from app.services.gemini_batch_match_provider import GeminiBatchMatchProvider

    batch_job = SimpleNamespace(
        dest=SimpleNamespace(
            inlined_responses=[
                SimpleNamespace(
                    metadata={"request_key": "request-0001"},
                    response=SimpleNamespace(
                        candidates=[
                            SimpleNamespace(
                                content=SimpleNamespace(
                                    parts=[
                                        SimpleNamespace(
                                            text='''{
                                              "results": [
                                                {
                                                  "application_id": "app-1",
                                                  "score": 0.91,
                                                  "summary": "Strong fit",
                                                  "rationale": "Relevant backend work.",
                                                  "strengths": ["Python"],
                                                  "gaps": ["None"]
                                                }
                                              ]
                                            }'''
                                        )
                                    ]
                                )
                            )
                        ]
                    ),
                    error=None,
                )
            ]
        )
    )
    batches = _FakeBatchesClient(get_jobs=[batch_job])
    provider = GeminiBatchMatchProvider(
        client=SimpleNamespace(batches=batches),
        model="gemini-test-model",
    )

    output = await provider.fetch_output(provider_batch_id="batches/provider-123")

    assert len(output.requests) == 1
    request = output.requests[0]
    assert request.request_key == "request-0001"
    assert request.error is None
    assert len(request.results) == 1
    result = request.results[0]
    assert result.application_id == "app-1"
    assert result.score == 0.91
    assert result.summary == "Strong fit"
    assert result.strengths == ["Python"]
    assert result.gaps == ["None"]


@pytest.mark.asyncio
async def test_gemini_batch_match_provider_fetch_output_marks_request_errors():
    from app.services.gemini_batch_match_provider import GeminiBatchMatchProvider

    batch_job = SimpleNamespace(
        dest=SimpleNamespace(
            inlined_responses=[
                SimpleNamespace(
                    metadata={"request_key": "request-0001"},
                    response=None,
                    error=SimpleNamespace(message="safety block"),
                ),
                SimpleNamespace(
                    metadata={"request_key": "request-0002"},
                    response=SimpleNamespace(
                        candidates=[
                            SimpleNamespace(
                                content=SimpleNamespace(
                                    parts=[SimpleNamespace(text="not json")]
                                )
                            )
                        ]
                    ),
                    error=None,
                ),
            ]
        )
    )
    batches = _FakeBatchesClient(get_jobs=[batch_job])
    provider = GeminiBatchMatchProvider(
        client=SimpleNamespace(batches=batches),
        model="gemini-test-model",
    )

    output = await provider.fetch_output(provider_batch_id="batches/provider-123")

    assert [(request.request_key, request.error) for request in output.requests] == [
        ("request-0001", "safety block"),
        ("request-0002", "provider returned invalid JSON"),
    ]
