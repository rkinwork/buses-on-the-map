import json
import logging
from contextlib import suppress
from functools import partial
from typing import Iterable, Type
import argparse

import trio
from jsonschema import Draft7Validator
from trio_websocket import serve_websocket, ConnectionClosed

from fake_bus import main as main_fb, BUS_SEND_DELAY

DEFAULT_CLIENT_HOST = '127.0.0.1'
DEFAULT_CLIENT_PORT = 8080
DEFAULT_BUS_SERVER_HOST = '127.0.0.1'
DEFAULT_BUS_SERVER_PORT = 8000

DEFAULT_CONFIG = {
    'client_host': DEFAULT_CLIENT_HOST,
    'client_port': DEFAULT_CLIENT_PORT,
    'bus_server_host': DEFAULT_BUS_SERVER_HOST,
    'bus_server_port': DEFAULT_BUS_SERVER_PORT,
}

WINDOW_BOUND_SCHEMA = {
    "type": "object",
    "properties": {
        "msgType": {
            "type": "string",
            "pattern": "^newBounds$",
        },
        "data": {
            "type": "object",
            "properties": {
                "south_lat": {"type": "number",
                              "minimum": -90,
                              "maximum": 90,
                              },
                'north_lat': {"type": "number",
                              "minimum": -90,
                              "maximum": 90,
                              },
                'west_lng': {"type": "number",
                             "minimum": -90,
                             "maximum": 90,
                             },
                'east_lng': {"type": "number",
                             "minimum": -90,
                             "maximum": 90,
                             },
            },
            "required": ["south_lat", "north_lat", "west_lng", "east_lng"]

        },
    },
    "required": ["msgType", "data"]
}

BUS_SCHEMA = {
    "type": "object",
    "properties": {
        "busId": {
            "type": "string",
        },
        'lat': {
            "type": "number",
            "minimum": -90,
            "maximum": 90,
        },
        'lng': {
            "type": "number",
            "minimum": -90,
            "maximum": 90,
        },
        'route': {
            "type": "string",
        },
    },
    "required": [
        "busId",
        "lat",
        "lng",
        "route",
    ]
}


class MessageException(Exception):
    def __init__(self, expts: Iterable[str], message='Problems with parsing and validating response'):
        self._exceptions_messages = expts
        super().__init__(message)

    def __iter__(self):
        return (message for message in self._exceptions_messages)


class BoundsReadMessageException(MessageException):
    pass


class BusReadMessageException(MessageException):
    pass


def validate_message(schema: dict, exception_cls: Type[MessageException], message: str):
    try:
        message = json.loads(message)
    except json.JSONDecodeError as e:
        raise exception_cls(expts=["not valid JSON"])
    errors = sorted(Draft7Validator(schema).iter_errors(message), key=str)
    if errors:
        raise exception_cls(expts=[error.message for error in errors])


class Bus:

    def __init__(self,
                 bus_id: str,
                 lat: float,
                 lng: float,
                 route: str
                 ):
        self.bus_id = bus_id
        self.lat = lat
        self.lng = lng
        self.route = route

    @classmethod
    def parse_response(cls, response):
        validate_message(schema=BUS_SCHEMA,
                         exception_cls=BusReadMessageException,
                         message=response)
        response = json.loads(response)

        return cls(bus_id=response['busId'],
                   lat=response['lat'],
                   lng=response['lng'],
                   route=response['route'],
                   )

    def dump(self):
        return {
            'busId': self.bus_id,
            'lat': self.lat,
            'lng': self.lng,
            'route': self.route,
        }


class Buses:
    def __init__(self):
        self.buses: dict[str, Bus] = {}

    def add_bus(self, message: str):
        bus = Bus.parse_response(message)
        self.buses[bus.bus_id] = bus

    def __iter__(self) -> Iterable[Bus]:
        return (bus for bus in self.buses.values())


class WindowBounds:

    def __init__(self,
                 buses: Buses,
                 south_lat: float = 0,
                 north_lat: float = 0,
                 west_lng: float = 0,
                 east_lng: float = 0,
                 ):
        self._buses = buses
        self._south_lat = south_lat
        self._north_lat = north_lat
        self._west_lng = west_lng
        self._east_lng = east_lng
        self._bounded_buses: Iterable[Bus] = []

    def is_inside(self, bus: Bus) -> bool:
        res = [
            self._south_lat < bus.lat,
            self._north_lat > bus.lat,
            self._east_lng > bus.lng,
            self._west_lng < bus.lng,
        ]
        return all(res)

    def update(self,
               south_lat: float = None,
               north_lat: float = None,
               west_lng: float = None,
               east_lng: float = None,
               ):
        if all([south_lat is not None,
                north_lat is not None,
                west_lng is not None,
                west_lng is not None,
                ]):
            self._south_lat = south_lat
            self._north_lat = north_lat
            self._west_lng = west_lng
            self._east_lng = east_lng
        self._bounded_buses = [bus for bus in self._buses if self.is_inside(bus)]
        logging.debug(f'{len(self._bounded_buses)} in bound')

    def dump(self) -> str:
        buses = [bus.dump() for bus in self._bounded_buses]
        logging.debug(buses)
        return json.dumps({
            "msgType": "Buses",
            "buses": buses
        })

    def process_message(self, message: str):
        validate_message(schema=WINDOW_BOUND_SCHEMA,
                         exception_cls=BoundsReadMessageException,
                         message=message)

        message = json.loads(message)
        self.update(**message['data'])


async def bus_server(request, buses: Buses):
    ws = await request.accept()
    while True:
        try:
            message = await ws.get_message()
        except ConnectionClosed:
            break

        try:
            buses.add_bus(message)
        except BusReadMessageException as e:
            await send_error_message(ws, e)


async def send_buses(ws, bounds: WindowBounds):
    while True:
        try:
            bounds.update()
            await ws.send_message(bounds.dump())
            await trio.sleep(BUS_SEND_DELAY)
        except ConnectionClosed:
            break


async def talk_to_browser(request, coros: Iterable):
    ws = await request.accept()
    async with trio.open_nursery() as nursery:
        [nursery.start_soon(coro, ws) for coro in coros]


async def send_error_message(ws, e: MessageException):
    msg = json.dumps({
        'errors': [message for message in e],
        "msgType": "Errors",
    })
    await ws.send_message(msg)


async def listen_to_browser(ws, bounds: WindowBounds):
    while True:
        try:
            message = await ws.get_message()
        except ConnectionClosed:
            break

        try:
            bounds.process_message(message=message)
        except BoundsReadMessageException as e:
            await send_error_message(ws, e)


async def main(config: dict = None):
    config = config or DEFAULT_CONFIG
    buses = Buses()
    bounds = WindowBounds(buses=buses)
    talk_to_browser_coros = [
        partial(listen_to_browser, bounds=bounds),
        partial(send_buses, bounds=bounds),
    ]
    ttb = partial(talk_to_browser, coros=talk_to_browser_coros)
    bs = partial(bus_server, buses=buses)
    async with trio.open_nursery() as nursery:
        nursery.start_soon(main_fb)
        nursery.start_soon(partial(serve_websocket,
                                   handler=bs,
                                   host=config['client_host'],
                                   port=config['client_port'],
                                   ssl_context=None,
                                   )
                           )
        nursery.start_soon(partial(serve_websocket,
                                   handler=ttb,
                                   host=config['bus_server_host'],
                                   port=config['bus_server_port'],
                                   ssl_context=None,
                                   )
                           )


def parse_config() -> dict:
    parser = argparse.ArgumentParser(description='Run Bus server')
    parser.add_argument("-c", "--client-server",
                        help="client server address", default=DEFAULT_CLIENT_HOST)
    parser.add_argument("-p", "--port", type=int, help="clients server port", default=DEFAULT_CLIENT_PORT)
    parser.add_argument("-s", "--bus-server",
                        help="client server address", default=DEFAULT_BUS_SERVER_HOST)
    parser.add_argument("--bp", type=int, help="clients server port", default=DEFAULT_BUS_SERVER_PORT)
    parser.add_argument("-v", "--verbose", action="store_true", help="Show logging information")
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    return {
        'client_host': args.client_server,
        'client_port': args.port,
        'bus_server_host': args.bus_server,
        'bus_server_port': args.bp,
    }


if __name__ == '__main__':
    with suppress(KeyboardInterrupt):
        trio.run(main, parse_config())
