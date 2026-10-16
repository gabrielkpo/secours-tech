-- Where a section comes from: the printed table of contents, the headings of the text layer
-- (fallback of D18 when the contents is missing or garbled), or the headings of the OCR text.
-- OCR sections are rebuilt by `ocr_bench index`; the other two by the loader.
ALTER TABLE sections ADD COLUMN origin text NOT NULL DEFAULT 'contents'
    CHECK (origin IN ('contents', 'headings', 'ocr'));
