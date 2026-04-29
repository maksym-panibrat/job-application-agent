"""Bidirectional slug ↔ company_name mapping.

The forward transform `slug.replace("-", " ").title()` was already used by
`GreenhouseBoardSource._parse_job` to set Job.company_name. We centralize it
here and add the reverse so the match queue can find profiles interested in a
job by reverse-mapping its company_name back to a slug."""


def slug_to_company_name(slug: str) -> str:
    return slug.replace("-", " ").title()


def company_name_to_slug(name: str) -> str:
    return name.lower().replace(" ", "-")
