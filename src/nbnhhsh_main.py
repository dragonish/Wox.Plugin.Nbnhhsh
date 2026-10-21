import asyncio
import json
import re
import urllib.error
import urllib.request
import webbrowser

from wox_plugin import (
    ActionContext,
    Context,
    CopyParams,
    CopyType,
    PluginInitParams,
    Query,
    QueryResponse,
    Result,
    ResultAction,
    ResultTail,
    ResultTailType,
    WoxImage,
)

HOMEPAGE = "https://lab.magiconch.com"
GUESS_API = HOMEPAGE + "/api/nbnhhsh/guess"

# "能不能好好说话（nbnhhsh）" -> ("能不能好好说话", "nbnhhsh")
_TRAN_RE = re.compile(r"^(.+?)([（(](.*)[)）])?$")

_ICON_TRANS = WoxImage.new_relative("images/trans.png")
_ICON_INPUTTING = WoxImage.new_relative("images/inputting.png")
_ICON_LINK = WoxImage.new_relative("images/link.png")
_ICON_ERROR = WoxImage.new_relative("images/error.png")


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
    async def init(self, ctx: Context, params: PluginInitParams) -> None:
        self.api = params.api

    async def _copy(self, ctx: Context, text: str) -> None:
        await self.api.copy(ctx, CopyParams(type=CopyType.TEXT, text=text))

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

        try:
            items = await asyncio.get_running_loop().run_in_executor(None, _post_guess, text)
        except urllib.error.HTTPError as e:
            return QueryResponse(results=[self._error_result(text, f"[网络错误，代码: {e.code}]")])
        except Exception as e:  # 超时、DNS 解析失败等
            return QueryResponse(results=[self._error_result(text, f"[网络错误: {e}]")])

        if not items:
            return QueryResponse(results=[])

        # Wox 按 score 降序展示结果；score 相同时会再按标题码位排序，
        # 因此按接口顺序赋递减 score，把顺序编码进分数里。
        # 从 max(结果数, 100) 起手递减：数量再多也不并列、不溢出。
        score = max(len(items), 100)
        results = []
        for item in items:
            name = item.get("name", "")
            for tran in item.get("trans") or []:
                parsed, sub = _parse_tran(tran)
                results.append(
                    Result(
                        title=parsed,
                        sub_title=sub,
                        icon=_ICON_TRANS,
                        score=score,
                        tails=self._name_tail(name),
                        actions=[
                            self._copy_action("复制", parsed),
                            self._copy_action("复制原始内容", tran, is_default=False),
                        ],
                    )
                )
                score = max(1, score - 1)
            for guess in item.get("inputting") or []:
                results.append(
                    Result(
                        title=guess,
                        sub_title="可能的结果",
                        icon=_ICON_INPUTTING,
                        score=score,
                        tails=self._name_tail(name),
                        actions=[self._copy_action("复制", guess)],
                    )
                )
                score = max(1, score - 1)

        results.append(self._submit_result(text))
        return QueryResponse(results=results)


plugin = Nbnhhsh()
