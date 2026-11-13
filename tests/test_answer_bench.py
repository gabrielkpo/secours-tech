"""Answer bench: scores computed by code, a record without doctrine text, a sheet for grading.
The chat client and the embedder are fakes; nothing leaves the machine."""

import json

import pytest

from secours_tech.answer import answer
from secours_tech.answer_bench import (
    Outcome,
    main,
    outcome,
    problem_kind,
    record,
    run,
    scored_test,
    scores,
    sheet,
    write,
)
from secours_tech.bench import load_case
from secours_tech.embed import embed_corpus
from test_answer import GOOD, PASSAGES, FakeChat, _draft
from test_bench import write_case
from test_embed import FakeEmbedder, _guide


def _outcome(case="Q01", refusal=False, status="answered", cited=1, expected=1, problems=()):
    return Outcome(case, "simple", refusal, status, 0.8, cited, expected, problems, 100, 10, 2.0)


def test_problem_kinds_keep_the_check_not_its_details():
    assert problem_kind("sentence 2: number 30 in no quote") == "number_not_quoted"
    assert problem_kind("sentence 1: quote not found word for word in P3") == "quote_not_found"
    assert problem_kind("answer: off the schema") == "off_schema"
    assert problem_kind("something new") == "other"


def test_scores_separate_cases_the_doctrine_answers_from_those_it_does_not():
    outcomes = [
        _outcome("Q01"),
        _outcome("Q02", status="clarification", expected=0),
        _outcome("Q03", status="blocked", cited=0, expected=0, problems=("not_supported",)),
        _outcome("Q04", refusal=True, status="refused", cited=0, expected=0),
        _outcome("Q05", refusal=True, status="answered", expected=0),
    ]
    result = scores(outcomes)
    assert (result["answerable"], result["unanswerable"]) == (3, 2)
    assert result["no_answer_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert result["cites_expected_page_rate"] == 0.5  # Q01 yes, Q02 no
    assert result["answered_unanswerable_rate"] == 0.5  # Q05
    assert result["blocked_by"] == {"not_supported": 1}
    assert result["status"] == {"answered": 2, "blocked": 1, "clarification": 1, "refused": 1}


def test_outcome_counts_cited_pages_against_the_expected_ones(tmp_path):
    case = load_case(write_case(tmp_path, excerpts=[("GDO-test.pdf", 11, "Couper le gaz.")]))
    result = answer("Que faire ?", PASSAGES, FakeChat(_draft(GOOD)))
    seen = outcome(case, result, 0.81234, 1.234)
    assert (seen.status, seen.cited_pages, seen.cited_expected) == ("answered", 1, 1)
    assert (seen.prompt_tokens, seen.completion_tokens) == (200, 40)
    assert (seen.top_similarity, seen.seconds) == (0.8123, 1.23)


def test_the_sheet_shows_expected_and_shown_answers_with_lines_to_grade(tmp_path):
    case = load_case(write_case(tmp_path))
    result = answer("Que faire ?", PASSAGES, FakeChat(_draft(GOOD)))
    text = sheet([case], {case.id: result}, [outcome(case, result, 0.8, 1.0)])
    assert "**Expected**: Cinq mètres." in text
    assert "**Serious fault if**: une autre distance" in text
    assert "Le gaz est coupé avant de ventiler" in text
    assert "grade = \nserious = " in text


def test_records_hold_no_doctrine_text_and_are_never_overwritten(conn, tmp_path):
    _guide(conn)
    fake = FakeEmbedder()
    embed_corpus(conn, fake)
    path = write_case(tmp_path, excerpts=[("GDO-test.pdf", 2, "Couper le gaz.")])
    path.write_text(path.read_text().replace("Quelle distance ?", "Et le gaz ?"))
    case = load_case(path)
    gas = "Couper le gaz avant de ventiler."
    draft = _draft({"text": "Le gaz est coupé.", "citations": [{"passage": "P1", "quote": gas}]})
    results, outcomes = run(conn, fake, FakeChat(draft), [case])
    assert results["Q01"].status == "answered"
    data = record(conn, "dev", "abc", outcomes, {"min_similarity": None})
    assert data["scores"]["cites_expected_page_rate"] == 1.0
    text = json.dumps(data, ensure_ascii=False)
    assert "gaz" not in text
    first, second = write(tmp_path, data), write(tmp_path, data)
    assert first != second and second.name.endswith("-dev-answers-2.json")


def test_the_test_split_needs_final_and_is_answered_once(tmp_path):
    cases = tmp_path / "cases"
    cases.mkdir()
    write_case(cases)
    for path in cases.glob("*.toml"):
        path.write_text(path.read_text().replace('split = "dev"', 'split = "test"'))
    base = ["--cases", str(cases), "--fingerprint", str(tmp_path / "none.sha256")]
    with pytest.raises(SystemExit, match="--final"):
        main([*base, "--split", "test"])
    records = tmp_path / "answers"
    records.mkdir()
    (records / "20261113T000000Z-test-answers.json").write_text("{}")
    assert scored_test(records)
    with pytest.raises(SystemExit, match="already answered"):
        main([*base, "--split", "test", "--final", "--records", str(records)])
