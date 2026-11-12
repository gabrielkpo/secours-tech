# Frozen test split: changes before it was scored

- 2026-11-12: 44d44fe1ba3f -> f19d2b21841e, 9 test cases. 18 excerpts of one case re-copied from the indexed text, where the PDF stores accents apart and plain extraction had left spaces inside words; 1 excerpt removed because it pointed to a table-of-contents page, which is never indexed. Questions, expected answers, categories and splits are unchanged.
- 2026-11-12: f19d2b21841e -> b52fc952b5d1, 9 test cases. 6 excerpts of one case re-copied from the text indexed with the Docker image: the earlier extraction (Homebrew poppler 26.04) had glued words together, the same characters now carry their spaces. Questions, expected answers, categories and splits are unchanged.
