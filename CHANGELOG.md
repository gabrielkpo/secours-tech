# Changelog

## [0.2.0](https://github.com/gabrielkpo/secours-tech/compare/v0.1.0...v0.2.0) (2026-11-13)


### Features

* add a Helm chart for the database, its backup and migrations ([#71](https://github.com/gabrielkpo/secours-tech/issues/71)) ([1c3b2ae](https://github.com/gabrielkpo/secours-tech/commit/1c3b2ae2d8a45bfb7e89479cf8a117801cffe75e))
* add a pipeline Docker image and a local compose stack ([#67](https://github.com/gabrielkpo/secours-tech/issues/67)) ([aa429e8](https://github.com/gabrielkpo/secours-tech/commit/aa429e84a557ee9456e2e3cab1da6ad72d40ae04))
* answer questions with cited passages, checked twice ([#72](https://github.com/gabrielkpo/secours-tech/issues/72)) ([84867d5](https://github.com/gabrielkpo/secours-tech/commit/84867d588099d81268a7f8b9900519d6e4f4db44))
* **bench:** check excerpts against the indexed text, and refreeze an unscored test split ([#52](https://github.com/gabrielkpo/secours-tech/issues/52)) ([87dbdbd](https://github.com/gabrielkpo/secours-tech/commit/87dbdbd7746191de7e18c67d966a0b8ca3b51a5b))
* **bench:** check, split, freeze and score the J2 evaluation set ([#37](https://github.com/gabrielkpo/secours-tech/issues/37)) ([354112a](https://github.com/gabrielkpo/secours-tech/commit/354112a2ffad0ce3420c45eb048ba9d2e1606bc1))
* **bench:** score hit@k, page recall@k and all@k instead of a hit rate named recall ([#50](https://github.com/gabrielkpo/secours-tech/issues/50)) ([dde9116](https://github.com/gabrielkpo/secours-tech/commit/dde9116a73b20bacd60559f9a0835451d515aa72))
* **bench:** write a versioned, scores-only record of every run ([#51](https://github.com/gabrielkpo/secours-tech/issues/51)) ([d1f1cdc](https://github.com/gabrielkpo/secours-tech/commit/d1f1cdc42af730c8e258489dc8a790ac38a48712))
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
* **retrieval:** embed the chunks with mistral-embed and add vector and hybrid search ([#38](https://github.com/gabrielkpo/secours-tech/issues/38)) ([5f4c176](https://github.com/gabrielkpo/secours-tech/commit/5f4c176d57d3a98202193d478be04535eabca4b5))
* **retrieval:** rerank the first 50 chunks with bge-reranker-v2-m3 or BGE-M3 multi-vector ([#41](https://github.com/gabrielkpo/secours-tech/issues/41)) ([eb513c6](https://github.com/gabrielkpo/secours-tech/commit/eb513c6a2b8e61b1a12e8163895ea27838fa5c9d))
* **structure:** fall back on printed headings when a guide has no usable contents (D18) ([#29](https://github.com/gabrielkpo/secours-tech/issues/29)) ([c8514ad](https://github.com/gabrielkpo/secours-tech/commit/c8514ad1046495f393de13878bd785ab41e9de60))
* **structure:** read the section tree from the printed table of contents (D18) ([#27](https://github.com/gabrielkpo/secours-tech/issues/27)) ([0f4c44f](https://github.com/gabrielkpo/secours-tech/commit/0f4c44f947839b0e7359a560ee0917024615af57))


### Bug Fixes

* **collect:** accept a new edition only when it is named, and escape page text in the watch issue ([#61](https://github.com/gabrielkpo/secours-tech/issues/61)) ([939f558](https://github.com/gabrielkpo/secours-tech/commit/939f558b9a8b613ef29b93cecc22f106341c8ed5))
* **extract, ocr:** keep a raised note mark apart from the number it follows ([#46](https://github.com/gabrielkpo/secours-tech/issues/46)) ([4f82349](https://github.com/gabrielkpo/secours-tech/commit/4f823499adeb757ddbcf992460c54b09a9eb5b4a))
* **extract:** send garbled text layers to OCR and mend split accents ([#24](https://github.com/gabrielkpo/secours-tech/issues/24)) ([b3ba4d0](https://github.com/gabrielkpo/secours-tech/commit/b3ba4d012d579b36ca43e545fd04de7ddbbecfa8))
* **ingest:** cut an overlong sentence at a space, never inside a word or a number ([#45](https://github.com/gabrielkpo/secours-tech/issues/45)) ([b4d2a55](https://github.com/gabrielkpo/secours-tech/commit/b4d2a551c703681b1c091d66c8dadcbdf9e77982))
* **ingest:** drop or join chunks too short to quote ([#65](https://github.com/gabrielkpo/secours-tech/issues/65)) ([2100b2c](https://github.com/gabrielkpo/secours-tech/commit/2100b2cf9988ee25f8b9822a2613142a50e9f79f))
* **ingest:** strip running headers and footers by position, not every repeated line ([#44](https://github.com/gabrielkpo/secours-tech/issues/44)) ([e531e07](https://github.com/gabrielkpo/secours-tech/commit/e531e07401e190f29715221c3b42a3d0a668b4f4))
* **load:** keep a republished guide current, and fail when a manifest guide has none ([#48](https://github.com/gabrielkpo/secours-tech/issues/48)) ([06fe56f](https://github.com/gabrielkpo/secours-tech/commit/06fe56fa087326c3ed163d836e5b68db48295d69))
* **load:** refuse PDFs outside the manifest, and retrieve official guides only ([#47](https://github.com/gabrielkpo/secours-tech/issues/47)) ([09dd958](https://github.com/gabrielkpo/secours-tech/commit/09dd958ba28a307d2b085aea6b87779058d1459c))
* **load:** withdraw OCR chunks filed under the loader's sections when a guide is re-extracted ([#63](https://github.com/gabrielkpo/secours-tech/issues/63)) ([2098087](https://github.com/gabrielkpo/secours-tech/commit/2098087dcb885b793dc992b0242f415076739b98))
* **mistral, collect:** keep the API key from following a redirect, validate the manifest ([#58](https://github.com/gabrielkpo/secours-tech/issues/58)) ([d290fe8](https://github.com/gabrielkpo/secours-tech/commit/d290fe8024c202cb4d56a4553a3ae4ef505fd452))
* **ocr-bench:** compare OCR text without its running footer and list bullets ([#22](https://github.com/gabrielkpo/secours-tech/issues/22)) ([1cb2ef4](https://github.com/gabrielkpo/secours-tech/commit/1cb2ef40fe08fb8b74417d37b15f33296bda83e1))
* **ocr-bench:** compare text not typography, and score a held-out validation sample ([#23](https://github.com/gabrielkpo/secours-tech/issues/23)) ([26df6b0](https://github.com/gabrielkpo/secours-tech/commit/26df6b05d28cb154ffc4ea800c4bf73ad4c5d922))
* **ocr, embed:** dry runs and limits for paid runs, and an OCR run that withdraws only what it rereads ([#59](https://github.com/gabrielkpo/secours-tech/issues/59)) ([339d153](https://github.com/gabrielkpo/secours-tech/commit/339d153edf0191912cc3d4a69be8fd0784fa30cf))
* run every external tool with a timeout and UTF-8 output, through one helper ([#60](https://github.com/gabrielkpo/secours-tech/issues/60)) ([030a182](https://github.com/gabrielkpo/secours-tech/commit/030a1824ad94dc6cd16d983682656fc7a788b2fd))


### Documentation

* add the multilingual-e5-base retrieval scores ([#40](https://github.com/gabrielkpo/secours-tech/issues/40)) ([1138ef9](https://github.com/gabrielkpo/secours-tech/commit/1138ef978aeed68aac185c6daa4941119b183791))
* rewrite the README as a project showcase ([#18](https://github.com/gabrielkpo/secours-tech/issues/18)) ([81030d7](https://github.com/gabrielkpo/secours-tech/commit/81030d7a40e901e0fa57c7cb5135cf229db36af8))
* rewrite the README on the code as it is, and publish the scores from run records ([#64](https://github.com/gabrielkpo/secours-tech/issues/64)) ([c50eb64](https://github.com/gabrielkpo/secours-tech/commit/c50eb64357b59b1281ac4d3b329ec89a2a139687))
* say that Claude drafts the expected answers and a firefighter checks them ([#31](https://github.com/gabrielkpo/secours-tech/issues/31)) ([79cee36](https://github.com/gabrielkpo/secours-tech/commit/79cee36be7efec85b70be78de404a09cfeca21bb))
* score the index again after merging short fragments ([#66](https://github.com/gabrielkpo/secours-tech/issues/66)) ([cebd156](https://github.com/gabrielkpo/secours-tech/commit/cebd1560a9781e2b296a8342b3abe6f59f1fd1c8))
* score the index built with the Docker image ([#69](https://github.com/gabrielkpo/secours-tech/issues/69)) ([79d8c00](https://github.com/gabrielkpo/secours-tech/commit/79d8c007579741b2aff33ae16ea80ca0e21f9ba8))
