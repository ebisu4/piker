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
Position info and display

"""
from __future__ import annotations
from dataclasses import dataclass
from functools import partial
from math import floor
from typing import Optional


from pyqtgraph import functions as fn

from ._annotate import LevelMarker
from ._anchors import (
    pp_tight_and_right,  # wanna keep it straight in the long run
    gpath_pin,
)
from ..calc import humanize
from ..clearing._allocate import Allocator, Position
from ._label import Label
from ._lines import LevelLine, order_line
from ._style import _font
from ._forms import FieldsForm, FillStatusBar, QLabel
from ..log import get_logger
from ..clearing._messages import Order

log = get_logger(__name__)


@dataclass
class SettingsPane:
    '''Composite set of widgets plus an allocator model for configuring
    order entry sizes and position limits per tradable instrument.

    '''
    # config for and underlying validation model
    tracker: PositionTracker
    alloc: Allocator

    # input fields
    form: FieldsForm

    # output fill status and labels
    fill_bar: FillStatusBar

    step_label: QLabel
    pnl_label: QLabel
    limit_label: QLabel

    def transform_to(self, size_unit: str) -> None:
        if self.alloc.size_unit == size_unit:
            return

    def on_selection_change(
        self,

        text: str,
        key: str,

    ) -> None:
        '''Called on any order pane drop down selection change.

        '''
        log.info(f'selection input: {text}')
        setattr(self.alloc, key, text)
        self.on_ui_settings_change(key, text)

    def on_ui_settings_change(
        self,

        key: str,
        value: str,

    ) -> bool:
        '''Called on any order pane edit field value change.

        '''
        alloc = self.alloc
        size_unit = alloc.size_unit

        # write any passed settings to allocator
        if key == 'limit':
            if size_unit == 'currency':
                alloc.currency_limit = float(value)
            else:
                alloc.units_limit = float(value)

        elif key == 'slots':
            alloc.slots = int(value)

        elif key == 'size_unit':
            # TODO: if there's a limit size unit change re-compute
            # the current settings in the new units
            pass

        elif key == 'account':
            account_name = value or 'paper'
            alloc.account = account_name

        else:
            raise ValueError(f'Unknown setting {key}')

        # read out settings and update UI
        log.info(f'settings change: {key}: {value}')

        suffix = {'currency': ' $', 'units': ' u'}[size_unit]
        limit = alloc.limit()

        # TODO: a reverse look up from the position to the equivalent
        # account(s), if none then look to user config for default?
        self.update_status_ui()

        step_size, currency_per_slot = alloc.step_sizes()

        if size_unit == 'currency':
            step_size = currency_per_slot

        self.step_label.format(
            step_size=str(humanize(step_size)) + suffix
        )
        self.limit_label.format(
            limit=str(humanize(limit)) + suffix
        )

        # update size unit in UI
        self.form.fields['size_unit'].setCurrentText(
            alloc._size_units[alloc.size_unit]
        )
        self.form.fields['account'].setCurrentText(alloc.account_name())
        self.form.fields['slots'].setText(str(alloc.slots))
        self.form.fields['limit'].setText(str(limit))

        # TODO: maybe return a diff of settings so if we can an error we
        # can have general input handling code to report it through the
        # UI in some way?
        return True

    def update_status_ui(
        self,
        size: float = None,

    ) -> None:

        alloc = self.alloc
        slots = alloc.slots
        used = alloc.slots_used(self.tracker.live_pp)

        # calculate proportion of position size limit
        # that exists and display in fill bar
        # TODO: what should we do for fractional slot pps?
        self.fill_bar.set_slots(
            slots,

            # TODO: how to show "partial" slots?
            # min(round(prop * slots), slots)
            min(used, slots)
        )

    def on_level_change_update_next_order_info(
        self,

        level: float,
        line: LevelLine,
        order: Order,

    ) -> None:
        '''A callback applied for each level change to the line
        which will recompute the order size based on allocator
        settings. this is assigned inside
        ``OrderMode.line_from_order()``

        '''
        order_info = self.alloc.next_order_info(
            startup_pp=self.tracker.startup_pp,
            live_pp=self.tracker.live_pp,
            price=level,
            action=order.action,
        )
        line.update_labels(order_info)

        # update bound-in staged order
        order.price = level
        order.size = order_info['size']
        # NOTE: the account is set at order stage time
        # inside ``OrderMode.line_from_order()``.


def position_line(

    chart: 'ChartPlotWidget',  # noqa
    size: float,
    level: float,
    color: str,

    orient_v: str = 'bottom',
    marker: Optional[LevelMarker] = None,

) -> LevelLine:
    '''Convenience routine to create a line graphic representing a "pp"
    aka the acro for a,
    "{piker, private, personal, puny, <place your p-word here>} position".

    If ``marker`` is provided it will be configured appropriately for
    the "direction" of the position.

    '''
    line = order_line(
        chart,
        level,

        # TODO: could we maybe add a ``action=None`` which
        # would be a mechanism to check a marker was passed in?

        color=color,
        highlight_on_hover=False,
        movable=False,
        hide_xhair_on_hover=False,
        only_show_markers_on_hover=False,
        always_show_labels=False,

        # explicitly disable ``order_line()`` factory's creation
        # of a level marker since we do it in this tracer thing.
        show_markers=False,
    )

    if marker:
        # configure marker to position data

        if size > 0:  # long
            style = '|<'  # point "up to" the line
        elif size < 0:  # short
            style = '>|'  # point "down to" the line

        marker.style = style

        # set marker color to same as line
        marker.setPen(line.currentPen)
        marker.setBrush(fn.mkBrush(line.currentPen.color()))
        marker.level = level
        marker.update()
        marker.show()

        # show position marker on view "edge" when out of view
        vb = line.getViewBox()
        vb.sigRangeChanged.connect(marker.position_in_view)

    line.set_level(level)

    return line


class PositionTracker:
    '''Track and display a real-time position for a single symbol
    on a chart.

    Graphically composed of a level line and marker as well as labels
    for indcating current position information. Updates are made to the
    corresponding "settings pane" for the chart's "order mode" UX.

    '''
    # inputs
    chart: 'ChartPlotWidget'  # noqa
    alloc: Allocator
    startup_pp: Position

    # allocated
    live_pp: Position
    pp_label: Label
    size_label: Label
    line: Optional[LevelLine] = None

    _color: str = 'default_lightest'

    def __init__(
        self,
        chart: 'ChartPlotWidget',  # noqa
        alloc: Allocator,
        startup_pp: Position,

    ) -> None:

        self.chart = chart
        self.alloc = alloc
        self.startup_pp = startup_pp
        self.live_pp = startup_pp.copy()

        view = chart.getViewBox()

        # literally the 'pp' (pee pee) label that's always in view
        self.pp_label = pp_label = Label(
            view=view,
            fmt_str='pp',
            color=self._color,
            update_on_range_change=False,
        )

        # create placeholder 'up' level arrow
        self._level_marker = None
        self._level_marker = self.level_marker(size=1)

        pp_label.scene_anchor = partial(
            gpath_pin,
            gpath=self._level_marker,
            label=pp_label,
        )
        pp_label.render()

        self.size_label = size_label = Label(
            view=view,
            color=self._color,

            # this is "static" label
            # update_on_range_change=False,
            fmt_str='\n'.join((
                ':{slots_used:.1f}x',
            )),

            fields={
                'slots_used': 0,
            },
        )
        size_label.render()

        size_label.scene_anchor = partial(
            pp_tight_and_right,
            label=self.pp_label,
        )

    @property
    def pane(self) -> FieldsForm:
        '''Return handle to pp side pane form.

        '''
        return self.chart.linked.godwidget.pp_pane

    def update_graphics(
        self,
        marker: LevelMarker

    ) -> None:
        '''Update all labels.

        Meant to be called from the maker ``.paint()``
        for immediate, lag free label draws.

        '''
        self.pp_label.update()
        self.size_label.update()

    def update_from_pp(
        self,
        position: Optional[Position] = None,

    ) -> None:
        '''Update graphics and data from average price and size passed in our
        EMS ``BrokerdPosition`` msg.

        '''
        # live pp updates
        pp = position or self.live_pp
        # pp.update_from_msg(msg)

        self.update_line(
            pp.avg_price,
            pp.size,
            self.chart.linked.symbol.lot_size_digits,
        )

        # label updates
        self.size_label.fields['slots_used'] = round(
            self.alloc.slots_used(pp), ndigits=1)
        self.size_label.render()

        if pp.size == 0:
            self.hide()

        else:
            self._level_marker.level = pp.avg_price

            # these updates are critical to avoid lag on view/scene changes
            self._level_marker.update()  # trigger paint
            self.pp_label.update()
            self.size_label.update()

            self.show()

            # don't show side and status widgets unless
            # order mode is "engaged" (which done via input controls)
            self.hide_info()

    def level(self) -> float:
        if self.line:
            return self.line.value()
        else:
            return 0

    def show(self) -> None:
        if self.live_pp.size:

            self.line.show()
            self.line.show_labels()

            self._level_marker.show()
            self.pp_label.show()
            self.size_label.show()

    def hide(self) -> None:
        self.pp_label.hide()
        self._level_marker.hide()
        self.size_label.hide()
        if self.line:
            self.line.hide()

    def hide_info(self) -> None:
        '''Hide details (right now just size label?) of position.

        '''
        self.size_label.hide()
        if self.line:
            self.line.hide_labels()

    # TODO: move into annoate module
    def level_marker(
        self,
        size: float,

    ) -> LevelMarker:

        if self._level_marker:
            self._level_marker.delete()

        # arrow marker
        # scale marker size with dpi-aware font size
        font_size = _font.font.pixelSize()

        # scale marker size with dpi-aware font size
        arrow_size = floor(1.375 * font_size)

        if size > 0:
            style = '|<'

        elif size < 0:
            style = '>|'

        arrow = LevelMarker(
            chart=self.chart,
            style=style,
            get_level=self.level,
            size=arrow_size,
            on_paint=self.update_graphics,
        )

        self.chart.getViewBox().scene().addItem(arrow)
        arrow.show()

        return arrow

    def update_line(
        self,
        price: float,
        size: float,
        size_digits: int,

    ) -> None:
        '''Update personal position level line.

        '''
        # do line update
        line = self.line

        if size:
            if line is None:

                # create and show a pp line
                line = self.line = position_line(
                    chart=self.chart,
                    level=price,
                    size=size,
                    color=self._color,
                    marker=self._level_marker,
                )

            else:

                line.set_level(price)
                self._level_marker.level = price
                self._level_marker.update()

            # update LHS sizing label
            line.update_labels({
                'size': size,
                'size_digits': size_digits,
                'fiat_size': round(price * size, ndigits=2),

                # TODO: per account lines on a single (or very related) symbol
                'account': self.alloc.account_name(),
            })
            line.show()

        elif line:  # remove pp line from view if it exists on a net-zero pp
            line.delete()
            self.line = None
