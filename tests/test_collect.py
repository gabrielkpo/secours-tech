import hashlib
import urllib.request
from pathlib import Path

import pytest

from secours_tech.collect import Entry, Listing, Manifest, check, fetch, parse_listing, register

PAGE = "https://www.securite-civile.interieur.gouv.fr/documentation/gto.html"
BASE = "https://www.securite-civile.interieur.gouv.fr/sites/securitecivile/files/medias/documents"

# Same markup as the official page on 2026-10-15 (one link per guide).
HTML = """
<ul><li>
  <a id="link-3647" download="GTO-Engagement-Milieux-Vicies-V2.pdf"
     href="/sites/securitecivile/files/medias/documents/2026-07/GTO-Engagement-Milieux-Vicies-V2.pdf"
     class="fr-link fr-link--downloads">
    <span class="fr-sr-only">Télécharger</span>
      GTO - Engagement en milieu vicié
    <span class="fr-link__icon" aria-hidden="true"></span>
  </a>
</li><li>
  <a href="/sites/securitecivile/files/medias/documents/2026-07/GTO-Ventilation-Operationnelle.pdf">
    <span class="fr-sr-only">Télécharger</span> GTO - Ventilation opérationnelle</a>
</li><li><a href="/documentation/other.html">Not a PDF</a></li></ul>
"""

VICIE = Entry(
    "GTO",
    "Engagement en milieu vicié",
    f"{BASE}/2026-07/GTO-Engagement-Milieux-Vicies-V2.pdf",
    "a" * 64,
)
VENTILATION = Entry(
    "GTO", "Ventilation", f"{BASE}/2026-07/GTO-Ventilation-Operationnelle.pdf", "b" * 64
)


def manifest(*docs: Entry) -> Manifest:
    return Manifest([Listing("GTO", PAGE)], list(docs))


def test_listing_links_are_absolute_with_readable_titles():
    links = parse_listing(HTML, PAGE)
    assert links == [
        ("GTO - Engagement en milieu vicié", VICIE.url),
        ("GTO - Ventilation opérationnelle", VENTILATION.url),
    ]


def test_include_keeps_only_matching_links():
    assert [title for title, _ in parse_listing(HTML, PAGE, include="Ventilation")] == [
        "GTO - Ventilation opérationnelle"
    ]


def test_blocked_page_is_reported_and_nothing_is_called_removed():
    report = check(manifest(VICIE), fetcher=lambda url: (403, None))
    assert report.blocked == [(PAGE, 403)]
    assert report.removed == []
    assert report.needs_action
    assert "could not read" in report.markdown()


def test_new_and_removed_guides_are_reported():
    old = Entry("GTO", "Withdrawn guide", f"{BASE}/2019/GTO-Old.pdf", "c" * 64)
    report = check(manifest(VICIE, old), fetcher=lambda url: (200, HTML))
    assert report.new == [("GTO", "GTO - Ventilation opérationnelle", VENTILATION.url)]
    assert report.removed == [old]


def test_matching_corpus_needs_no_action():
    report = check(manifest(VICIE, VENTILATION), fetcher=lambda url: (200, HTML))
    assert not report.needs_action


def test_register_files_known_pdfs_and_detects_a_new_version(tmp_path: Path):
    downloads, corpus = tmp_path / "downloads", tmp_path / "corpus"
    downloads.mkdir()
    (downloads / "GTO-Engagement-Milieux-Vicies-V2.pdf").write_bytes(b"new edition")
    (downloads / "unknown.pdf").write_bytes(b"?")
    m = manifest(VICIE)
    refused = register(m, downloads, corpus)  # a changed fingerprint is not taken on its name
    assert refused.filed == [] and [path.name for path, _, _ in refused.refused] == [
        "GTO-Engagement-Milieux-Vicies-V2.pdf"
    ]
    assert refused.unknown == [downloads / "unknown.pdf"]
    assert m.documents[0].sha256 == VICIE.sha256
    result = register(m, downloads, corpus, frozenset({"GTO-Engagement-Milieux-Vicies-V2.pdf"}))
    assert result.filed == [corpus / "GTO" / "GTO-Engagement-Milieux-Vicies-V2.pdf"]
    assert m.documents[0].sha256 == hashlib.sha256(b"new edition").hexdigest()


def test_manifest_round_trip(tmp_path: Path):
    path = tmp_path / "sources.toml"
    original = Manifest([Listing("PSE", PAGE, include="PSE")], [VICIE], "# header\n")
    original.dump(path)
    loaded = Manifest.load(path)
    assert loaded.listings == original.listings
    assert loaded.documents == original.documents
    assert path.read_text(encoding="utf-8").startswith("# header\n")


def test_repository_manifest_is_valid():
    m = Manifest.load(Path(__file__).parent.parent / "corpus" / "sources.toml")
    assert len(m.documents) == len({doc.url for doc in m.documents}) == 28
    assert {doc.family for doc in m.documents} == {"GDO", "GTO", "PSE", "SSUAP"}


def test_unreachable_page_is_reported_for_a_manual_check():
    report = check(manifest(VICIE), fetcher=lambda url: (0, None))
    assert report.blocked == [(PAGE, 0)]
    assert "unreachable" in report.markdown()


@pytest.mark.parametrize(
    ("url", "family", "sha", "error"),
    [
        ("http://www.securite-civile.interieur.gouv.fr/x.pdf", "GDO", "a" * 64, "is not on"),
        ("file:///etc/passwd", "GDO", "a" * 64, "is not on"),
        (f"{BASE}/x.pdf", "OTHER", "a" * 64, "unknown family"),
        (f"{BASE}/x.pdf", "GDO", "not-a-hash", "malformed sha256"),
    ],
)
def test_a_manifest_entry_off_the_official_site_or_malformed_is_refused(
    tmp_path: Path, url, family, sha, error
):
    path = tmp_path / "sources.toml"
    path.write_text(
        f'[[document]]\nfamily = "{family}"\ntitle = "T"\nurl = "{url}"\nsha256 = "{sha}"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=error):
        Manifest.load(path)


def test_fetch_refuses_a_page_off_the_official_site():
    with pytest.raises(ValueError, match="not an official page"):
        fetch("https://example.org/listing.html")


def test_a_redirect_off_the_official_site_is_not_followed():
    from secours_tech.collect import _OfficialOnly

    handler = _OfficialOnly()
    request = urllib.request.Request(PAGE)
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://example.org/") is None
    assert handler.redirect_request(request, None, 302, "Found", {}, f"{BASE}/x.pdf") is not None


def test_text_read_from_the_page_is_shown_as_code_and_off_site_links_apart():
    hostile = (
        '<a href="/files/GDO-New.pdf">GDO [x](https://evil.example) @someone</a>'
        '<a href="https://evil.example/GDO.pdf">Télécharger GDO pirate</a>'
    )
    report = check(manifest(), fetcher=lambda url: (200, hostile))
    text = report.markdown()
    assert "`GDO [x](https://evil.example) @someone`" in text
    assert "Links to another site" in text and "`https://evil.example/GDO.pdf`" in text
    assert "](https://evil.example" not in text.replace("`GDO [x](https://evil.example)", "")

    breakout = '<a href="/x) @octocat [free money](https://evil.example/p.pdf">GDO</a>'
    text = check(manifest(), fetcher=lambda url: (200, breakout)).markdown()
    (line,) = [line for line in text.splitlines() if line.startswith("- [ ] GTO")]
    assert line.endswith("%29%20%40octocat%20%5Bfree%20money%5D%28https://evil.example/p.pdf)")
    assert "@octocat" not in text and "[free money]" not in text


def test_register_explains_a_refused_new_version(tmp_path: Path, make_pdf, capsys):
    from secours_tech.collect import main

    pdf = make_pdf(["Nouvelle édition."], name="GTO-Engagement-Milieux-Vicies-V2.pdf")
    path = tmp_path / "sources.toml"
    manifest(VICIE).dump(path)
    argv = ["--manifest", str(path), "register", str(pdf.parent), "--corpus", str(tmp_path / "c")]
    with pytest.raises(SystemExit):
        main(argv)
    out = capsys.readouterr().out
    assert "refused, the fingerprint changed" in out and "1 pages" in out
    assert "--accept-new-version" in out
    main([*argv, "--accept-new-version", pdf.name])
    assert Manifest.load(path).documents[0].sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
