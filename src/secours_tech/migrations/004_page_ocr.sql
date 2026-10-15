-- OCR output of the pages routed to 'ocr'. Stored, not indexed: a guide's OCR text becomes
-- chunks only once its sample passes the reliability check (100 % of numbers, 99 % of characters).
ALTER TABLE pages
    ADD COLUMN ocr_text text,
    ADD COLUMN ocr_confidence real CHECK (ocr_confidence BETWEEN 0 AND 100),
    ADD COLUMN ocr_engine text,
    ADD COLUMN ocr_at timestamptz;
