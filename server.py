import json
import logging
from contextlib import suppress
from functools import partial
from typing import Iterable

import trio
from trio_websocket import serve_websocket, ConnectionClosed

from fake_bus import main as main_fb, BUS_SEND_DELAY

logging.basicConfig(level=logging.INFO)


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
        logging.info(f'{len(self._bounded_buses)} in bound')

    def dump(self) -> str:
        buses = [bus.dump() for bus in self._bounded_buses]
        logging.info(buses)
        return json.dumps({
            "msgType": "Buses",
            "buses": buses
        })


async def bus_server(request, buses: Buses):
    ws = await request.accept()
    while True:
        try:
            message = await ws.get_message()
            buses.add_bus(message)
        except ConnectionClosed:
            break


async def send_buses(ws, bounds: WindowBounds):
    while True:
        try:
            bounds.update()
            await ws.send_message(bounds.dump())
            await trio.sleep(BUS_SEND_DELAY)
        except ConnectionClosed:
            break


async def talk_to_browser(request, buses: Buses):
    ws = await request.accept()
    bounds = WindowBounds(buses=buses)
    async with trio.open_nursery() as nursery:
        nursery.start_soon(listen_to_browser, ws, bounds)
        nursery.start_soon(send_buses, ws, bounds)


async def listen_to_browser(ws, bounds: WindowBounds):
    while True:
        try:
            message = await ws.get_message()
            message = json.loads(message)
            if 'msgType' in message and message['msgType'] == 'newBounds' and 'data' in message:
                logging.info(message)
                bounds.update(**message['data'])
        except ConnectionClosed:
            break


async def main():
    buses = Buses()
    bs = partial(bus_server, buses=buses)
    ttb = partial(talk_to_browser, buses=buses)
    async with trio.open_nursery() as nursery:
        nursery.start_soon(main_fb)
        nursery.start_soon(partial(serve_websocket, handler=bs, host='127.0.0.1', port=8080, ssl_context=None))
        nursery.start_soon(partial(serve_websocket, handler=ttb, host='127.0.0.1', port=8000, ssl_context=None))


if __name__ == '__main__':
    with suppress(KeyboardInterrupt):
        trio.run(main)
