import unittest

from text_search import Match, TextSearch


class TextSearchTests(unittest.TestCase):
    def test_counts_and_navigation_wrap_in_both_directions(self):
        search = TextSearch("cat Cat cat", "cat")
        self.assertEqual(3, search.total)
        self.assertEqual(Match(0, 3), search.next())
        self.assertEqual(1, search.ordinal)
        self.assertEqual(Match(4, 7), search.next())
        self.assertEqual(2, search.ordinal)
        self.assertEqual(Match(0, 3), search.previous())
        self.assertEqual(Match(8, 11), search.previous())
        self.assertEqual(3, search.ordinal)
        self.assertEqual(Match(0, 3), search.next())

    def test_case_sensitivity_and_literal_characters(self):
        self.assertEqual(1, TextSearch("cat Cat", "Cat", case_sensitive=True).total)
        self.assertEqual(2, TextSearch("[a] [a] aaa", "[a]").total)
        self.assertEqual(2, TextSearch("a.b a.b acb", "a.b").total)

    def test_empty_and_missing_term(self):
        for term in ("", "absent"):
            search = TextSearch("content", term)
            self.assertEqual(0, search.total)
            self.assertIsNone(search.next())
            self.assertIsNone(search.previous())
            self.assertEqual(("content", 0), search.replacement("new", True))

    def test_replace_one_respects_current_match(self):
        search = TextSearch("cat CAT cat", "cat")
        search.next()
        search.next()
        self.assertEqual(("cat dog cat", 1), search.replacement("dog"))

    def test_replace_all_is_literal_and_nonrecursive(self):
        search = TextSearch("A a a", "a")
        self.assertEqual((r"\1 \1 \1", 3), search.replacement(r"\1", True))
        self.assertEqual(("aa aa aa", 3), search.replacement("aa", True))
        self.assertEqual(("  ", 3), search.replacement("", True))

    def test_unicode_multiline_and_index_mapping(self):
        search = TextSearch("😀中文\n中文", "中文")
        self.assertEqual(2, search.total)
        self.assertEqual(Match(1, 3), search.next())
        self.assertEqual("1.0+1c", search.tk_index(1))
        self.assertEqual(Match(4, 6), search.next())
        self.assertEqual(1, TextSearch("a\nb\na\nb", "b\na").total)

    def test_highlight_limit_does_not_limit_count_or_navigation(self):
        search = TextSearch("a" * 3000, "a")
        self.assertEqual(3000, search.total)
        self.assertEqual(2000, len(list(search.ranges())))
        self.assertEqual(Match(2999, 3000), search.select_after(2999))
        self.assertEqual(3000, search.ordinal)
        self.assertEqual(Match(0, 1), search.next())

    def test_matches_do_not_overlap(self):
        search = TextSearch("aaaaa", "aa")
        self.assertEqual(2, search.total)
        self.assertEqual(("bba", 2), search.replacement("b", True))
