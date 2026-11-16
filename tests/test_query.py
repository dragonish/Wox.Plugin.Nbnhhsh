import asyncio
import json
import unittest
from unittest.mock import patch

from wox_plugin import (
    ActionContext,
    Context,
    Query,
    QueryEnv,
    QueryType,
    Selection,
    SetSettingOption,
    SetSettingResult,
    WoxImageType,
    WoxPreviewType,
)

from src.nbnhhsh_main import Nbnhhsh, _all_preview


class _FakeApi:
    """只实现 query 与切换动作需要的设置读写、刷新查询。"""

    def __init__(self, value: str = "false", *, fail: bool = False, save_ok: bool | Exception = True) -> None:
        self.value = value
        self.fail = fail
        self.save_ok = save_ok
        self.keys: list[str] = []
        self.settings: list[SetSettingOption] = []
        self.refreshes: list = []
        self.logs: list[tuple] = []

    async def get_setting(self, ctx: Context, key: str) -> str:
        self.keys.append(key)
        if self.fail:
            raise RuntimeError("boom")
        return self.value

    async def set_setting(self, ctx: Context, option: SetSettingOption) -> SetSettingResult:
        self.settings.append(option)
        if isinstance(self.save_ok, Exception):
            raise self.save_ok
        return SetSettingResult(success=self.save_ok, errmsg="" if self.save_ok else "save failed")

    async def refresh_query(self, ctx: Context, param) -> None:
        self.refreshes.append(param)

    async def log(self, ctx: Context, level, msg: str) -> None:
        self.logs.append((level, msg))


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
        self.assertEqual(response.results[0].actions[-1].name, "展示全部释义")

    def test_preview_is_shared_by_every_result(self):
        self.plugin.api = _FakeApi("true")
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        previews = [result.preview for result in response.results[:-1]]
        self.assertTrue(previews)
        self.assertTrue(all(preview.preview_data == previews[0].preview_data for preview in previews))

    def test_results_have_copy_and_preview_actions(self):
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        first = response.results[0]
        self.assertEqual([action.name for action in first.actions], ["复制", "复制原始内容", "展示全部释义"])

    def test_preview_toggle_action_reflects_enabled_state(self):
        self.plugin.api = _FakeApi("true")
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        toggle = response.results[0].actions[-1]
        self.assertEqual(toggle.name, "隐藏全部释义")
        self.assertFalse(toggle.is_default)
        self.assertTrue(toggle.prevent_hide_after_action)

    def test_preview_toggle_icon_is_theme_aware_inline_svg(self):
        # 只有内联 svg + var(--wox-theme-icon-color) 才会被 Wox 按行文字色着色，
        # 相对路径 SVG 会被固定渲染成黑/白。
        for value in ("false", "true"):
            with self.subTest(value=value):
                self.plugin.api = _FakeApi(value)
                response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
                icon = response.results[0].actions[-1].icon
                self.assertEqual(icon.image_type, WoxImageType.SVG)
                self.assertIn("var(--wox-theme-icon-color)", icon.image_data)

    def test_toggle_action_hides_preview_and_refreshes(self):
        api = _FakeApi("true")
        self.plugin.api = api
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        toggle = response.results[0].actions[-1]
        asyncio.run(toggle.action(Context.new(), ActionContext(result_id="result-1")))
        self.assertEqual(len(api.settings), 1)
        option = api.settings[0]
        self.assertEqual(option.key, "enable_preview")
        self.assertEqual(option.value, "false")
        self.assertFalse(option.platform_specific)
        self.assertFalse(option.is_local)
        self.assertEqual(len(api.refreshes), 1)
        self.assertTrue(api.refreshes[0].preserve_selected_index)

    def test_toggle_action_shows_preview_and_refreshes(self):
        api = _FakeApi("false")
        self.plugin.api = api
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        toggle = response.results[0].actions[-1]
        asyncio.run(toggle.action(Context.new(), ActionContext(result_id="result-1")))
        self.assertEqual(api.settings[0].value, "true")
        self.assertEqual(len(api.refreshes), 1)

    def test_toggle_action_primes_query_cache(self):
        self.plugin.api = _FakeApi("false")
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        toggle = response.results[0].actions[-1]
        with patch("src.nbnhhsh_main._cache_set") as cache_set:
            asyncio.run(toggle.action(Context.new(), ActionContext(result_id="result-1")))
        cache_set.assert_called_once()
        self.assertEqual(cache_set.call_args.args[0], "lo")

    def test_toggle_action_logs_when_save_fails(self):
        for save_ok in (False, RuntimeError("boom")):
            with self.subTest(save_ok=save_ok):
                api = _FakeApi("false", save_ok=save_ok)
                self.plugin.api = api
                response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
                toggle = response.results[0].actions[-1]
                asyncio.run(toggle.action(Context.new(), ActionContext(result_id="result-1")))
                self.assertEqual(api.settings[0].key, "enable_preview")
                self.assertEqual(api.refreshes, [])
                self.assertTrue(any("保存预览设置失败" in msg for _, msg in api.logs))

    def test_inputting_result_has_preview_toggle(self):
        items = [{"name": "yyds", "trans": [], "inputting": ["永远的神"]}]
        with patch("src.nbnhhsh_main._cache_get", return_value=None), patch("src.nbnhhsh_main._post_guess", return_value=items):
            response = asyncio.run(self.plugin.query(Context.new(), self._query("uniquetext")))
        self.assertEqual([action.name for action in response.results[0].actions], ["复制", "展示全部释义"])

    def test_submit_result_has_no_preview_toggle(self):
        response = asyncio.run(self.plugin.query(Context.new(), self._query("lo")))
        submit = response.results[-1]
        self.assertEqual([action.name for action in submit.actions], ["访问网站提交"])

    def test_error_result_has_no_preview_toggle(self):
        with (
            patch("src.nbnhhsh_main._cache_get", return_value=None),
            patch("src.nbnhhsh_main._post_guess", side_effect=RuntimeError("boom")),
        ):
            response = asyncio.run(self.plugin.query(Context.new(), self._query("uniqueerr")))
        self.assertEqual([action.name for action in response.results[0].actions], ["访问网站"])

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
