import uuid


def test_application_list_query_does_not_select_job_descriptions():
    from app.services.match_service import build_application_list_query

    query = build_application_list_query(uuid.uuid4(), status=None, min_score=None)
    compiled = str(query.compile(compile_kwargs={"literal_binds": False})).lower()

    assert "jobs.description_raw" not in compiled
    assert "jobs.description" not in compiled


def test_score_candidate_query_selects_only_scoring_columns():
    from app.services.match_service import build_score_candidate_query

    query = build_score_candidate_query(
        company_ids=[uuid.uuid4()],
        matched_ids=set(),
        limit=20,
    )
    compiled = str(query.compile(compile_kwargs={"literal_binds": False})).lower()
    selected = compiled.split(" from ", 1)[0]

    assert "select jobs.id" in compiled
    assert "jobs.description_raw" in selected
    assert "jobs.description," in selected
    assert "jobs.created_at" not in compiled
    assert "jobs.updated_at" not in compiled
