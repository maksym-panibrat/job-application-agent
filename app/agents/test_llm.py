from langchain_core.language_models.fake_chat_models import FakeListChatModel

_DEFAULT_RESPONSES: dict[str, list[str]] = {
    "onboarding": [
        "Hi! Tell me about the role you're looking for.",
        '{"target_title": "Software Engineer", "location": "Remote"}',
    ],
    "matching": [
        '{"score": 75, "rationale": "Good match", "strengths": ["Python"], "gaps": ["Go"]}',
    ],
    "generation": [
        "Tailored resume content here.",
        "Tailored cover letter content here.",
    ],
    "resume_extraction": [
        '{"name": "Test User", "skills": ["Python"], "work_experience": []}',
    ],
}


def get_fake_llm(purpose: str = "matching") -> FakeListChatModel:
    responses = _DEFAULT_RESPONSES.get(purpose, ["fake response"])
    return FakeListChatModel(responses=responses)
