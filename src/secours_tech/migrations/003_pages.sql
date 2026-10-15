-- One row per PDF page: how its text is obtained, and the measures behind that choice. It
-- drives OCR (route = 'ocr') and the coverage metric (pages with text / pages not blank).
CREATE TABLE pages (
    document_id bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    page        integer NOT NULL CHECK (page > 0),
    route       text NOT NULL CHECK (route IN ('text_layer', 'ocr', 'blank')),
    letters     integer NOT NULL CHECK (letters >= 0),
    text_masks  integer NOT NULL CHECK (text_masks >= 0),  -- lines of text drawn as images
    images      integer NOT NULL CHECK (images >= 0),
    quality     real NOT NULL CHECK (quality BETWEEN 0 AND 1),
    PRIMARY KEY (document_id, page)
);
