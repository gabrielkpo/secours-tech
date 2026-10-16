from secours_tech.structure import (
    Entry,
    Section,
    _levels,
    _toc_lines,
    detect_headings,
    locate,
    section_at,
    split_page,
)

TOC = """\
                                    Sommaire
CHAPITRE 1 - Les dangers et les risques ...................................... 13
1. L’accident électrique de personnes .......................................... 13
       1.1. Les causes ............................................................. 14
          1.1.1. Les seuils de danger ............................................. 15
       1.2. La tension de pas ______________________________________________ 15
            a.   Le contact direct ................................................ 15
            b.   Le contact indirect .............................................. 16
2. Les spécificités liées aux panneaux photovoltaïques                         17
"""


def test_contents_lines_keep_title_and_printed_page():
    lines = _toc_lines(TOC)
    assert [(label, page) for _, label, page in lines][:3] == [
        ("CHAPITRE 1 - Les dangers et les risques", 13),
        ("1. L’accident électrique de personnes", 13),
        ("1.1. Les causes", 14),
    ]
    assert lines[-1][1:] == ("2. Les spécificités liées aux panneaux photovoltaïques", 17)


def test_a_footnote_followed_by_a_number_is_not_a_contents_line():
    assert _toc_lines("principaux composants du bois.\n5\n") == []


def test_levels_come_from_chapter_numbering_and_letters():
    assert _levels(_toc_lines(TOC)) == [1, 2, 3, 4, 3, 4, 4, 2]


def test_headings_are_placed_on_the_pdf_page_despite_a_shift():
    entries = [
        Entry("CHAPITRE 1 - Les dangers", 13, 1, 7),
        Entry("1.1. Les causes", 14, 2, 7),
    ]
    pages = [""] * 20
    pages[14] = "CHAPITRE 1 - Les dangers\nIntroduction."  # printed 13 is PDF page 15
    pages[15] = "1.1. Les causes\nTexte."
    sections = locate(entries, pages, toc_pages={7})
    assert [(s.title, s.page, s.path) for s in sections] == [
        ("CHAPITRE 1 - Les dangers", 15, ("CHAPITRE 1 - Les dangers",)),
        ("1.1. Les causes", 16, ("CHAPITRE 1 - Les dangers", "1.1. Les causes")),
    ]


def test_a_contents_whose_headings_are_not_found_gives_no_structure():
    entries = [Entry("Titre introuvable", 3, 1, 1), Entry("Autre titre absent", 4, 1, 1)]
    assert locate(entries, ["texte sans rapport"] * 10, toc_pages={1}) == []


def test_a_page_is_cut_where_its_headings_start():
    sections = [
        Section("VI-2. La gîte", 3, 112, ("Chapitre 3", "VI-2. La gîte")),
        Section("VI-3. Carènes liquides", 3, 113, ("Chapitre 3", "VI-3. Carènes liquides")),
        Section("VI-4. Autres facteurs", 3, 113, ("Chapitre 3", "VI-4. Autres facteurs")),
    ]
    text = "fin de la gîte.\nVI-3. Carènes liquides\nUne masse d'eau.\nVI-4. Autres facteurs\nLa stabilité."
    assert split_page(text, 113, sections) == [
        (0, "fin de la gîte.\n"),
        (1, "VI-3. Carènes liquides\nUne masse d'eau.\n"),
        (2, "VI-4. Autres facteurs\nLa stabilité."),
    ]


def test_section_at_takes_the_last_section_started():
    assert section_at([5, 9, 9, 12], 10) == 2
    assert section_at([5, 9], 4) is None


FICHES = [
    (
        "Sommaire\nVEN-STR-PRO-1 Recloisonnement\nVEN-STR-PRO-2 Désenfumage\n"
        "VEN-STR-ATT-1 Ouvrant\nVEN-STR-ATT-2 Ventilateur"
    ),
    (
        "VEN-STR-PRO\nVENTILER POUR PROTEGER\nVEN-STR-PRO-1 Recloisonnement du feu\n"
        "1. Objectif\nLimiter la propagation des fumées."
    ),
    (
        "2.\nMise en œuvre\nLes étapes sont les suivantes :\n"
        "3. Fermer les portes, puis contrôler les volumes voisins avant toute autre action;"
    ),
]


def test_headings_give_a_structure_when_there_is_no_table_of_contents():
    sections, listing = detect_headings(FICHES)
    assert listing == {1}  # a page listing fiche codes is a contents page, not read
    assert [(s.title, s.level, s.page) for s in sections] == [
        ("VEN-STR-PRO VENTILER POUR PROTEGER", 1, 2),
        ("VEN-STR-PRO-1 Recloisonnement du feu", 2, 2),
        ("1. Objectif", 3, 2),
        ("2. Mise en œuvre", 3, 3),  # a number alone on its line takes the next line as title
    ]  # a sentence ending in ";" is a list item, not a heading
    assert sections[-1].path == (
        "VEN-STR-PRO VENTILER POUR PROTEGER",
        "VEN-STR-PRO-1 Recloisonnement du feu",
        "2. Mise en œuvre",
    )


def test_pages_to_skip_are_not_read_for_headings():
    sections, _ = detect_headings(FICHES, skip={2})
    assert [s.page for s in sections] == [3]


def test_a_printed_contents_page_in_ocr_text_gives_no_heading():
    contents = (
        "Sommaire\nCHAPITRE 1 - Connaissances générales 11\n1. Les biens culturels 11\n"
        "1.1. Définition...11\n1.2. Les différents biens culturels...12\n"
        "2. La vulnérabilité du patrimoine 14"
    )
    body = "CHAPITRE 1 - Connaissances générales\n1. Les biens culturels\nUn bien culturel est..."
    sections, listing = detect_headings([contents, body])
    assert listing == {1}
    assert [(s.title, s.page) for s in sections] == [
        ("CHAPITRE 1 - Connaissances générales", 2),
        ("1. Les biens culturels", 2),
    ]
