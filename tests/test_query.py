import asyncio
import json
import unittest
from unittest.mock import patch

from wox_plugin import Context, Query, QueryEnv, QueryType, Selection, WoxPreviewType

from src.nbnhhsh_main import Nbnhhsh, _all_preview


class _FakeApi:
    """只实现 query 需要的 get_setting。"""

    def __init__(self, value: str = "false", *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.keys: list[str] = []

    async def get_setting(self, ctx: Context, key: str) -> str:
        self.keys.append(key)
        if self.fail:
            raise RuntimeError("boom")
        return self.value


class TestAllPreview(unittest.TestCase):
    def test_lists_every_translation(self):
        preview = _all_preview("yy", ["杨洋", "岳岳（注）"], ["意淫"])
        self.assertEqual(preview.preview_type, WoxPreviewType.MARKDOWN)
        self.assertIn("### yy", preview.preview_data)
        self.assertIn("**全部释义（2）**", preview.preview_data)
        self.assertIn("1. 杨洋", preview.preview_data)
        self.assertIn("2. 岳岳（注）", preview.preview_data)
        self.assertIn("- 意淫", preview.preview_data)
        self.assertEqual(preview.scroll_position, "")
        self.assertEqual(preview.preview_tags[0].label, "共 2 条释义")

    def test_inputting_only_has_no_translation_section(self):
        preview = _all_preview("yydsx", [], ["一样的思绪"])
        self.assertNotIn("全部释义", preview.preview_data)
        self.assertIn("**可能的结果**", preview.preview_data)
        self.assertEqual(preview.preview_tags, [])


class TestNbnhhsh(unittest.TestCase):
    def setUp(self):
        self.plugin = Nbnhhsh()

    def _query(self, search: str) -> Query:
        return Query(
            id="test",
            type=QueryType.INPUT,
            raw_query=f"sx {search}",
            selection=Selection(),
            env=QueryEnv(),
            trigger_keyword="sx",
            search=search,
        )

    def test_query(self):
        response = asyncio.run(self.plugin.query(Context.new(), self._query("test")))
        self.assertTrue(response.results)
        self.assertFalse(response.results[0].title.startswith("[网络错误"))

    def test_preview_disabled_by_default(self):
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        self.assertTrue(response.results)
        first = response.results[0]
        self.assertEqual(first.preview.preview_data, "")
        payload = json.loads(first.to_json())
        self.assertEqual(payload["Preview"]["PreviewData"], "")

    def test_preview_enabled_by_setting(self):
        api = _FakeApi("true")
        self.plugin.api = api
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        self.assertTrue(response.results)
        first = response.results[0]
        self.assertEqual(first.preview.preview_type, WoxPreviewType.MARKDOWN)
        self.assertIn("全部释义", first.preview.preview_data)
        self.assertEqual(first.preview.scroll_position, "")
        self.assertEqual(api.keys, ["enable_preview"])
        payload = json.loads(first.to_json())
        self.assertEqual(payload["Preview"]["ScrollPosition"], "")

    def test_preview_setting_accepts_uppercase_and_spaces(self):
        for value in ("TRUE", " true ", "True"):
            with self.subTest(value=value):
                self.plugin.api = _FakeApi(value)
                response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
                self.assertIn("全部释义", response.results[0].preview.preview_data)

    def test_preview_disabled_when_setting_read_fails(self):
        self.plugin.api = _FakeApi(fail=True)
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        self.assertEqual(response.results[0].preview.preview_data, "")

    def test_preview_is_shared_by_every_result(self):
        self.plugin.api = _FakeApi("true")
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        previews = [result.preview for result in response.results[:-1]]
        self.assertTrue(previews)
        self.assertTrue(all(preview.preview_data == previews[0].preview_data for preview in previews))

    def test_results_only_have_copy_actions(self):
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        first = response.results[0]
        self.assertEqual([action.name for action in first.actions], ["复制", "复制原始内容"])

    def test_short_query_returns_nothing_and_skips_setting_read(self):
        api = _FakeApi("true")
        self.plugin.api = api
        response = asyncio.run(self.plugin.query(Context.new(), self._query("l")))
        self.assertEqual(response.results, [])
        self.assertEqual(api.keys, [])

    def test_all_flag_is_no_longer_special(self):
        with patch("src.nbnhhsh_main._post_guess", return_value=[]) as guess:
            response = asyncio.run(self.plugin.query(Context.new(), self._query("-a lo")))
        self.assertEqual(guess.call_args.args, ("-a lo",))
        self.assertEqual(response.results, [])


if __name__ == "__main__":
    unittest.main()
