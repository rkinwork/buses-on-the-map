import json
import logging
from contextlib import suppress
from functools import partial

import trio
from trio_websocket import serve_websocket, ConnectionClosed


from fake_bus import main as main_fb, BUS_SEND_DELAY

logging.basicConfig(level=logging.INFO)


class Buses:
    def __init__(self):
        self.buses = {}

    def add_bus(self, message: str):
        message = json.loads(message)
        logging.info(message)
        self.buses[message['busId']] = message

    def dump(self) -> str:
        return json.dumps({
            "msgType": "Buses",
            "buses": list(self.buses.values())
        })


async def bus_server(request, buses: Buses):
    ws = await request.accept()
    while True:
        try:
            message = await ws.get_message()
            buses.add_bus(message)
        except ConnectionClosed:
            break


async def talk_to_browser(request, buses: Buses):
    ws = await request.accept()
    while True:
        try:
            await ws.send_message(buses.dump())
            await trio.sleep(BUS_SEND_DELAY)
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
