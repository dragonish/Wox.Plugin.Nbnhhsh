import asyncio
import unittest

from wox_plugin import Context, Query, QueryEnv, QueryType, Selection

from src.main import Nbnhhsh


class TestNbnhhsh(unittest.TestCase):
    def setUp(self):
        self.plugin = Nbnhhsh()

    def test_query(self):
        query = Query(
            id="test",
            type=QueryType.INPUT,
            raw_query="test",
            selection=Selection(),
            env=QueryEnv(),
            search="test",
        )
        response = asyncio.run(self.plugin.query(Context.new(), query))
        self.assertTrue(response.results)
        self.assertFalse(response.results[0].title.startswith("[网络错误"))


if __name__ == "__main__":
    unittest.main()
