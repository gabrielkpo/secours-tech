import json
import os

import pytest

from secours_tech.bench import main
from secours_tech.collect import Entry
from secours_tech.extract import Page
from secours_tech.load import SourceDocument, store_document

GUIDE = ["Le silo stocke du grain.", "Couper le gaz avant de ventiler le local."]
CASE = (
    'id = "{id}"\ncategory = "simple"\nsplit = "{split}"\nquestion = "Couper le gaz ?"\n'
    'expected_answer = "Couper."\nserious_fault_if = "rien"\n\n[[excerpts]]\n'
    'document = "GDO-test.pdf"\npage = 2\ntext = "Couper le gaz"\n'
)


@pytest.fixture
def bench(conn, tmp_path, monkeypatch):
    pages = tuple(Page(i, t, "text_layer", 30, 0, 0, 1.0) for i, t in enumerate(GUIDE, start=1))
    store_document(
        conn,
        SourceDocument("GDO-test.pdf", "a" * 64, GUIDE, pages),
        Entry("GDO", "Test", "https://example.org/GDO-test.pdf", "a" * 64),
    )
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "Q01.toml").write_text(CASE.format(id="Q01", split="dev"), encoding="utf-8")
    (cases / "Q02.toml").write_text(CASE.format(id="Q02", split="test"), encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    runs = tmp_path / "runs"
    argv = ["--cases", str(cases), "--fingerprint", str(tmp_path / "none"), "run"]
    return runs, [*argv, "--runs", str(runs)]


def test_a_run_writes_a_record_of_scores_only(bench, capsys):
    runs, argv = bench
    main(argv)
    (path,) = runs.glob("*.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert path.name.endswith("-dev-fulltext-none-none.json")
    assert data["scores"]["all"]["hit@5"] == 1.0
    assert data["ranks"] == {"Q01": [1]}
    assert data["cases"]["answered"] == 1 and len(data["cases"]["sha256"]) == 64
    assert data["index"]["chunks"] == {"text_layer": 2} and len(data["index"]["sha256"]) == 64
    assert "gaz" not in path.read_text(encoding="utf-8").lower()  # no doctrine text
    main(["report", str(path)])
    assert "| dev | fulltext | - | - | 1 | 1.00 | 1.00 |" in capsys.readouterr().out


def test_the_test_split_is_scored_once_per_setup(bench):
    runs, argv = bench
    main([*argv, "--split", "test", "--final"])
    with pytest.raises(SystemExit, match="already scored with this setup"):
        main([*argv, "--split", "test", "--final"])
    main([*argv, "--split", "test", "--final", "--force"])
    assert len(list(runs.glob("*-test-*.json"))) == 2
