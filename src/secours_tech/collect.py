"""Watch the official doctrine pages and file the guides downloaded by hand.

The ministry's site answers 403 to automated clients, including an honestly identified bot.
This module does not disguise itself as a browser: when a page is blocked, `check` says so,
a person downloads the PDFs in a browser, and `register` files them. Everything after the
download (fingerprints, versions, retired guides, indexing) is automatic.
"""

import argparse
import hashlib
import os
import re
import shutil
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from importlib.metadata import version
from pathlib import Path

USER_AGENT = (
    f"secours-tech-corpus-watch/{version('secours-tech')}"
    " (+https://github.com/gabrielkpo/secours-tech)"
)
OFFICIAL = "https://www.securite-civile.interieur.gouv.fr/"
FAMILIES = ("GDO", "GTO", "PSE", "SSUAP")
MAX_PAGE = 5_000_000  # bytes; a listing page is a few hundred kilobytes
_SHA256 = re.compile(r"[0-9a-f]{64}")
MANIFEST = Path("corpus/sources.toml")


@dataclass(frozen=True)
class Listing:
    """An official page that lists downloadable guides."""

    family: str
    url: str
    include: str | None = None  # keep only links whose title or URL contains this text


@dataclass(frozen=True)
class Entry:
    """A guide we index: where it is published and the fingerprint of the file we hold."""

    family: str
    title: str
    url: str
    sha256: str

    @property
    def filename(self) -> str:
        return urllib.parse.unquote(self.url.rsplit("/", 1)[-1])


def _check_entry(path: Path, item: dict) -> None:
    """A manifest entry points to the ministry's https site, names a known family and, for a
    document, pins a well-formed SHA-256: anything else is refused before it is fetched or
    loaded."""
    url, family, sha = item.get("url", ""), item.get("family"), item.get("sha256")
    if not url.startswith(OFFICIAL):
        raise ValueError(f"{path}: {url!r} is not on {OFFICIAL}")
    if family not in FAMILIES:
        raise ValueError(f"{path}: unknown family {family!r} for {url}")
    if "title" in item and not (isinstance(sha, str) and _SHA256.fullmatch(sha)):
        raise ValueError(f"{path}: malformed sha256 for {url}")


@dataclass
class Manifest:
    listings: list[Listing]
    documents: list[Entry]
    header: str = ""

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        for item in [*data.get("listing", []), *data.get("document", [])]:
            _check_entry(path, item)
        header = []
        for line in text.splitlines():  # the leading comment block is kept on dump
            if not line.startswith("#"):
                break
            header.append(line + "\n")
        return cls(
            [Listing(**item) for item in data.get("listing", [])],
            [Entry(**item) for item in data.get("document", [])],
            "".join(header),
        )

    def dump(self, path: Path) -> None:
        def q(value: str) -> str:
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

        parts = [self.header.rstrip("\n") + "\n"] if self.header else []
        for item in self.listings:
            lines = ["[[listing]]", f"family = {q(item.family)}", f"url = {q(item.url)}"]
            if item.include:
                lines.append(f"include = {q(item.include)}")
            parts.append("\n".join(lines) + "\n")
        for doc in self.documents:
            parts.append(
                "\n".join(
                    [
                        "[[document]]",
                        f"family = {q(doc.family)}",
                        f"title = {q(doc.title)}",
                        f"url = {q(doc.url)}",
                        f"sha256 = {q(doc.sha256)}",
                    ]
                )
                + "\n"
            )
        path.write_text("\n".join(parts), encoding="utf-8")


class _PdfLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href") or ""
        if tag == "a" and href.lower().split("?")[0].endswith(".pdf"):
            self._href, self._text = href, []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def parse_listing(html: str, page_url: str, include: str | None = None) -> list[tuple[str, str]]:
    """(title, absolute URL) of every PDF link on an official page."""
    parser = _PdfLinks()
    parser.feed(html)
    found = []
    for href, text in parser.links:
        title = text.removeprefix("Télécharger").strip()
        url = urllib.parse.urljoin(page_url, href)
        if include and include not in title and include not in url:
            continue
        found.append((title, url))
    return found


class _OfficialOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith(OFFICIAL):
            return None  # a redirect off the ministry's site is reported, not followed
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_OfficialOnly)


def fetch(url: str) -> tuple[int, str | None]:
    """HTTP status and body of an official https page. The bot says who it is; a 403 is
    reported, not worked around; a page larger than MAX_PAGE is reported as unreadable."""
    if not url.startswith(OFFICIAL):
        raise ValueError(f"not an official page: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        # the URL was just checked, and redirects stay on the ministry's https site
        with _OPENER.open(request, timeout=30) as response:
            body = response.read(MAX_PAGE + 1)
            if len(body) > MAX_PAGE:
                return 0, None
            return response.status, body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, None
    except OSError:  # URLError, timeouts, connection resets
        return 0, None  # unreachable: reported like a blocked page, so a person checks it


@dataclass
class Report:
    blocked: list[tuple[str, int]] = field(default_factory=list)
    new: list[tuple[str, str, str]] = field(default_factory=list)  # family, title, url
    removed: list[Entry] = field(default_factory=list)

    @property
    def needs_action(self) -> bool:
        return bool(self.blocked or self.new or self.removed)

    def markdown(self) -> str:
        if not self.needs_action:
            return "Every official page was read and the corpus matches it. Nothing to do.\n"
        out = []
        if self.blocked:
            out.append("### Pages the bot could not read")
            out.append("Open each page in a browser and compare it with `corpus/sources.toml`:")
            out += [
                f"- [ ] {url} ({f'HTTP {status}' if status else 'unreachable'})"
                for url, status in self.blocked
            ]
        official = [(f, t, u) for f, t, u in self.new if u.startswith(OFFICIAL)]
        elsewhere = [(f, t, u) for f, t, u in self.new if not u.startswith(OFFICIAL)]
        if official:
            out.append("### Guides published but not indexed")
            out += [
                f"- [ ] {family}: [{_code(title)}]({_link(url)})" for family, title, url in official
            ]
        if elsewhere:
            out.append("### Links to another site (not guides to download: check the page)")
            out += [f"- {family}: {_code(title)} {_code(url)}" for family, title, url in elsewhere]
        if self.removed:
            out.append("### Indexed guides no longer published")
            out += [f"- [ ] {doc.family}: {doc.title} ({doc.url})" for doc in self.removed]
        out.append(
            "\nDownload the PDFs in a browser, then run "
            "`uv run python -m secours_tech.collect register <folder>` and open a PR."
        )
        return "\n".join(out) + "\n"


def _code(text: str) -> str:
    """Text read from a web page, shown as inline code: no Markdown, link or @mention in it is
    rendered in the issue."""
    return "`" + " ".join(text.replace("`", "'").split()) + "`"


def _link(url: str) -> str:
    """A link destination read from a page, percent-encoded outside the characters a URL needs:
    a ")", a space or a "[" in it cannot close the link and add Markdown after it."""
    return urllib.parse.quote(url, safe=":/?=&%#~.-_")


def check(manifest: Manifest, fetcher=fetch) -> Report:
    """Compare the official pages with the manifest."""
    report = Report()
    known = {doc.url for doc in manifest.documents}
    seen: set[str] = set()
    for listing in manifest.listings:
        status, html = fetcher(listing.url)
        if status != 200 or html is None:
            report.blocked.append((listing.url, status))
            continue
        for title, url in parse_listing(html, listing.url, listing.include):
            seen.add(url)
            if url not in known:
                report.new.append((listing.family, title, url))
    # a guide can only be called "removed" when every page was read
    if not report.blocked:
        report.removed = [doc for doc in manifest.documents if doc.url not in seen]
    return report


@dataclass
class Registered:
    filed: list[Path] = field(default_factory=list)
    new_versions: list[Entry] = field(default_factory=list)
    refused: list[tuple[Path, Entry, str]] = field(default_factory=list)  # file, entry, new SHA
    unknown: list[Path] = field(default_factory=list)


def describe(pdf: Path) -> str:
    """Page count, title and modification date of a PDF, to tell two editions apart."""
    import pikepdf

    try:
        with pikepdf.open(pdf) as document:
            info = document.docinfo
            title = str(info.get("/Title", "")) or "no title"
            modified = str(info.get("/ModDate", "")) or "no date"
            return f"{len(document.pages)} pages, title {title!r}, modified {modified}"
    except pikepdf.PdfError as error:  # a truncated download is the case this is for
        return f"unreadable PDF ({error})"


def register(
    manifest: Manifest, downloads: Path, corpus: Path, accept: frozenset[str] = frozenset()
) -> Registered:
    """File PDFs downloaded by hand under corpus/<family>/, matched to the manifest by name.

    A file whose fingerprint differs from the manifest is a new version only when its name is in
    `accept`: the manifest entry is then updated, and the loader retires the previous version.
    Otherwise it is refused, not filed (a truncated or wrong download must not replace a guide
    on the strength of its name). Files not in the manifest are listed for a person to describe
    (family, title, URL); they are not filed.
    """
    result = Registered()
    by_name = {doc.filename: doc for doc in manifest.documents}
    for pdf in sorted(downloads.glob("*.pdf")):
        doc = by_name.get(pdf.name)
        if doc is None:
            result.unknown.append(pdf)
            continue
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if sha != doc.sha256 and pdf.name not in accept:
            result.refused.append((pdf, doc, sha))
            continue
        if sha != doc.sha256:
            updated = replace(doc, sha256=sha)
            manifest.documents[manifest.documents.index(doc)] = updated
            result.new_versions.append(updated)
        target = corpus / doc.family / pdf.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, target)
        result.filed.append(target)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    sub = parser.add_subparsers(dest="command", required=True)
    check_cmd = sub.add_parser("check", help="compare the official pages with the manifest")
    check_cmd.add_argument("--report", type=Path, help="also write the report to this file")
    register_cmd = sub.add_parser("register", help="file PDFs downloaded by hand")
    register_cmd.add_argument("downloads", type=Path)
    register_cmd.add_argument("--corpus", type=Path, default=Path("data/raw"))
    register_cmd.add_argument(
        "--accept-new-version",
        action="append",
        default=[],
        metavar="FILENAME",
        help="file name whose new fingerprint is accepted as a new edition (repeatable)",
    )
    args = parser.parse_args(argv)

    manifest = Manifest.load(args.manifest)
    if args.command == "check":
        report = check(manifest)
        text = report.markdown()
        sys.stdout.write(text)
        if args.report:
            args.report.write_text(text, encoding="utf-8")
        if output := os.environ.get("GITHUB_OUTPUT"):
            with Path(output).open("a", encoding="utf-8") as fh:
                fh.write(f"action={'true' if report.needs_action else 'false'}\n")
        return

    result = register(manifest, args.downloads, args.corpus, frozenset(args.accept_new_version))
    if result.new_versions:
        manifest.dump(args.manifest)
    for path in result.filed:
        print(f"filed {path}")
    for doc in result.new_versions:
        print(f"new version of {doc.family} '{doc.title}': manifest updated, open a PR")
    for path in result.unknown:
        print(f"not in the manifest, add an entry first: {path.name}")
    for path, doc, sha in result.refused:
        old = args.corpus / doc.family / path.name
        print(f"refused, the fingerprint changed: {path.name}")
        print(f"  manifest {doc.sha256}" + (f"  {describe(old)}" if old.exists() else ""))
        print(f"  download {sha}  {describe(path)}")
        print(f"  a new edition? check it, then: register --accept-new-version '{path.name}'")
    if result.refused:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
