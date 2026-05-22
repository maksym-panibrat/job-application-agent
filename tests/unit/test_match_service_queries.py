import uuid


def test_application_list_query_does_not_select_job_descriptions():
    from app.services.match_service import build_application_list_query

    query = build_application_list_query(uuid.uuid4(), status=None, min_score=None)
    compiled = str(query.compile(compile_kwargs={"literal_binds": False})).lower()

    assert "jobs.description_raw" not in compiled
    assert "jobs.description" not in compiled
