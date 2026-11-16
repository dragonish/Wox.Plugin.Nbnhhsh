import asyncio
import json
import re
import time
import urllib.error
import urllib.request
import webbrowser
from typing import cast

from wox_plugin import (
    ActionContext,
    Context,
    CopyParams,
    CopyType,
    LogLevel,
    PluginInitParams,
    Query,
    QueryResponse,
    RefreshQueryParam,
    Result,
    ResultAction,
    ResultTail,
    ResultTailType,
    SetSettingOption,
    WoxImage,
    WoxPreview,
    WoxPreviewScrollPosition,
    WoxPreviewTag,
    WoxPreviewType,
)

HOMEPAGE = "https://lab.magiconch.com"
GUESS_API = HOMEPAGE + "/api/nbnhhsh/guess"

# 开启后会在结果右侧的预览面板列出该缩写的全部释义。
SETTING_ENABLE_PREVIEW = "enable_preview"

# "能不能好好说话（nbnhhsh）" -> ("能不能好好说话", "nbnhhsh")
_TRAN_RE = re.compile(r"^(.+?)([（(](.*)[)）])?$")

_ICON_TRANS = WoxImage.new_relative("images/trans.png")
_ICON_INPUTTING = WoxImage.new_relative("images/inputting.png")
_ICON_LINK = WoxImage.new_relative("images/link.png")
_ICON_ERROR = WoxImage.new_relative("images/error.png")

# 动作面板图标必须是内联 SVG：Wox 只会给 ImageType 为 svg 且包含
# var(--wox-theme-icon-color) 的图标套用行文字色与选中态颜色。
_PREVIEW_SHOW_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="var(--wox-theme-icon-color)" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/>'
    '<circle cx="12" cy="12" r="3"/></svg>'
)
_PREVIEW_HIDE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="var(--wox-theme-icon-color)" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="m2 2 20 20"/>'
    '<path d="M6.71 6.71A10.5 10.5 0 0 0 2 12s3.5 7 10 7a10.4 10.4 0 0 0 5.29-1.44"/>'
    '<path d="M10.58 10.58a2 2 0 0 0 2.84 2.84"/>'
    '<path d="M9.88 5.09A10.9 10.9 0 0 1 12 5c6.5 0 10 7 10 7a17.8 17.8 0 0 1-3.16 4.19"/></svg>'
)
_ICON_PREVIEW_SHOW = WoxImage.new_svg(_PREVIEW_SHOW_SVG)
_ICON_PREVIEW_HIDE = WoxImage.new_svg(_PREVIEW_HIDE_SVG)

# 缓存最近的查询结果，避免重复输入时反复请求接口。
_CACHE_TTL = 60.0
_CACHE_MAX = 32
_guess_cache: dict[str, tuple[float, list]] = {}


def _cache_get(text: str) -> list | None:
    hit = _guess_cache.get(text)
    if hit is None:
        return None
    stamp, items = hit
    if time.monotonic() - stamp > _CACHE_TTL:
        _guess_cache.pop(text, None)
        return None
    return items


def _cache_set(text: str, items: list) -> None:
    if text not in _guess_cache and len(_guess_cache) >= _CACHE_MAX:
        oldest = min(_guess_cache, key=lambda key: _guess_cache[key][0])
        _guess_cache.pop(oldest, None)
    _guess_cache[text] = (time.monotonic(), items)


def _all_preview(name: str, trans: list, inputting: list) -> WoxPreview:
    """列出该缩写的全部释义，供预览面板展示。"""
    lines = [f"### {name}"]
    if trans:
        lines += ["", f"**全部释义（{len(trans)}）**", ""]
        lines += [f"{index}. {tran}" for index, tran in enumerate(trans, 1)]
    if inputting:
        lines += ["", "**可能的结果**", ""]
        lines += [f"- {guess}" for guess in inputting]
    return WoxPreview(
        preview_type=WoxPreviewType.MARKDOWN,
        preview_data="\n".join(lines),
        # 空字符串表示从顶部开始；SDK 默认的 BOTTOM 会直接滚到末尾。
        scroll_position=cast(WoxPreviewScrollPosition, ""),
        preview_tags=[WoxPreviewTag(label=f"共 {len(trans)} 条释义")] if trans else [],
    )


def _post_guess(text):
    """同步 POST，放到线程池里跑，避免阻塞插件的事件循环。"""
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        GUESS_API,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_tran(tran):
    m = _TRAN_RE.match(tran)
    if m and m.group(1):
        return m.group(1), m.group(3) or ""
    return tran, ""


class Nbnhhsh:
    def __init__(self) -> None:
        # 最近一次成功查询的 (关键词, 结果)，切换预览前用它预热缓存。
        self._last_success: tuple[str, list] | None = None

    async def init(self, ctx: Context, params: PluginInitParams) -> None:
        self.api = params.api

    async def _copy(self, ctx: Context, text: str) -> None:
        await self.api.copy(ctx, CopyParams(type=CopyType.TEXT, text=text))

    async def _log_error(self, ctx: Context, msg: str) -> None:
        api = getattr(self, "api", None)
        if api is None:
            return
        try:
            await api.log(ctx, LogLevel.ERROR, msg)
        except Exception:  # 日志失败不应影响主流程
            pass

    async def _preview_enabled(self, ctx: Context) -> bool:
        """读取「预览展示全部释义」开关；未初始化或读取失败时按关闭处理。"""
        api = getattr(self, "api", None)
        if api is None:
            return False
        try:
            value = await api.get_setting(ctx, SETTING_ENABLE_PREVIEW)
        except Exception:  # 旧版 Wox 或设置读取失败时不启用
            return False
        return str(value).strip().lower() == "true"

    async def _set_preview_enabled(self, ctx: Context, enabled: bool) -> None:
        """写入「预览展示全部释义」开关，并刷新当前查询让预览立即生效。"""
        api = getattr(self, "api", None)
        if api is None:
            return

        # 预热缓存，避免刷新时因 TTL 过期而重新请求接口。
        if self._last_success:
            _cache_set(*self._last_success)

        try:
            result = await api.set_setting(
                ctx,
                SetSettingOption(
                    key=SETTING_ENABLE_PREVIEW,
                    value="true" if enabled else "false",
                    platform_specific=False,
                    is_local=False,
                ),
            )
        except Exception as e:  # 旧版 Wox 或设置写入失败
            await self._log_error(ctx, f"保存预览设置失败: {e}")
            return

        if not result.success:
            await self._log_error(ctx, f"保存预览设置失败: {result.errmsg}")
            return

        try:
            await api.refresh_query(ctx, RefreshQueryParam(preserve_selected_index=True))
        except Exception as e:
            await self._log_error(ctx, f"刷新查询失败: {e}")

    def _preview_toggle_action(self, enabled: bool) -> ResultAction:
        """按当前设置生成「展示/隐藏全部释义」动作，名称表示点击后的目标状态。"""
        name = "隐藏全部释义" if enabled else "展示全部释义"
        target = not enabled

        async def _do(ctx: Context, ac: ActionContext) -> None:
            await self._set_preview_enabled(ctx, target)

        return ResultAction(
            name=name,
            icon=_ICON_PREVIEW_HIDE if enabled else _ICON_PREVIEW_SHOW,
            is_default=False,
            prevent_hide_after_action=True,
            action=_do,
        )

    def _copy_action(self, name: str, text: str, is_default: bool = True) -> ResultAction:
        async def _do(ctx: Context, ac: ActionContext) -> None:
            await self._copy(ctx, text)

        return ResultAction(
            name=name,
            icon=WoxImage.new_emoji("📋"),
            is_default=is_default,
            action=_do,
        )

    def _open_url_action(self, name: str, url: str) -> ResultAction:
        async def _do(ctx: Context, ac: ActionContext) -> None:
            webbrowser.open(url)

        return ResultAction(
            name=name,
            icon=WoxImage.new_emoji("🔗"),
            is_default=True,
            action=_do,
        )

    def _name_tail(self, name: str) -> list[ResultTail]:
        """把原始缩写名固定在结果尾部，与 sub_title 的含义区分开。"""
        if not name:
            return []
        return [ResultTail(type=ResultTailType.TEXT, text=name, id="name")]

    def _submit_result(self, text: str) -> Result:
        return Result(
            title=f"[尚未录入对应文字: {text}]",
            sub_title="访问网站提交对应文字",
            icon=_ICON_LINK,
            score=0,
            actions=[
                self._open_url_action("访问网站提交", f"{HOMEPAGE}/nbnhhsh/#/text/{text}"),
            ],
        )

    def _error_result(self, text: str, error: str) -> Result:
        return Result(
            title=error,
            sub_title="访问网站查看详情",
            icon=_ICON_ERROR,
            score=0,
            actions=[
                self._open_url_action("访问网站", f"{HOMEPAGE}/nbnhhsh/#/text/{text}"),
            ],
        )

    async def query(self, ctx: Context, query: Query) -> QueryResponse:
        text = (query.search or "").strip()
        if len(text) < 2:
            return QueryResponse(results=[])

        items = _cache_get(text)
        if items is None:
            try:
                response = await asyncio.get_running_loop().run_in_executor(None, _post_guess, text)
            except urllib.error.HTTPError as e:
                return QueryResponse(results=[self._error_result(text, f"[网络错误，代码: {e.code}]")])
            except Exception as e:  # 超时、DNS 解析失败等
                return QueryResponse(results=[self._error_result(text, f"[网络错误: {e}]")])
            items = list(response or [])
            _cache_set(text, items)

        if not items:
            return QueryResponse(results=[])

        self._last_success = (text, items)
        preview_enabled = await self._preview_enabled(ctx)

        # Wox 按 score 降序展示结果；score 相同时会再按标题码位排序，
        # 因此按接口顺序赋递减 score，把顺序编码进分数里。
        # 从 max(结果数, 100) 起手递减：数量再多也不并列、不溢出。
        score = max(len(items), 100)
        results = []
        for item in items:
            name = item.get("name", "")
            trans = list(item.get("trans") or [])
            inputting = list(item.get("inputting") or [])
            preview = _all_preview(name or text, trans, inputting) if preview_enabled else WoxPreview()
            for tran in trans:
                parsed, sub = _parse_tran(tran)
                results.append(
                    Result(
                        title=parsed,
                        sub_title=sub,
                        icon=_ICON_TRANS,
                        preview=preview,
                        score=score,
                        tails=self._name_tail(name),
                        actions=[
                            self._copy_action("复制", parsed),
                            self._copy_action("复制原始内容", tran, is_default=False),
                            self._preview_toggle_action(preview_enabled),
                        ],
                    )
                )
                score = max(1, score - 1)
            for guess in inputting:
                results.append(
                    Result(
                        title=guess,
                        sub_title="可能的结果",
                        icon=_ICON_INPUTTING,
                        preview=preview,
                        score=score,
                        tails=self._name_tail(name),
                        actions=[
                            self._copy_action("复制", guess),
                            self._preview_toggle_action(preview_enabled),
                        ],
                    )
                )
                score = max(1, score - 1)

        results.append(self._submit_result(text))
        return QueryResponse(results=results)


plugin = Nbnhhsh()
