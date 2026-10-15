"""Public read-only feed; deliberately has no desktop API, database or owner session access."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .mini_gateway import GatewayError, project


PREFIX = '/public/v1/'
FIELDS = {'overview': {'freshness', 'stats', 'news', 'movements'},
          'market': {'assets', 'news'}, 'research': {'items', 'total', 'offset'}}
FORBIDDEN = {'portfolio', 'positions', 'principal_cny', 'quantity', 'average_cost', 'settings', 'config',
             'session_key', 'openid', 'token', 'secret', 'appsecret', 'api_key', 'password'}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp is required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return parsed


def check_private_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN:
                raise ValueError('Private field in public feed')
            check_private_keys(child)
    elif isinstance(value, list):
        for child in value:
            check_private_keys(child)


class PublicFeed:
    def __init__(self, path):
        self.path = Path(path)

    def snapshot(self, view):
        if view not in FIELDS:
            raise GatewayError(404, '接口不存在')
        try:
            with self.path.open('rb') as stream:
                content = stream.read(1_000_001)
            if len(content) > 1_000_000:
                raise ValueError('Feed too large')
            feed = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
            if (not isinstance(feed, dict) or set(feed) != {'schema_version', 'publication', 'views'}
                    or type(feed['schema_version']) is not int or feed['schema_version'] != 1):
                raise ValueError('Invalid schema')
            publication = feed['publication']
            if not isinstance(publication, dict) or any(publication.get(flag) is not True for flag in
                    ('approved_for_publication', 'rights_reviewed', 'privacy_reviewed')):
                raise ValueError('Unapproved feed')
            if not isinstance(publication.get('reviewer'), str) or not publication['reviewer'].strip():
                raise ValueError('Reviewer required')
            if (timestamp(publication.get('published_at')) - datetime.now(timezone.utc)).total_seconds() > 60:
                raise ValueError('Future publication')
            views = feed['views']
            if not isinstance(views, dict) or set(views) != set(FIELDS):
                raise ValueError('Public views only')
            check_private_keys(views)
            # Validate the whole batch before exposing any page; no fallback to an older approved file.
            for key, value in views.items():
                if not isinstance(value, dict) or set(value) != {'data_as_of', 'data'}:
                    raise ValueError('Invalid view envelope')
                if (timestamp(value['data_as_of']) - datetime.now(timezone.utc)).total_seconds() > 60:
                    raise ValueError('Future data time')
                if not isinstance(value['data'], dict) or not set(value['data']) <= FIELDS[key]:
                    raise ValueError('Unexpected public field')
                for field in ('news', 'movements', 'assets', 'items'):
                    if field in value['data'] and (not isinstance(value['data'][field], list)
                            or any(not isinstance(row, dict) for row in value['data'][field])):
                        raise ValueError('Invalid public rows')
            item = views[view]
            result = {'schema_version': 1, 'audience': 'public', 'read_only': True, 'view': view,
                      'served_at': datetime.now(timezone.utc).isoformat(), 'data_as_of': item['data_as_of'],
                      'published_at': publication['published_at'], 'data': project(view, item['data'])}
            if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 350_000:
                raise ValueError('Public response too large')
            return result
        except Exception:
            raise GatewayError(503, '公开内容尚未发布或校验未通过') from None


class PublicHandler(BaseHTTPRequestHandler):
    server_version = 'PublicResearchGateway'
    sys_version = ''

    def handle_api(self):
        try:
            if not self.path.startswith(PREFIX) or '?' in self.path:
                raise GatewayError(404, '接口不存在')
            if self.command != 'GET':
                raise GatewayError(405, '仅提供公开只读接口')
            result = self.server.feed.snapshot(self.path[len(PREFIX):])
            status = 200
        except GatewayError as error:
            status, result = error.status, {'error': str(error)}
        except Exception:
            status, result = 500, {'error': '公开服务暂时不可用'}
        body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = handle_api

    def log_message(self, format, *args):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feed', type=Path, required=True, help='Explicitly reviewed public feed, never desktop data')
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args(argv)
    feed = PublicFeed(args.feed)
    try:
        for view in FIELDS:
            feed.snapshot(view)
    except GatewayError as error:
        parser.error(str(error))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), PublicHandler)
    server.daemon_threads = True
    server.feed = feed
    print(f'Public feed on 127.0.0.1:{args.port}; HTTPS reverse proxy required')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
