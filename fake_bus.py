import argparse
import logging
from contextlib import suppress
import json
from zipfile import ZipFile
from typing import Iterable, Tuple
from itertools import cycle, islice
from copy import deepcopy
from functools import wraps

import trio
from trio_websocket import open_websocket_url
from trio_websocket import HandshakeError, ConnectionClosed

BUS_SEND_DELAY = 1
RECONNECT_TIMEOUT_SEC = 10


def fake_bus(bus_id: str, route: dict) -> str:
    resp = {
        "busId": str(bus_id),
        "lat": 55.7500,
        "lng": 37.600,
        "route": str(route['name']),
    }

    coordinates = route['coordinates']
    for coord in cycle(coordinates):
        resp['lat'] = coord[0]
        resp['lng'] = coord[1]
        yield json.dumps(resp, ensure_ascii=False)


def load_routes_from_source(routes='routes.zip', limit=10) -> Iterable[dict]:
    rts = ZipFile(routes)
    flns = [filename for filename in rts.namelist() if filename.endswith('.json')]
    for r in islice(flns, limit):
        with rts.open(r) as fl:
            yield json.load(fl)


def load_routes(routes_source='routes.zip', num_routes=10, buses_per_route=50, prefix='') -> Tuple[str, dict]:
    """Generates fake bus routes. With random number of buses per route."""
    prefix = prefix + '__' if prefix else ''
    for route in load_routes_from_source(routes=routes_source, limit=num_routes):
        coordinates, coord_len = route['coordinates'], len(route['coordinates'])
        # for pos in range(1, randint(1, buses_per_route_max)):
        for pos in range(1, buses_per_route):
            route['coordinates'] = [*coordinates[coord_len // pos:], *coordinates[:coord_len // pos]]
            yield (
                f'{prefix}{route["name"]}-{pos}',
                deepcopy(route),
            )


def even_chunks(it, consumers_num):
    size = (len(it) // consumers_num) + 1
    it = iter(it)
    return iter(lambda: tuple(islice(it, size)), ())


def relaunch_on_disconnect(async_function):
    @wraps(async_function)
    async def wrapper(*args, **kwargs):
        while True:
            try:
                return await async_function(*args, **kwargs)
            except (OSError, HandshakeError) as ose:
                logging.error(f'Connection attempt failed: {ose}')
                await trio.sleep(RECONNECT_TIMEOUT_SEC)
            except ConnectionClosed as e:
                logging.error(f'Connection has been closed {e}')
                await trio.sleep(RECONNECT_TIMEOUT_SEC)

    return wrapper


@relaunch_on_disconnect
async def send_updates(server_address: str, receive_channel: trio.MemoryReceiveChannel):
    async with open_websocket_url(url=server_address) as ws:
        async for value in receive_channel:
            await ws.send_message(value)


async def run_bus(send_channel: trio.MemorySendChannel,
                  bus_id: str,
                  route: dict,
                  refresh_timeout: int,
                  ):
    async with send_channel:
        for m in fake_bus(bus_id=bus_id, route=route):
            await send_channel.send(m)
            await trio.sleep(refresh_timeout)


async def batch_run_bus(server_address: str, buses: Iterable, refresh_timeout: int):
    async with trio.open_nursery() as nursery:
        send_channel, receive_channel = trio.open_memory_channel(0)
        async with send_channel, receive_channel:
            nursery.start_soon(send_updates, server_address, receive_channel.clone())
            for bus in buses:
                nursery.start_soon(run_bus, send_channel.clone(), *bus, refresh_timeout)


async def emulate_bus_run(server: str,
                          routes_number: int = 10,
                          buses_per_route: int = 10,
                          websockets_number: int = 3,
                          emulator_id: str = '',
                          refresh_timeout: int = 1,
                          ):
    routes = [route for route in load_routes(num_routes=routes_number,
                                             buses_per_route=buses_per_route,
                                             prefix=emulator_id,
                                             )]
    chunks = [chunk for chunk in even_chunks(routes, websockets_number)]
    logging.info(f"created {len(routes)}")
    async with trio.open_nursery() as nursery:
        for chunk in chunks:
            nursery.start_soon(batch_run_bus, server, chunk, refresh_timeout)


async def main(config: [dict] = None):
    config = config or {}
    if 'server' not in config:
        config['server'] = 'ws://127.0.0.1:8080'
    await emulate_bus_run(**config)


def parse_config() -> dict:
    parser = argparse.ArgumentParser(description='Generate fake buses for server testing purpose')
    parser.add_argument("-s", "--server",
                        help="socket address")
    parser.add_argument("-r", "--routes", type=int, help="how many routes generate", default=10)
    parser.add_argument("-n", "--number", type=int, help="how many generate buses per route", default=10)
    parser.add_argument("--sockets", type=int, help="how many sockets we should generate", default=3)
    parser.add_argument("-e", "--emid", help="prefix for emulated buses", default='')
    parser.add_argument("-t", "--timeout", type=int, help="bus info refresh timeout", default=1)
    parser.add_argument("-v", "--verbose", action="store_true", help="Show logging information")
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    return {
        'server': args.server,
        'routes_number': args.routes,
        'buses_per_route': args.number,
        'websockets_number': args.sockets,
        'emulator_id': args.emid,
        'refresh_timeout': args.timeout,
    }


if __name__ == '__main__':
    config = parse_config()
    with suppress(KeyboardInterrupt):
        trio.run(main, config)
