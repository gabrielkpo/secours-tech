# Changelog

## [0.2.0](https://github.com/gabrielkpo/secours-tech/compare/v0.1.0...v0.2.0) (2026-10-16)


### Features

* **collect:** official sources manifest, monthly corpus watch and document versions ([#19](https://github.com/gabrielkpo/secours-tech/issues/19)) ([332efc6](https://github.com/gabrielkpo/secours-tech/commit/332efc6fb72d2ed015744308d0be85ff1675174b))
* **db:** load documents and chunks into Postgres with pgvector ([#13](https://github.com/gabrielkpo/secours-tech/issues/13)) ([29a6e35](https://github.com/gabrielkpo/secours-tech/commit/29a6e35df795761c8fd0d9f931c2d03fa00219cb))
* **extract:** read pages with poppler and route each page to text layer, OCR or blank ([#20](https://github.com/gabrielkpo/secours-tech/issues/20)) ([21ba389](https://github.com/gabrielkpo/secours-tech/commit/21ba389bfd97b0bbf5b58e9b1b59d1a42a40eb18))
* ingest PDFs into chunks with document and page ([03aa768](https://github.com/gabrielkpo/secours-tech/commit/03aa768ad1be925c0b38565d926c2a8c214246d4))
* **ingest:** extract PDF pages into chunks that keep document and page ([906fb8d](https://github.com/gabrielkpo/secours-tech/commit/906fb8d6a78e9cc0c60c3809fb551a4bfed47828))
* **ingest:** sentence-aware chunks, header removal, scan warnings and CLI ([#3](https://github.com/gabrielkpo/secours-tech/issues/3)) ([37807c8](https://github.com/gabrielkpo/secours-tech/commit/37807c825ddac81ec11228c2d764ee1082113541))
* **ocr-bench:** index the OCR text of the guides that pass, with a versioned record ([#26](https://github.com/gabrielkpo/secours-tech/issues/26)) ([cbcbdc2](https://github.com/gabrielkpo/secours-tech/commit/cbcbdc2262d46817959edfa6fa30ef60c500c31e))
* **ocr:** read image pages with Tesseract and score OCR reliability against hand transcriptions ([#21](https://github.com/gabrielkpo/secours-tech/issues/21)) ([678e15a](https://github.com/gabrielkpo/secours-tech/commit/678e15a7059c48b092dc60578ffbf5c61f63ea2d))
* **ocr:** read pages without their pictures, at 600 dpi with Sauvola binarisation ([#25](https://github.com/gabrielkpo/secours-tech/issues/25)) ([b58e78d](https://github.com/gabrielkpo/secours-tech/commit/b58e78d036f2608d69f30efbaabdbb9900d22995))
* **ocr:** read the indexed text with Mistral OCR, keep Tesseract as a local checker ([#28](https://github.com/gabrielkpo/secours-tech/issues/28)) ([92f4599](https://github.com/gabrielkpo/secours-tech/commit/92f459908a63853bf2bb81dacdafceef2eecef44))
* **structure:** fall back on printed headings when a guide has no usable contents (D18) ([#29](https://github.com/gabrielkpo/secours-tech/issues/29)) ([c8514ad](https://github.com/gabrielkpo/secours-tech/commit/c8514ad1046495f393de13878bd785ab41e9de60))
* **structure:** read the section tree from the printed table of contents (D18) ([#27](https://github.com/gabrielkpo/secours-tech/issues/27)) ([0f4c44f](https://github.com/gabrielkpo/secours-tech/commit/0f4c44f947839b0e7359a560ee0917024615af57))


### Bug Fixes

* **extract:** send garbled text layers to OCR and mend split accents ([#24](https://github.com/gabrielkpo/secours-tech/issues/24)) ([b3ba4d0](https://github.com/gabrielkpo/secours-tech/commit/b3ba4d012d579b36ca43e545fd04de7ddbbecfa8))
* **ocr-bench:** compare OCR text without its running footer and list bullets ([#22](https://github.com/gabrielkpo/secours-tech/issues/22)) ([1cb2ef4](https://github.com/gabrielkpo/secours-tech/commit/1cb2ef40fe08fb8b74417d37b15f33296bda83e1))
* **ocr-bench:** compare text not typography, and score a held-out validation sample ([#23](https://github.com/gabrielkpo/secours-tech/issues/23)) ([26df6b0](https://github.com/gabrielkpo/secours-tech/commit/26df6b05d28cb154ffc4ea800c4bf73ad4c5d922))


### Documentation

* rewrite the README as a project showcase ([#18](https://github.com/gabrielkpo/secours-tech/issues/18)) ([81030d7](https://github.com/gabrielkpo/secours-tech/commit/81030d7a40e901e0fa57c7cb5135cf229db36af8))
* say that Claude drafts the expected answers and a firefighter checks them ([#31](https://github.com/gabrielkpo/secours-tech/issues/31)) ([79cee36](https://github.com/gabrielkpo/secours-tech/commit/79cee36be7efec85b70be78de404a09cfeca21bb))
