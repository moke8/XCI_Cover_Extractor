import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from datasource_base import google_translate


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps([[['宝可梦 心金', None]]]).encode()


class GoogleTranslateTests(unittest.TestCase):
    def test_uses_automatic_source_detection_for_japanese_titles(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return _FakeResponse()

        with patch('datasource_base.urlopen', side_effect=fake_urlopen):
            translated = google_translate(
                'ポケットモンスター ハートゴールド', 'zh-CN')

        query = parse_qs(urlsplit(requests[0][0].full_url).query)
        self.assertEqual(query['sl'], ['auto'])
        self.assertEqual(query['tl'], ['zh-CN'])
        self.assertEqual(
            query['q'], ['ポケットモンスター ハートゴールド'])
        self.assertEqual(translated, '宝可梦 心金')


if __name__ == '__main__':
    unittest.main()
