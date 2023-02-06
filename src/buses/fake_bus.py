import json
import logging
import zipfile
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from itertools import cycle, islice
from pathlib import Path
from typing import Any, Iterable, Tuple

import configargparse
import trio
from trio_websocket import ConnectionClosed, HandshakeError, open_websocket_url

from buses import models

RECONNECT_TIMEOUT_SEC = 10
DEFAULT_LAT = 55.75
DEFAULT_LNG = 37.6
COORDINATES_KEY = 'coordinates'


def load_routes_from_source(routes: Path, limit=10) -> Iterable[dict]:
    rts = zipfile.ZipFile(routes)
    fl_names = [
        f_name for f_name in rts.namelist() if f_name.endswith('.json')
    ]
    for route in islice(fl_names, limit):
        with rts.open(route) as fl:
            yield json.load(fl)


def get_routes(
    routes: Iterable[dict],
    buses_per_route=50,
    prefix='',
) -> Iterable[Tuple[str, dict]]:
    if prefix != '':
        prefix = '{prefix}__'.format(prefix=prefix)
    for route in routes:
        coords, coord_len = route[COORDINATES_KEY], len(route[COORDINATES_KEY])
        for pos in range(1, buses_per_route):
            route['coordinates'] = [
                *coords[coord_len // pos:],
                *coords[:coord_len // pos],
            ]
            yield (
                '{prefix}{route_name}-{pos}'.format(
                    prefix=prefix,
                    route_name=route['name'],
                    pos=pos,
                ),
                deepcopy(route),
            )


def split_on_even_chunks(it, consumers_num) -> Iterable[Tuple[Any]]:
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
                logging.error('Connection attempt failed: {0}'.format(ose))
                await trio.sleep(RECONNECT_TIMEOUT_SEC)
            except ConnectionClosed as e_cl:
                logging.error('Connection has been closed {0}'.format(e_cl))
                await trio.sleep(RECONNECT_TIMEOUT_SEC)

    return wrapper


@relaunch_on_disconnect
async def send_updates(
    server_address: str,
    receive_channel: trio.MemoryReceiveChannel,
):
    async with open_websocket_url(url=server_address) as ws:
        async for message in receive_channel:
            await ws.send_message(message)


async def run_bus(
    send_channel: trio.MemorySendChannel,
    bus_id: str,
    route: dict,
    refresh_timeout: int,
    task_status=trio.TASK_STATUS_IGNORED,
):
    task_status.started()
    async with send_channel:
        for coord in cycle(route[COORDINATES_KEY]):
            await send_channel.send(
                models.Bus(
                    bus_id=bus_id,
                    lat=coord[0],
                    lng=coord[1],
                    route=route['name'],
                ).to_json(),
            )
            await trio.sleep(refresh_timeout)


async def run_buses(
    server_address: str,
    buses: Iterable,
    refresh_timeout: int,
):
    async with trio.open_nursery() as nursery:
        send_channel, receive_channel = trio.open_memory_channel(0)
        async with send_channel, receive_channel:
            nursery.start_soon(
                send_updates,
                server_address,
                receive_channel.clone(),
            )
            for bus in buses:
                nursery.start_soon(
                    run_bus,
                    send_channel.clone(),
                    *bus,
                    refresh_timeout,
                )


@dataclass
class Config:
    server: str
    routes_number: int = 10
    buses_per_route: int = 10
    websockets_number: int = 3
    emulator_id: str = ''
    refresh_timeout: int = 1
    zipfile: str = 'routes.zip'


async def emulate_bus_run(
    config: Config,
    routes: Iterable[Tuple[str, dict]],
):
    routes = tuple(routes)
    chunks = tuple(split_on_even_chunks(routes, config.websockets_number))
    logging.info('created {cnt}'.format(cnt=len(routes)))
    async with trio.open_nursery() as nursery:
        for chunk in chunks:
            nursery.start_soon(
                run_buses,
                config.server,
                chunk,
                config.refresh_timeout,
            )


def get_config() -> Config:
    parser = configargparse.ArgumentParser(
        description='Generate fake buses for server testing purpose',
    )
    parser.add_argument(
        '-s',
        '--server',
        help='target server socket formatted as ws://127.0.0.1:8080',
        env_var='FAKE_BUS__TARGET_SERVER_HOST',
        default='ws://127.0.0.1:8080',
    )
    parser.add_argument(
        '-z',
        '--zipfile',
        help='location of zip file',
        env_var='FAKE_BUS__ZIP_FILE_LOCATION',
        default='routes.zip',
    )
    parser.add_argument(
        '-r',
        '--routes',
        type=int,
        help='how many routes generate',
        env_var='FAKE_BUS__ROUTES',
        default=10,
    )
    parser.add_argument(
        '-n',
        '--number',
        type=int,
        help='how many generate buses per route',
        env_var='FAKE_BUS__ROUTES_COUNT',
        default=10,
    )
    parser.add_argument(
        '--sockets',
        type=int,
        help='how many sockets we should generate',
        env_var='FAKE_BUS__SOCKETS_NUM',
        default=3,
    )
    parser.add_argument(
        '-e',
        '--emid',
        help='prefix for emulated buses',
        env_var='FAKE_BUS__ROUTE_PREFIX',
        default='',
    )
    parser.add_argument(
        '-t',
        '--timeout',
        type=int,
        help='bus info refresh timeout in seconds',
        env_var='FAKE_BUS__SEND_TIMEOUT',
        default=1,
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        env_var='FAKE_BUS__VERBOSE',
        help='Show logging information',
    )
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    return Config(
        server=args.server,
        routes_number=args.routes,
        buses_per_route=args.number,
        websockets_number=args.sockets,
        emulator_id=args.emid,
        refresh_timeout=args.timeout,
        zipfile=args.zipfile,
    )


def main():
    config = get_config()
    routes = get_routes(
        routes=load_routes_from_source(
            routes=Path(config.zipfile),
            limit=config.routes_number,
        ),
        buses_per_route=config.buses_per_route,
        prefix=config.emulator_id,
    )
    with suppress(KeyboardInterrupt):
        trio.run(
            emulate_bus_run,
            config,
            routes,
        )


if __name__ == '__main__':
    main()
