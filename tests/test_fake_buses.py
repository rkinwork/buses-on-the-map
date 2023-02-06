import pytest
import trio

from buses.fake_bus import run_bus


async def test_run_bus(nursery):
    send_channel, receive_channel = trio.open_memory_channel(0)
    async with send_channel, receive_channel:
        await nursery.start(
            run_bus,
            send_channel.clone(),
            'text_bus_id',
            {
                'name': '9',
                'coordinates': [
                    [
                        55.706868446769,
                        37.685822404602,
                    ],
                    [
                        55.711135404128,
                        37.682009189109,
                    ],
                ],
            },
            1,
        )
        cnt = 0
        async for _ in receive_channel:
            cnt += 1
            if cnt > 3:
                if cnt > 3:
                    break

        assert cnt > 3

# check that route cycled
# check that bus coordinate passes to the channel
