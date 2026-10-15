import hashlib
from pathlib import Path

from secours_tech.collect import Entry, Listing, Manifest, check, parse_listing, register

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
    result = register(m, downloads, corpus)
    assert result.filed == [corpus / "GTO" / "GTO-Engagement-Milieux-Vicies-V2.pdf"]
    assert result.unknown == [downloads / "unknown.pdf"]
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
