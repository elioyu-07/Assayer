import unittest

from assayer_platform import PlatformContractError, StagedResultDocument


class StagedResultDocumentTest(unittest.TestCase):
    def test_nested_arrays_are_paged_without_repeating_previous_items(self):
        source = {
            "status": "completed",
            "summary": {
                "count": 12,
                "findings": [
                    {"id": f"finding-{index}", "evidence": [f"evidence-{index}"]}
                    for index in range(12)
                ],
            },
        }
        document = StagedResultDocument(source)
        reference = document.overview["summary"]["findings"]
        self.assertEqual(reference["delivery"], "paged")
        self.assertEqual(reference["itemCount"], 12)

        first = document.page(reference["sectionId"], page_size=5)
        second = document.page(
            reference["sectionId"], cursor=first["nextCursor"], page_size=5,
        )
        last = document.page(
            reference["sectionId"], cursor=second["nextCursor"], page_size=5,
        )
        self.assertEqual({key: first["page"][key] for key in ("start", "count", "total")}, {"start": 0, "count": 5, "total": 12})
        self.assertEqual({key: second["page"][key] for key in ("start", "count", "total")}, {"start": 5, "count": 5, "total": 12})
        self.assertEqual({key: last["page"][key] for key in ("start", "count", "total")}, {"start": 10, "count": 2, "total": 12})
        self.assertIsNone(last["nextCursor"])
        ids = [item["id"] for page in (first, second, last) for item in page["items"]]
        self.assertEqual(ids, [f"finding-{index}" for index in range(12)])

    def test_long_text_is_losslessly_chunked(self):
        source = {"detail": "Audit evidence. " * 1000}
        document = StagedResultDocument(source)
        reference = document.overview["detail"]
        self.assertEqual(reference["delivery"], "chunked_text")
        page = document.page(reference["sectionId"], page_size=50)
        self.assertEqual("".join(page["items"]), source["detail"])

    def test_cursor_is_bound_to_one_result_and_section(self):
        document = StagedResultDocument({"left": [1, 2], "right": [3, 4]})
        left = document.overview["left"]["sectionId"]
        right = document.overview["right"]["sectionId"]
        cursor = document.page(left, page_size=1)["nextCursor"]
        with self.assertRaises(PlatformContractError) as mismatch:
            document.page(right, cursor=cursor)
        self.assertEqual(mismatch.exception.code, "INVALID_CURSOR")

    def test_page_shrinks_to_the_platform_byte_budget(self):
        item = {"left": "x" * 3000, "right": "y" * 3000, "note": "z" * 3000}
        document = StagedResultDocument({"findings": [{**item, "id": index} for index in range(20)]})
        section = document.overview["findings"]["sectionId"]
        first = document.page(section, page_size=20)
        self.assertLess(first["page"]["count"], 20)
        self.assertLessEqual(first["page"]["approxBytes"], document.descriptor()["pageBudgetBytes"])
        self.assertIsNotNone(first["nextCursor"])

    def test_identical_detail_arrays_reuse_one_section(self):
        shared = [{"id": "finding-1"}]
        document = StagedResultDocument({"primary": shared, "alias": shared})
        self.assertEqual(document.overview["primary"], document.overview["alias"])
        self.assertEqual(document.section_count, 1)


if __name__ == "__main__":
    unittest.main()
