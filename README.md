# SecoursTech

Assistant that answers operational questions from the official French firefighter doctrine
(GDO, GTO, SSUAP), and **always cites the document and page** it relied on.

> Status: work in progress (v3, rebuilt from scratch). See `docs/architecture.md`.

## Why a rebuild
v2 called the LLM straight from the browser, which exposed the API key in the JS bundle and
shipped 170+ MB of PDFs inside Git. v3 keeps the key on the server, rate-limits the API and
downloads the documents at build time.
