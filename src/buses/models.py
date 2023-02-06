import json
import logging
from dataclasses import dataclass
from typing import Iterable, Type

from jsonschema import Draft7Validator

from buses import buses_errors

WINDOW_BOUND_SCHEMA = """{
   "type":"object",
   "properties":{
      "msgType":{
         "type":"string",
         "pattern":"^newBounds$"
      },
      "data":{
         "type":"object",
         "properties":{
            "south_lat":{
               "type":"number",
               "minimum":-90,
               "maximum":90
            },
            "north_lat":{
               "type":"number",
               "minimum":-90,
               "maximum":90
            },
            "west_lng":{
               "type":"number",
               "minimum":-90,
               "maximum":90
            },
            "east_lng":{
               "type":"number",
               "minimum":-90,
               "maximum":90
            }
         },
         "required":[
            "south_lat",
            "north_lat",
            "west_lng",
            "east_lng"
         ]
      }
   },
   "required":[
      "msgType",
      "data"
   ]
}
"""

BUS_SCHEMA = """{
   "type":"object",
   "properties":{
      "busId":{
         "type":"string"
      },
      "lat":{
         "type":"number",
         "minimum":-90,
         "maximum":90
      },
      "lng":{
         "type":"number",
         "minimum":-90,
         "maximum":90
      },
      "route":{
         "type":"string"
      }
   },
   "required":[
      "busId",
      "lat",
      "lng",
      "route"
   ]
}
"""


def parse_message(
    validator: Draft7Validator,
    exception_cls: Type[buses_errors.MessageError],
    message: str,
) -> dict:
    try:
        message = json.loads(message)
    except json.JSONDecodeError:
        raise exception_cls(expts=('not valid JSON',))
    errors = sorted(validator.iter_errors(message), key=str)
    if errors:
        raise exception_cls(expts=[error.message for error in errors])
    return message


WINDOW_BOUND_VALIDATOR = Draft7Validator(json.loads(WINDOW_BOUND_SCHEMA))
BUS_VALIDATOR = Draft7Validator(json.loads(BUS_SCHEMA))


class Bus:

    def __init__(
        self,
        bus_id: str,
        lat: float,
        lng: float,
        route: str,
    ):
        self.bus_id = bus_id
        self.lat = lat
        self.lng = lng
        self.route = route

    @classmethod
    def parse_response(cls, response):
        parsed = parse_message(
            validator=BUS_VALIDATOR,
            exception_cls=buses_errors.BusReadMessageError,
            message=response,
        )

        return cls(
            bus_id=parsed['busId'],
            lat=parsed['lat'],
            lng=parsed['lng'],
            route=parsed['route'],
        )

    def dump(self):
        return {
            'busId': self.bus_id,
            'lat': self.lat,
            'lng': self.lng,
            'route': self.route,
        }

    def to_json(self):
        return json.dumps(
            self.dump(),
            ensure_ascii=True,
        )


class Buses:
    def __init__(self):
        self.buses: dict[str, Bus] = {}

    def add_bus(self, message: str):
        bus = Bus.parse_response(message)
        self.buses[bus.bus_id] = bus

    def __iter__(self) -> Iterable[Bus]:
        return (bus for bus in self.buses.values())


@dataclass
class Bound:
    south_lat: float = 0
    north_lat: float = 0
    west_lng: float = 0
    east_lng: float = 0


class WindowBounds:

    def __init__(
        self,
        buses: Buses,
        bound: Bound = None,
    ):
        self._buses = buses
        self._bound = bound or Bound()
        self._bounded_buses: Iterable[Bus] = ()

    def is_inside(self, bus: Bus) -> bool:
        res = [
            self._bound.south_lat < bus.lat,
            self._bound.north_lat > bus.lat,
            self._bound.east_lng > bus.lng,
            self._bound.west_lng < bus.lng,
        ]
        return all(res)

    def update(
        self,
        bound: Bound = None,
    ):
        if bound is not None:
            self._bound = bound
        self._bounded_buses = tuple(
            bus for bus in self._buses if self.is_inside(bus)
        )
        logging.debug('{cnt} in bound'.format(cnt=len(self._bounded_buses)))

    def dump(self) -> str:
        buses = tuple(bus.dump() for bus in self._bounded_buses)
        logging.debug(buses)
        return json.dumps({
            'msgType': 'Buses',
            'buses': buses,
        })

    def process_message(self, message: str):
        parsed_message = parse_message(
            validator=WINDOW_BOUND_VALIDATOR,
            exception_cls=buses_errors.BoundsReadMessageError,
            message=message,
        )
        self.update(bound=Bound(**parsed_message['data']))
