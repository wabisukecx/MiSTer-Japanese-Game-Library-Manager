import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from artwork.screenscraper import (
    Credentials, ScreenScraper, APIError, QuotaExceeded, parse_games,
)

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
USER = {'response': {'ssuser': {'maxthreads': '1', 'maxrequestspermin': '60',
        'maxrequestsperday': '100', 'requeststoday': '1', 'maxdownloadspeed': '1024',
        'maxrequestskoperday': '10', 'requestskotoday': '0'}}}
GAMES = {'response': {'jeux': [
    {'id': '1340', 'systeme': {'id': '3'}, 'noms': [{'region': 'jp', 'text': 'ドクターマリオ'},
      {'region': 'us', 'text': 'Dr. Mario'}]},
    {'id': '99', 'systeme': {'id': '12'}, 'noms': [{'region': 'jp', 'text': 'GBA版'}]},
]}}


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.credentials = Credentials('developer', 'developer-secret', 'member', 'member-secret')
        self.transport = Mock()
        self.client = ScreenScraper(self.credentials, Path(self.tmp.name), transport=self.transport,
                                   sleep=lambda _: None, clock=lambda: 1000,
                                   state_root=Path(self.tmp.name) / 'rate')

    def tearDown(self):
        self.tmp.cleanup()

    def test_credentials_repr_and_missing_configuration(self):
        self.assertNotIn('developer-secret', repr(self.credentials))
        self.assertNotIn('member-secret', repr(self.credentials))
        with self.assertRaises(ValueError):
            Credentials('', '', '', '')

    def test_search_filters_system_and_keeps_titles(self):
        self.transport.side_effect = [json.dumps(USER).encode(), json.dumps(GAMES, ensure_ascii=False).encode()]
        result = self.client.search('Dr. Mario')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['provider_game_id'], 1340)
        self.assertEqual(result[0]['japanese_title'], 'ドクターマリオ')
        params = parse_qs(urlsplit(self.transport.call_args.args[0]).query)
        self.assertEqual(params['recherche'], ['Dr. Mario'])
        self.assertEqual(params['systemeid'], ['3'])
        self.assertNotIn('romnom', params)

    def test_jp_png_cache_and_no_secrets_on_disk(self):
        self.transport.side_effect = [json.dumps(USER).encode(), PNG]
        first = self.client.download(1340, region='jp')
        second = self.client.download(1340, region='jp')
        self.assertEqual(first, second)
        self.assertEqual(self.transport.call_count, 2)
        self.assertEqual(first['region'], 'JP')
        params = parse_qs(urlsplit(self.transport.call_args.args[0]).query)
        self.assertEqual(params['media'], ['box-2D(jp)'])
        self.assertEqual(params['outputformat'], ['png'])
        for p in Path(self.tmp.name).rglob('*.json'):
            content = p.read_text(encoding='utf-8')
            self.assertNotIn('developer-secret', content)
            self.assertNotIn('member-secret', content)
            self.assertNotIn('devpassword', content)

    def test_daily_quota_exhaustion_stops_before_search(self):
        user = json.loads(json.dumps(USER))
        user['response']['ssuser']['requeststoday'] = '100'
        self.transport.return_value = json.dumps(user).encode()
        with self.assertRaises(QuotaExceeded):
            self.client.search('Dr. Mario')
        self.assertEqual(self.transport.call_count, 1)

    def test_rate_limit_does_not_retry_or_expose_url(self):
        self.transport.side_effect = HTTPError('https://host/?devpassword=developer-secret',429,
                                              'member-secret', {}, None)
        with self.assertRaises(APIError) as caught:
            self.client.search('Dr. Mario')
        self.assertEqual(self.transport.call_count, 1)
        self.assertNotIn('secret', str(caught.exception))

    def test_transient_failure_retry(self):
        self.transport.side_effect = [HTTPError('https://host/',503,'busy',{},None),
                                      json.dumps(USER).encode(), PNG]
        self.client.download(1340)
        self.assertEqual(self.transport.call_count, 3)

    def test_no_media_not_mislabeled_as_japanese(self):
        self.transport.side_effect = [json.dumps(USER).encode(), b'NOMEDIA']
        with self.assertRaises(APIError):
            self.client.download(1340)
        self.assertEqual(list(Path(self.tmp.name).rglob('*.png')), [])

    def test_corrupt_cache_redownloaded(self):
        self.transport.side_effect = [json.dumps(USER).encode(), PNG, PNG]
        first = self.client.download(1340)
        Path(first['local_path']).write_bytes(b'broken')
        self.client.download(1340)
        self.assertEqual(self.transport.call_count, 3)

    def test_non_json_error_text_redacted(self):
        self.transport.return_value = b'bad developer-secret member-secret'
        with self.assertRaises(APIError) as caught:
            self.client.search('Dr. Mario')
        self.assertNotIn('secret', str(caught.exception))

    def test_unknown_limits_fail_closed(self):
        self.transport.return_value = b'{"response":{"ssuser":{}}}'
        with self.assertRaises(APIError):
            self.client.search('Dr. Mario')

    def test_cancellation(self):
        self.client.cancelled = lambda: True
        with self.assertRaises(InterruptedError):
            self.client.download(1340)
        self.transport.assert_not_called()

    def test_search_cache_reuse(self):
        self.transport.side_effect = [json.dumps(USER).encode(), json.dumps(GAMES).encode()]
        first = self.client.search('Dr. Mario')
        self.assertEqual(first, self.client.search('Dr. Mario'))
        self.assertEqual(self.transport.call_count, 2)

    def test_cancel_during_transport_is_not_retried(self):
        self.transport.side_effect = InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):
            self.client.search('Dr. Mario')
        self.assertEqual(self.transport.call_count, 1)
        self.assertFalse(self.client.lock.exists())

    def test_lower_minute_limit_is_applied_before_next_request(self):
        user = json.loads(json.dumps(USER))
        user['response']['ssuser']['maxrequestspermin'] = '2'
        sleeps = []
        self.client.sleep = sleeps.append
        self.transport.side_effect = [json.dumps(user).encode(), PNG]
        self.client.download(1340)
        self.assertEqual(self.client.interval, 30)
        self.assertGreaterEqual(sum(sleeps), 30)

    def test_http_auth_failure_is_not_retried(self):
        self.transport.side_effect = HTTPError('https://host/?devpassword=developer-secret',403,
                                              'member-secret', {}, None)
        with self.assertRaises(APIError):
            self.client.search('Dr. Mario')
        self.assertEqual(self.transport.call_count, 1)

    def test_unexpected_search_shape_is_an_error(self):
        with self.assertRaises(APIError):
            parse_games({'response': {'error': 'no access'}})


if __name__ == '__main__':
    unittest.main()
