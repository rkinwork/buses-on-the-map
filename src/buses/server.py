import json
import logging
from contextlib import suppress
from functools import partial
from typing import Iterable

import configargparse
import trio
from trio_websocket import ConnectionClosed, serve_websocket

from buses import buses_errors, models

BUS_SEND_DELAY = 1

DEFAULT_CLIENT_HOST = '127.0.0.1'
DEFAULT_CLIENT_PORT = 8080
DEFAULT_BUS_SERVER_HOST = '127.0.0.1'
DEFAULT_BUS_SERVER_PORT = 8000


class Server:

    @classmethod
    async def send_error_message(
        cls,
        ws,
        message_exception: buses_errors.MessageError,
    ):
        msg = json.dumps({
            'errors': tuple(message_exception),
            'msgType': 'Errors',
        })
        await ws.send_message(msg)

    @classmethod
    async def listen_to_browser(
        cls,
        ws,
        bounds: models.WindowBounds,
    ):
        while True:
            try:
                message = await ws.get_message()
            except ConnectionClosed:
                break

            try:
                bounds.process_message(message=message)
            except buses_errors.BoundsReadMessageError as exception:
                await cls.send_error_message(ws, exception)

    @classmethod
    async def send_buses(
        cls,
        ws,
        bounds: models.WindowBounds,
    ):
        while True:
            bounds.update()
            try:
                await ws.send_message(bounds.dump())
            except ConnectionClosed:
                break
            await trio.sleep(BUS_SEND_DELAY)

    async def bus_server(
        self,
        request,
        buses: models.Buses,
    ):
        ws = await request.accept()
        while True:
            try:
                message = await ws.get_message()
            except ConnectionClosed:
                break

            try:
                buses.add_bus(message)
            except buses_errors.BusReadMessageError as exception:
                await self.send_error_message(ws, exception)

    @classmethod
    async def talk_to_browser(
        cls,
        request,
        coros: Iterable,
    ):
        ws = await request.accept()
        async with trio.open_nursery() as nursery:
            for coro in coros:
                nursery.start_soon(coro, ws)

    async def run_server(
        self,
        config: dict,
        buses: models.Buses,
        bounds: models.WindowBounds,
    ):

        async with trio.open_nursery() as nursery:
            nursery.start_soon(
                partial(
                    serve_websocket,
                    handler=partial(
                        self.bus_server,
                        buses=buses,
                    ),
                    host=config['client_host'],
                    port=config['client_port'],
                    ssl_context=None,
                ),
            )
            nursery.start_soon(
                partial(
                    serve_websocket,
                    handler=partial(
                        self.talk_to_browser,
                        coros=[
                            partial(
                                self.listen_to_browser,
                                bounds=bounds,
                            ),
                            partial(
                                self.send_buses,
                                bounds=bounds,
                            ),
                        ],
                    ),
                    host=config['bus_server_host'],
                    port=config['bus_server_port'],
                    ssl_context=None,
                ),
            )


def main():
    parser = configargparse.ArgumentParser(description='Run Bus server')
    parser.add_argument(
        '-c',
        '--client-server',
        help='client server address',
        env_var='BUS_SERVER__SERVER_ADDRESS',
        default=DEFAULT_CLIENT_HOST,
    )
    parser.add_argument(
        '-p',
        '--port',
        type=int,
        help='clients server port',
        env_var='BUS_SERVER__PORT',
        default=DEFAULT_CLIENT_PORT,
    )
    parser.add_argument(
        '-s',
        '--bus-server',
        help='server address for bus clients',
        env_var='BUS_SERVER__BUS_CLIENT_HOST',
        default=DEFAULT_BUS_SERVER_HOST,
    )
    parser.add_argument(
        '--bp',
        type=int,
        help='clients server port',
        env_var='BUS_SERVER__BUS_CLIENT_PORT',
        default=DEFAULT_BUS_SERVER_PORT,
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        env_var='BUS_SERVER__VERBOSE',
        help='Show logging information',
    )
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    config = {
        'client_host': args.client_server,
        'client_port': args.port,
        'bus_server_host': args.bus_server,
        'bus_server_port': args.bp,
    }
    buses = models.Buses()
    bounds = models.WindowBounds(buses=buses)
    with suppress(KeyboardInterrupt):
        trio.run(
            Server().run_server,
            config,
            buses,
            bounds,
        )


if __name__ == '__main__':
    main()
