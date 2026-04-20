"""
LLM-based resume extraction.

Extracts structured profile data from resume markdown text using Gemini Flash.
Called by profile_service.save_resume() after storing raw text.
"""

import json
import re
import time

import structlog
from google.api_core.exceptions import ResourceExhausted
from langchain_google_genai import ChatGoogleGenerativeAI

from app.agents.llm_safe import BudgetExhausted, safe_ainvoke
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


class ResumeExtractionError(Exception):
    pass


class LLMUnavailableError(ResumeExtractionError):
    pass


class InvalidResumeError(ResumeExtractionError):
    pass


async def extract_profile_from_resume(resume_md: str) -> dict:
    """
    Use Gemini Flash to extract structured profile data from resume text.

    Returns a dict with keys: full_name, email, phone, linkedin_url, github_url,
    portfolio_url, target_roles, skills (list), work_experiences (list).

    Raises:
        LLMUnavailableError: quota exhausted or budget exceeded
        InvalidResumeError: LLM response was not parseable JSON
        ResumeExtractionError: any other extraction failure
    """
    settings = get_settings()
    t0 = time.perf_counter()

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

        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())

        data = json.loads(raw)
        if not isinstance(data, dict):
            raise InvalidResumeError("LLM returned non-dict JSON")

        await log.ainfo(
            "resume_extraction.completed",
            fields=len(data),
            resume_length=len(resume_md),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        return data

    except (ResourceExhausted, BudgetExhausted) as exc:
        await log.awarning("resume_extraction.llm_unavailable", error=str(exc))
        raise LLMUnavailableError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        await log.awarning("resume_extraction.parse_failed", error=str(exc))
        raise InvalidResumeError(str(exc)) from exc
    except ResumeExtractionError:
        raise
    except Exception as exc:
        await log.awarning("resume_extraction.failed", error=str(exc))
        raise ResumeExtractionError(str(exc)) from exc
