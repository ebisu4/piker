# piker: trading gear for hackers
# Copyright (C) Tyler Goodlet (in stewardship for piker0)

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
In suit parlance: "Execution management systems"

"""
from dataclasses import dataclass, field
from typing import (
    AsyncIterator, Dict, Callable, Tuple,
    Any,
)
# import uuid

import trio
from trio_typing import TaskStatus
import tractor

from . import data
from .log import get_logger
from .data._source import Symbol


log = get_logger(__name__)

# setup local ui event streaming channels for request/resp
# streamging with EMS daemon
_to_ems, _from_order_book = trio.open_memory_channel(100)


@dataclass
class OrderBook:
    """Buy-side (client-side ?) order book ctl and tracking.

    A style similar to "model-view" is used here where this api is
    provided as a supervised control for an EMS actor which does all the
    hard/fast work of talking to brokers/exchanges to conduct
    executions.

    Currently, mostly for keeping local state to match the EMS and use
    received events to trigger graphics updates.

    """
    _sent_orders: Dict[str, dict] = field(default_factory=dict)
    _confirmed_orders: Dict[str, dict] = field(default_factory=dict)

    _to_ems: trio.abc.SendChannel = _to_ems
    _from_order_book: trio.abc.ReceiveChannel = _from_order_book

    def on_fill(self, uuid: str) -> None:
        cmd = self._sent_orders[uuid]
        log.info(f"Order executed: {cmd}")
        self._confirmed_orders[uuid] = cmd

    def alert(
        self,
        uuid: str,
        symbol: 'Symbol',
        price: float
    ) -> str:
        # XXX: should make this an explicit attr
        # it's assigned inside ``.add_plot()``
        # lc = self.view.linked_charts

        # uid = str(uuid.uuid4())
        cmd = {
            'msg': 'alert',
            'price': price,
            'symbol': symbol.key,
            'brokers': symbol.brokers,
            'oid': uuid,
        }
        self._sent_orders[uuid] = cmd
        self._to_ems.send_nowait(cmd)

    async def buy(self, price: float) -> str:
        ...

    async def sell(self, price: float) -> str:
        ...

    async def cancel(self, oid: str) -> bool:
        """Cancel an order (or alert) from the EMS.

        """
        ...

    # higher level operations

    async def transmit_to_broker(self, price: float) -> str:
        ...

    async def modify(self, oid: str, price) -> bool:
        ...


_orders: OrderBook = None


def get_orders(emsd_uid: Tuple[str, str] = None) -> OrderBook:

    if emsd_uid is not None:
        # TODO: read in target emsd's active book on startup
        pass

    global _orders

    if _orders is None:
        _orders = OrderBook()

    return _orders


# TODO: make this a ``tractor.msg.pub``
async def send_order_cmds():
    """Order streaming task: deliver orders transmitted from UI
    to downstream consumers.

    This is run in the UI actor (usually the one running Qt).
    The UI simply delivers order messages to the above ``_to_ems``
    send channel (from sync code using ``.send_nowait()``), these values
    are pulled from the channel here and send to any consumer(s).

    This effectively makes order messages look like they're being
    "pushed" from the parent to the EMS actor.

    """

    global _from_order_book
    # book = get_orders()

    async for cmd in _from_order_book:

        # send msg over IPC / wire
        log.info(f'sending order cmd: {cmd}')
        yield cmd

        # lc = order['chart']
        # symbol = order['symol']
        # msg = order['msg']
        # price = order['price']
        # oid = order['oid']

        # TODO
        # oid = str(uuid.uuid4())

        # cmd = {
        #     'price': price,
        #     'action': 'alert',
        #     'symbol': symbol.key,
        #     'brokers': symbol.brokers,
        #     'msg': msg,
        #     'price': price,
        #     'oid': oid,
        # }

        # book._sent_orders[oid] = cmd


# TODO: numba all of this
def mk_check(trigger_price, known_last) -> Callable[[float, float], bool]:
    """Create a predicate for given ``exec_price`` based on last known
    price, ``known_last``.

    This is an automatic alert level thunk generator based on where the
    current last known value is and where the specified value of
    interest is; pick an appropriate comparison operator based on
    avoiding the case where the a predicate returns true immediately.

    """
    # str compares:
    # https://stackoverflow.com/questions/46708708/compare-strings-in-numba-compiled-function

    if trigger_price >= known_last:

        def check_gt(price: float) -> bool:
            return price >= trigger_price

        return check_gt, 'down'

    elif trigger_price <= known_last:

        def check_lt(price: float) -> bool:
            return price <= trigger_price

        return check_lt, 'up'


@dataclass
class _ExecBook:
    """EMS-side execution book.

    Contains conditions for executions (aka "orders").
    A singleton instance is created per EMS actor (for now).

    """
    # levels which have an executable action (eg. alert, order, signal)
    orders: Dict[
        Tuple[str, str],
        Tuple[
            # predicates
            Callable[[float], bool],

            # actions
            Callable[[float], Dict[str, Any]],

        ]
    ] = field(default_factory=dict)

    # tracks most recent values per symbol each from data feed
    lasts: Dict[
        Tuple[str, str],
        float
    ] = field(default_factory=dict)


_book = None


def get_book() -> _ExecBook:
    global _book

    if _book is None:
        _book = _ExecBook()

    return _book


async def exec_orders(
    ctx: tractor.Context,
    broker: str,
    symbol: str,
    exec_price: float,
    task_status: TaskStatus[dict] = trio.TASK_STATUS_IGNORED,
) -> AsyncIterator[dict]:

    async with data.open_feed(
        broker,
        [symbol],
        loglevel='info',
    ) as feed:

        # TODO: get initial price

        first_quote = await feed.receive()

        book = get_book()
        book.lasts[(broker, symbol)] = first_quote[symbol]['last']

        task_status.started(first_quote)

        # shield this field so the remote brokerd does not get cancelled
        stream = feed.stream

        with stream.shield():
            async for quotes in stream:

                ##############################
                # begin price actions sequence
                # XXX: optimize this for speed
                ##############################

                for sym, quote in quotes.items():

                    execs = book.orders.get((broker, sym))

                    for tick in quote.get('ticks', ()):
                        price = tick.get('price')
                        if price < 0:
                            # lel, fuck you ib
                            continue

                        # update to keep new cmds informed
                        book.lasts[(broker, symbol)] = price

                        if not execs:
                            continue

                        for oid, pred, name, cmd in tuple(execs):

                            # push trigger msg back to parent as an "alert"
                            # (mocking for eg. a "fill")
                            if pred(price):

                                cmd['name'] = name
                                cmd['index'] = feed.shm._last.value - 1
                                # current shm array index
                                cmd['trigger_price'] = price

                                await ctx.send_yield(cmd)
                                # await ctx.send_yield({
                                #     'type': 'alert',
                                #     'price': price,
                                #     # current shm array index
                                #     'index': feed.shm._last.value - 1,
                                #     'name': name,
                                #     'oid': oid,
                                # })

                                print(
                                    f"GOT ALERT FOR {exec_price} @ \n{tick}\n")

                                print(f'removing pred for {oid}')
                                execs.remove((oid, pred, name, cmd))

                                print(f'execs are {execs}')

        # feed teardown


@tractor.stream
async def stream_and_route(ctx, ui_name):
    """Order router (sub)actor entrypoint.

    This is the daemon (child) side routine which starts an EMS
    runtime per broker/feed and and begins streaming back alerts
    from executions back to subscribers.

    """
    actor = tractor.current_actor()
    book = get_book()

    # new router entry point
    async with tractor.wait_for_actor(ui_name) as portal:

        # spawn one task per broker feed
        async with trio.open_nursery() as n:

            async for cmd in await portal.run(send_order_cmds):

                msg = cmd['msg']

                if msg == 'cancel':
                    # TODO:
                    pass

                trigger_price = cmd['price']
                sym = cmd['symbol']
                brokers = cmd['brokers']
                oid = cmd['oid']

                if msg == 'alert':
                    log.info(f'Alert {cmd} received in {actor.uid}')

                broker = brokers[0]
                last = book.lasts.get((broker, sym))

                if last is None:  # spawn new brokerd feed task

                    quote = await n.start(
                        exec_orders,
                        ctx,
                        # TODO: eventually support N-brokers
                        broker,
                        sym,
                        trigger_price,
                    )
                    print(f"received first quote {quote}")

                last = book.lasts[(broker, sym)]
                print(f'Known last is {last}')

                # Auto-gen scanner predicate:
                # we automatically figure out what the alert check condition
                # should be based on the current first price received from the
                # feed, instead of being like every other shitty tina platform
                # that makes the user choose the predicate operator.
                pred, name = mk_check(trigger_price, last)

                # create list of executions on first entry
                book.orders.setdefault((broker, sym), []).append(
                    (oid, pred, name, cmd)
                )

                # ack-respond that order is live
                await ctx.send_yield({'msg': 'ack', 'oid': oid})

            # continue and wait on next order cmd


async def spawn_router_stream_alerts(
    order_mode,
    symbol: Symbol,
    # lines: 'LinesEditor',
    task_status: TaskStatus[str] = trio.TASK_STATUS_IGNORED,
) -> None:
    """Spawn an EMS daemon and begin sending orders and receiving
    alerts.

    """

    actor = tractor.current_actor()
    subactor_name = 'emsd'

    # TODO: add ``maybe_spawn_emsd()`` for this
    async with tractor.open_nursery() as n:

        portal = await n.start_actor(
            subactor_name,
            rpc_module_paths=[__name__],
        )
        stream = await portal.run(
            stream_and_route,
            ui_name=actor.name
        )

        async with tractor.wait_for_actor(subactor_name):
            # let parent task continue
            task_status.started(_to_ems)

        # begin the trigger-alert stream
        # this is where we receive **back** messages
        # about executions **from** the EMS actor
        async for alert in stream:

            # delete the line from view
            oid = alert['oid']
            msg_type = alert['msg']

            if msg_type == 'ack':
                print(f"order accepted: {alert}")

                # show line label once order is live
                order_mode.lines.commit_line(oid)

                continue

            order_mode.arrows.add(
                oid,
                alert['index'],
                alert['price'],
                pointing='up' if alert['name'] == 'up' else 'down'
            )

            # print(f'_lines: {_lines}')
            print(f'deleting line with oid: {oid}')

            # delete level from view
            order_mode.lines.remove_line(uuid=oid)

            # chart._vb._lines_editor
            # _lines.pop(oid).delete()

            # TODO: this in another task?
            # not sure if this will ever be a bottleneck,
            # we probably could do graphics stuff first tho?

            # XXX: linux only for now
            result = await trio.run_process(
                [
                    'notify-send',
                    '-u', 'normal',
                    '-t', '10000',
                    'piker',
                    f'alert: {alert}',
                ],
            )
            log.runtime(result)

            # do we need this?
            # await _from_ems.put(alert)
