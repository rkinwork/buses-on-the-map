from functools import partial
import json
from functools import wraps

import pytest
from trio_websocket import serve_websocket, open_websocket
import trio

from server import talk_to_browser, WindowBounds, Buses, listen_to_browser

HOST = '127.0.0.1'
RESOURCE = '/'


class FailAfter:

    def __init__(self, seconds):
        self._seconds = seconds

    def __call__(self, fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            with trio.move_on_after(self._seconds) as cancel_scope:
                await fn(*args, **kwargs)
            if cancel_scope.cancelled_caught:
                pytest.fail(f'Test runtime exceeded the maximum {self._seconds} seconds')

        return wrapper


@pytest.fixture
async def ttb_server(nursery):
    buses = Buses()
    bounds = WindowBounds(buses=buses)
    coros = [
        partial(listen_to_browser, bounds=bounds),
    ]
    ttb = partial(talk_to_browser, coros=coros)
    serve_fn = partial(serve_websocket,
                       ttb,
                       HOST,
                       0,
                       ssl_context=None)
    server = await nursery.start(serve_fn)
    yield server


@pytest.fixture
async def ttb_conn(ttb_server):
    async with open_websocket(
        HOST,
        ttb_server.port,
        RESOURCE,
        use_ssl=False,
    ) as conn:
        yield conn


@FailAfter(3)
@pytest.mark.parametrize('message, check', [
    ('{',
     json.dumps(
         {"errors": ["not valid JSON"], "msgType": "Errors"}
     ),
     ),
    (
        json.dumps(
            {
                'WRONGmsgType': 'newBounds',
                'WRONGdata': {
                    'south_lat': 1.0,
                    'north_lat': 1.0,
                    'west_lng': 1.0,
                    'east_lng': 1.0,
                },
            },
        ),
        json.dumps(
            {'errors': ["'data' is a required property", "'msgType' is a required property"], 'msgType': 'Errors'}
        ),

    ),
    (
        json.dumps(
            {
                'msgType': 'WRONGnewBounds',
                'data': {
                },
            },
        ),
        json.dumps(
            {'errors': ["'WRONGnewBounds' does not match '^newBounds$'",
                        "'east_lng' is a required property",
                        "'north_lat' is a required property",
                        "'south_lat' is a required property",
                        "'west_lng' is a required property"],
             'msgType': 'Errors'}

        ),

    ),

])
async def test_client_send_and_receive(message, check, ttb_conn):
    async with ttb_conn:
        await ttb_conn.send_message(message)
        received_msg = await ttb_conn.get_message()
        assert received_msg == check
