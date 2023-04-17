# piker: trading gear for hackers
# Copyright (C) Tyler Goodlet (in stewardship for pikers)

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

'''
Trade and transaction ledger processing.

'''
from __future__ import annotations
from collections import UserDict
from contextlib import contextmanager as cm
from pathlib import Path
import time
from typing import (
    Any,
    Iterator,
    Union,
    Generator
)

from pendulum import (
    datetime,
    parse,
)
import tomli
import toml

from .. import config
from ..data.types import Struct
from ..log import get_logger
from ._mktinfo import (
    Symbol,  # legacy
    MktPair,
    Asset,
)

log = get_logger(__name__)


class Transaction(Struct, frozen=True):

    # TODO: unify this with the `MktPair`,
    # once we have that as a required field,
    # we don't really need the fqsn any more..
    fqsn: str

    tid: Union[str, int]  # unique transaction id
    size: float
    price: float
    cost: float  # commisions or other additional costs
    dt: datetime

    # TODO: we can drop this right since we
    # can instead expect the backend to provide this
    # via the `MktPair`?
    expiry: datetime | None = None

    # remap for back-compat
    @property
    def fqme(self) -> str:
        return self.fqsn

    # TODO: drop the Symbol type, construct using
    # t.sys (the transaction system)

    # the underlying "transaction system", normally one of a ``MktPair``
    # (a description of a tradable double auction) or a ledger-recorded
    # ("ledger" in any sense as long as you can record transfers) of any
    # sort) ``Asset``.
    sym: MktPair | Asset | Symbol | None = None

    @property
    def sys(self) -> Symbol:
        return self.sym

    # (optional) key-id defined by the broker-service backend which
    # ensures the instrument-symbol market key for this record is unique
    # in the "their backend/system" sense; i.e. this uid for the market
    # as defined (internally) in some namespace defined by the broker
    # service.
    bs_mktid: str | int | None = None

    def to_dict(self) -> dict:
        dct = super().to_dict()

        # TODO: switch to sys!
        dct.pop('sym')

        # ensure we use a pendulum formatted
        # ISO style str here!@
        dct['dt'] = str(self.dt)
        return dct


class TransactionLedger(UserDict):
    '''
    Very simple ``dict`` wrapper + ``pathlib.Path`` handle to
    a TOML formatted transaction file for enabling file writes
    dynamically whilst still looking exactly like a ``dict`` from the
    outside.

    '''
    def __init__(
        self,
        ledger_dict: dict,
        file_path: Path,

    ) -> None:
        self.file_path = file_path
        super().__init__(ledger_dict)

    def write_config(self) -> None:
        '''
        Render the self.data ledger dict to it's TML file form.

        '''
        with self.file_path.open(mode='w') as fp:

            # rewrite the key name to fqme if needed
            fqsn: str = self.data.get('fqsn')
            if fqsn:
                self.data['fqme'] = fqsn

            toml.dump(self.data, fp)

    def update_from_t(
        self,
        t: Transaction,
    ) -> None:
        self.data[t.tid] = t.to_dict()

    def iter_trans(
        self,
        broker: str = 'paper',
        mkt_by_fqme: dict[str, MktPair] | None = None,

    ) -> Generator[
        tuple[str, Transaction],
        None,
        None,
    ]:
        '''
        Deliver trades records in ``(key: str, t: Transaction)``
        form via generator.

        '''
        if broker != 'paper':
            raise NotImplementedError('Per broker support not dun yet!')

            # TODO: lookup some standard normalizer
            # func in the backend?
            # from ..brokers import get_brokermod
            # mod = get_brokermod(broker)
            # trans_dict = mod.norm_trade_records(self.data)

            # NOTE: instead i propose the normalizer is
            # a one shot routine (that can be lru cached)
            # and instead call it for each entry incrementally:
            # normer = mod.norm_trade_record(txdict)

        for tid, txdict in self.data.items():
            # special field handling for datetimes
            # to ensure pendulum is used!
            fqme = txdict.get('fqme', txdict['fqsn'])
            dt = parse(txdict['dt'])
            expiry = txdict.get('expiry')
            mkt_by_fqme = mkt_by_fqme or {}

            yield (
                tid,
                Transaction(
                    fqsn=fqme,
                    tid=txdict['tid'],
                    dt=dt,
                    price=txdict['price'],
                    size=txdict['size'],
                    cost=txdict.get('cost', 0),
                    bs_mktid=txdict['bs_mktid'],

                    # optional
                    sym=mkt_by_fqme[fqme] if mkt_by_fqme else None,
                    expiry=parse(expiry) if expiry else None,
                )
            )

    def to_trans(
        self,
        broker: str = 'paper',

        **kwargs,

    ) -> dict[str, Transaction]:
        '''
        Return the entire output from ``.iter_trans()`` in a ``dict``.

        '''
        return dict(
            self.iter_trans(
                broker,
                **kwargs,
            )
        )


@cm
def open_trade_ledger(
    broker: str,
    account: str,

) -> Generator[dict, None, None]:
    '''
    Indempotently create and read in a trade log file from the
    ``<configuration_dir>/ledgers/`` directory.

    Files are named per broker account of the form
    ``<brokername>_<accountname>.toml``. The ``accountname`` here is the
    name as defined in the user's ``brokers.toml`` config.

    '''
    ldir: Path = config._config_dir / 'ledgers'
    if not ldir.is_dir():
        ldir.mkdir()

    fname = f'trades_{broker}_{account}.toml'
    tradesfile: Path = ldir / fname

    if not tradesfile.is_file():
        log.info(
            f'Creating new local trades ledger: {tradesfile}'
        )
        tradesfile.touch()

    with tradesfile.open(mode='rb') as cf:
        start = time.time()
        ledger_dict = tomli.load(cf)
        log.info(f'Ledger load took {time.time() - start}s')
        cpy = ledger_dict.copy()

    ledger = TransactionLedger(
        ledger_dict=cpy,
        file_path=tradesfile,
    )

    try:
        yield ledger
    finally:
        if ledger.data != ledger_dict:

            # TODO: show diff output?
            # https://stackoverflow.com/questions/12956957/print-diff-of-python-dictionaries
            log.info(f'Updating ledger for {tradesfile}:\n')
            ledger.write_config()


def iter_by_dt(
    clears: dict[str, Any],

) -> Iterator[tuple[str, dict]]:
    '''
    Iterate entries of a ``clears: dict`` table sorted by entry recorded
    datetime presumably set at the ``'dt'`` field in each entry.

    '''
    for tid, data in sorted(
        list(clears.items()),
        key=lambda item: item[1]['dt'],
    ):
        yield tid, data
