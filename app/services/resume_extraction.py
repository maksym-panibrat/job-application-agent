"""
LLM-based resume extraction.

Extracts structured profile data from resume markdown text using Claude Haiku.
Called by profile_service.save_resume() after storing raw text.
"""

import json
import re

import structlog
from langchain_google_genai import ChatGoogleGenerativeAI

from app.agents.llm_safe import safe_ainvoke
from app.config import get_settings

log = structlog.get_logger()

EXTRACTION_PROMPT = """\
Extract structured profile data from this resume. Return ONLY valid JSON with these fields \
(omit any field you cannot confidently extract, do not guess):

- full_name: string
- email: string
- phone: string
- linkedin_url: string
- github_url: string
- portfolio_url: string
- target_roles: list of 1-3 appropriate job title strings inferred from experience
- skills: list of objects, each with:
    name (string), category (one of: language, framework, cloud, domain, tool),
    proficiency (one of: expert, proficient, familiar), years (number or null)
- work_experiences: list of objects, each with:
    company (string), title (string), start_date (YYYY-MM-DD string),
    end_date (YYYY-MM-DD string or null for current), description_md (1-2 sentence summary),
    technologies (list of strings)

Return only the JSON object, no markdown fences.

Resume:
{resume_md}"""


async def extract_profile_from_resume(resume_md: str) -> dict:
    """
    Use Claude Haiku to extract structured profile data from resume text.

    Returns a dict with keys: full_name, email, phone, linkedin_url, github_url,
    portfolio_url, target_roles, skills (list), work_experiences (list).
    Returns empty dict on failure.
    """
    settings = get_settings()

    try:
        if settings.environment == "test":
            from app.agents.test_llm import get_fake_llm
            llm = get_fake_llm("resume_extraction")
        else:
            llm = ChatGoogleGenerativeAI(
                model=settings.llm_resume_extraction_model,
                google_api_key=settings.google_api_key.get_secret_value(),
            )
        prompt = EXTRACTION_PROMPT.format(resume_md=resume_md[:8000])
        response = await safe_ainvoke(llm, prompt)
        raw = response.content if isinstance(response.content, str) else str(response.content)

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())

        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as exc:
        await log.awarning("resume_extraction.failed", error=str(exc))
        return {}
