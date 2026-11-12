from pathlib import Path

import pytest

from secours_tech.bench import (
    Case,
    CaseError,
    Excerpt,
    assign_splits,
    evaluate,
    fingerprint,
    fulltext,
    load_case,
    load_cases,
    merge_chunks,
    missing_excerpts,
    normalize,
    page_ranks,
    summarize,
    unindexed_excerpts,
    write_split,
)
from secours_tech.collect import Entry
from secours_tech.extract import Page
from secours_tech.load import SourceDocument, store_document

CASE = """\
id = "{id}"
category = "{category}"
split = "dev"
refusal = {refusal}
question = "Quelle distance ?"
expected_answer = "Cinq mètres."
facts = ["**5 mètres**"]
serious_fault_if = "une autre distance"
{excerpts}"""

EXCERPT = """
[[excerpts]]
document = "{document}"
page = {page}
text = "{text}"
"""


def write_case(folder: Path, id="Q01", category="simple", refusal=False, excerpts=None) -> Path:
    if excerpts is None:
        excerpts = [("doc.pdf", 1, "Couper le gaz.")]
    body = "".join(EXCERPT.format(document=d, page=p, text=t) for d, p, t in excerpts)
    path = folder / f"{id}.toml"
    path.write_text(
        CASE.format(id=id, category=category, refusal=str(refusal).lower(), excerpts=body),
        encoding="utf-8",
    )
    return path


def test_a_case_is_read_with_the_pages_it_expects(tmp_path):
    case = load_case(write_case(tmp_path, excerpts=[("a.pdf", 3, "x"), ("a.pdf", 3, "y")]))
    assert case.expected_pages == {("a.pdf", 3)}
    assert not case.refusal


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda t: t.replace('question = "Quelle distance ?"\n', ""), "missing question"),
        (lambda t: t.replace('category = "simple"', 'category = "other"'), "unknown category"),
        (lambda t: t.replace('id = "Q01"', 'id = "Q02"'), "differs from the file name"),
        (lambda t: t.split("[[excerpts]]")[0], "needs at least one excerpt"),
        (lambda t: t.replace("page = 1", 'page = "un"'), "malformed excerpt"),
    ],
)
def test_a_malformed_case_is_refused(tmp_path, edit, message):
    path = write_case(tmp_path)
    path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(CaseError, match=message):
        load_case(path)


def test_a_refusal_case_needs_no_excerpt(tmp_path):
    assert load_case(write_case(tmp_path, refusal=True, excerpts=[])).refusal


def _cases(sizes: dict[str, int]) -> list[Case]:
    cases = []
    for category, count in sizes.items():
        for i in range(count):
            cases.append(Case(f"{category}{i}", category, "dev", "?", False, (), Path("x")))
    return cases


def test_the_test_split_takes_thirty_percent_in_every_category():
    cases = _cases({"simple": 11, "complex": 5, "function": 5, "out_of_scope": 5, "trap": 3,
                    "medical": 2})  # fmt: skip
    splits = assign_splits(cases)
    taken = {}
    for case in cases:
        taken[case.category] = taken.get(case.category, 0) + (splits[case.id] == "test")
    assert sum(taken.values()) == 9  # 30 % of 31
    assert taken == {"simple": 3, "complex": 2, "function": 1, "out_of_scope": 1, "trap": 1,
                     "medical": 1}  # fmt: skip
    assert assign_splits(cases) == splits  # same seed, same draw


def test_the_fingerprint_follows_the_test_cases_only(tmp_path):
    for id in ("Q01", "Q02"):
        write_case(tmp_path, id=id)
    write_split(load_case(tmp_path / "Q02.toml"), "test")
    before = fingerprint(load_cases(tmp_path))
    dev = tmp_path / "Q01.toml"
    dev.write_text(dev.read_text(encoding="utf-8") + "\n# note\n", encoding="utf-8")
    assert fingerprint(load_cases(tmp_path)) == before  # a dev case may still change
    test = tmp_path / "Q02.toml"
    test.write_text(test.read_text(encoding="utf-8").replace("Cinq", "Six"), encoding="utf-8")
    assert fingerprint(load_cases(tmp_path)) != before


def test_excerpts_are_found_word_for_word_on_their_page(tmp_path, make_pdf):
    pdf = make_pdf(["Intro.", "Couper le gaz et ventiler le local."], name="guide.pdf")
    case = Case(
        "Q01",
        "simple",
        "dev",
        "?",
        False,
        (
            Excerpt("guide.pdf", 2, "Couper le gaz […] ventiler le local."),
            Excerpt("guide.pdf", 1, "Couper le gaz"),  # right text, wrong page
            Excerpt("scan.pdf", 4, "lu par OCR."),  # no PDF here: OCR text only
            Excerpt("guide.pdf", 9, "Couper le gaz"),  # past the end of the PDF
        ),
        Path("x"),
    )
    ocr = {("scan.pdf", 4): "Texte lu par OCR."}
    missing = missing_excerpts([case], pdf.parent, lambda d, p: ocr.get((d, p)))
    assert [(e.document, e.page) for _, e in missing] == [("guide.pdf", 1), ("guide.pdf", 9)]


def test_pages_are_counted_once_in_retrieval_order():
    excerpts = (Excerpt("a.pdf", 7, "x"), Excerpt("a.pdf", 9, "y"), Excerpt("a.pdf", 7, "z"))
    case = Case("Q01", "simple", "dev", "?", False, excerpts, Path("x"))
    ranking = [("a.pdf", 1), ("a.pdf", 1), ("b.pdf", 2), ("a.pdf", 7)]
    assert page_ranks(case, ranking) == [3, None]  # pages 7 and 9 of a.pdf


def test_hit_recall_and_all_pages_differ_on_a_multi_page_case():
    # case 1: 1 of its 4 pages at rank 2; case 2: both pages, at ranks 1 and 6
    scores = summarize([[2, None, None, None], [1, 6]], ks=(5, 10))
    assert scores == {
        "hit@5": 1.0,
        "recall@5": (1 / 4 + 1 / 2) / 2,
        "all@5": 0.0,
        "hit@10": 1.0,
        "recall@10": (1 / 4 + 1) / 2,
        "all@10": 0.5,
        "mrr": (1 / 2 + 1) / 2,
    }


def test_refusal_cases_are_not_scored_on_retrieval():
    found = Case("Q01", "trap", "dev", "?", False, (Excerpt("a.pdf", 1, "x"),), Path("x"))
    refusal = Case("Q02", "medical", "dev", "?", True, (), Path("x"))
    scores = evaluate([found, refusal], lambda question: [("a.pdf", 1)], ks=(5,))
    row = {"n": 1, "hit@5": 1.0, "recall@5": 1.0, "all@5": 1.0, "mrr": 1.0}
    assert scores == {"all": row, "trap": row}


def test_fulltext_ranks_the_page_that_shares_words_with_the_question(conn):
    texts = ["Le silo stocke du grain.", "Couper le gaz avant de ventiler le local."]
    pages = tuple(Page(i, t, "text_layer", 30, 0, 0, 1.0) for i, t in enumerate(texts, start=1))
    store_document(
        conn,
        SourceDocument("GDO-test.pdf", "a" * 64, texts, pages),
        Entry("GDO", "Test", "https://example.org/GDO-test.pdf", "a" * 64),
    )
    assert fulltext(conn)("Faut-il couper le gaz ?")[0] == ("GDO-test.pdf", 2)
    assert fulltext(conn)("???") == []


def test_a_frozen_test_split_cannot_be_frozen_again(tmp_path):
    from secours_tech.bench import main

    cases, stamp = tmp_path / "cases", tmp_path / "frozen.sha256"
    cases.mkdir()
    write_case(cases)
    write_split(load_case(cases / "Q01.toml"), "test")
    main(["--cases", str(cases), "--fingerprint", str(stamp), "freeze"])
    with pytest.raises(SystemExit, match="already frozen"):
        main(["--cases", str(cases), "--fingerprint", str(stamp), "freeze"])


def test_a_document_outside_the_manifest_is_never_retrieved(conn):
    texts = ["Couper le gaz avant de ventiler le local."]
    pages = (Page(1, texts[0], "text_layer", 30, 0, 0, 1.0),)
    store_document(conn, SourceDocument("GDO-old.pdf", "b" * 64, texts, pages))  # local
    assert fulltext(conn)("couper le gaz") == []


def test_chunks_are_merged_back_without_their_repeated_sentence():
    merged = merge_chunks(["Couper le gaz. Ventiler le local.", "Ventiler le local. Baliser."])
    assert merged == "Couper le gaz. Ventiler le local. Baliser."
    assert merge_chunks(["Titre A", "Titre B"]) == "Titre A Titre B"  # no overlap: a space


def test_excerpts_are_checked_against_the_indexed_text_of_their_page():
    def case(case_id, refusal, *excerpts):
        return Case(case_id, "simple", "dev", "?", refusal, excerpts, Path("x"))

    pages = {("a.pdf", 1): "Couper le gaz. Ventiler le local. Baliser la zone."}
    cases = [
        case("Q01", False, Excerpt("a.pdf", 1, "Couper le gaz. […] Baliser la zone.")),
        case("Q02", False, Excerpt("a.pdf", 1, "Couper l'eau.")),
        case("Q03", False, Excerpt("a.pdf", 2, "Sommaire")),
        case("Q04", True, Excerpt("b.pdf", 9, "Hors du corpus indexé.")),
    ]
    found = unindexed_excerpts(cases, lambda document, page: pages.get((document, page)))
    assert [(case_id, reason) for case_id, _, reason in found] == [
        ("Q02", "not verbatim"),
        ("Q03", "page not indexed"),
    ]


def test_an_accent_over_a_dotless_i_compares_as_the_plain_letter():
    assert normalize("maı\u0302trise") == normalize("maîtrise")
    assert normalize("victime ; • vidange") == normalize("victime ; vidange")  # list bullet


def test_a_corrected_test_split_is_refrozen_only_while_unscored(tmp_path):
    from secours_tech.bench import main

    cases, stamp, runs = tmp_path / "cases", tmp_path / "frozen.sha256", tmp_path / "runs"
    cases.mkdir()

    def test_case(text):
        write_case(cases, excerpts=[("doc.pdf", 1, text)])
        write_split(load_case(cases / "Q01.toml"), "test")

    argv = ["--cases", str(cases), "--fingerprint", str(stamp)]
    test_case("Couper le gaz.")
    main([*argv, "freeze"])
    first = stamp.read_text()
    test_case("Couper le gaz du local.")
    main([*argv, "refreeze", "--runs", str(runs), "--reason", "Excerpt re-copied."])
    assert stamp.read_text() != first
    assert "Excerpt re-copied." in (tmp_path / "frozen-test-history.md").read_text()
    runs.mkdir()
    (runs / "20261112T000000Z-test-fulltext-none-none.json").write_text("{}")
    test_case("Couper le gaz.")
    with pytest.raises(SystemExit, match="already scored"):
        main([*argv, "refreeze", "--runs", str(runs), "--reason", "again"])
