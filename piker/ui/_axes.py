"""
Chart axes graphics and behavior.
"""
from typing import List

import pandas as pd
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import QPointF

from ._style import _font, hcolor
from ..data._source import float_digits

_axis_pen = pg.mkPen(hcolor('bracket'))


class PriceAxis(pg.AxisItem):

    def __init__(
        self,
    ) -> None:
        super().__init__(orientation='right')
        self.setTickFont(_font)
        self.setStyle(**{
            'textFillLimits': [(0, 0.666)],
            'tickFont': _font,
            # 'tickTextWidth': 100,
            # 'tickTextHeight': 20,
            # 'tickTextWidth': 40,
            # 'autoExpandTextSpace': True,
            # 'maxTickLength': -20,
            # 'stopAxisAtTick': (True, True),  # doesn't work well on price
        })
        # self.setLabel(**{'font-size': '10pt'})
        self.setTickFont(_font)
        self.setPen(_axis_pen)

        self.setWidth(40)

    # XXX: drop for now since it just eats up h space

    def tickStrings(self, vals, scale, spacing):
        digits = float_digits(spacing * scale)

        # print(f'vals: {vals}\nscale: {scale}\nspacing: {spacing}')
        # print(f'digits: {digits}')

        return [
            ('{:,.%df}' % digits).format(v).replace(',', ' ') for v in vals
        ]


class DynamicDateAxis(pg.AxisItem):
    # time formats mapped by seconds between bars
    tick_tpl = {
        60*60*24: '%Y-%b-%d',
        60: '%H:%M',
        30: '%H:%M:%S',
        5: '%H:%M:%S',
    }

    def __init__(
        self,
        linked_charts,
        *args,
        **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.linked_charts = linked_charts
        self.setTickFont(_font)
        self.setPen(_axis_pen)

        # default styling
        self.setStyle(**{
            # tickTextOffset=4,
            'textFillLimits': [(0, 0.666)],
            'tickFont': _font,
        })
        self.setHeight(11)

    def _indexes_to_timestrs(
        self,
        indexes: List[int],
    ) -> List[str]:
        bars = self.linked_charts.chart._array
        times = bars['time']
        bars_len = len(bars)
        # delay = times[-1] - times[times != times[-1]][-1]
        delay = times[-1] - times[-2]

        epochs = times[list(
            map(int, filter(lambda i: i < bars_len, indexes))
        )]
        # TODO: **don't** have this hard coded shift to EST
        dts = pd.to_datetime(epochs, unit='s')  # - 4*pd.offsets.Hour()
        return dts.strftime(self.tick_tpl[delay])

    def tickStrings(self, values: List[float], scale, spacing):
        return self._indexes_to_timestrs(values)


class AxisLabel(pg.GraphicsObject):

    _font = _font
    _w_margin = 0
    _h_margin = 3

    def __init__(
        self,
        parent: pg.GraphicsObject,
        digits: int = 2,
        bg_color: str = 'bracket',
        fg_color: str = 'black',
        opacity: int = 1,
    ):
        super().__init__(parent)
        self.parent = parent
        self.opacity = opacity
        self.label_str = ''
        self.digits = digits
        self._txt_br: QtCore.QRect = None

        self.bg_color = pg.mkColor(hcolor(bg_color))
        self.fg_color = pg.mkColor(hcolor(fg_color))

        self.pic = QtGui.QPicture()
        p = QtGui.QPainter(self.pic)

        self.rect = None

        p.setPen(self.fg_color)
        p.setOpacity(self.opacity)

        self.setFlag(self.ItemIgnoresTransformations)

    def _size_br_from_str(self, value: str) -> None:
        """Do our best to render the bounding rect to a set margin
        around provided string contents.

        """
        txt_br = self._font._fm.boundingRect(value)
        h, w = txt_br.height(), txt_br.width()
        self.rect = QtCore.QRectF(
            0, 0,
            w + self._w_margin,
            h + self._h_margin
        )

    def paint(self, p, option, widget):
        p.drawPicture(0, 0, self.pic)

        if self.label_str:

            if not self.rect:
                self._size_br_from_str(self.label_str)

            p.setFont(_font)
            p.setPen(self.fg_color)
            p.fillRect(self.rect, self.bg_color)

            # this adds a nice black outline around the label for some odd
            # reason; ok by us
            p.drawRect(self.rect)

            p.drawText(option.rect, self.text_flags, self.label_str)

    def boundingRect(self):  # noqa
        return self.rect or QtCore.QRectF()

    # uggggghhhh

    def tick_to_string(self, tick_pos):
        raise NotImplementedError()

    def update_label(self, evt_post, point_view):
        raise NotImplementedError()

    # end uggggghhhh


# _common_text_flags = (
#     QtCore.Qt.TextDontClip |
#     QtCore.Qt.AlignCenter |
#     QtCore.Qt.AlignTop |
#     QtCore.Qt.AlignHCenter |
#     QtCore.Qt.AlignVCenter
# )


class XAxisLabel(AxisLabel):

    _w_margin = 8

    text_flags = (
        QtCore.Qt.TextDontClip
        | QtCore.Qt.AlignCenter
        # | QtCore.Qt.AlignTop
        # | QtCore.Qt.AlignVCenter
        # | QtCore.Qt.AlignHCenter
    )

    def update_label(
        self,
        abs_pos: QPointF,  # scene coords
        data: float,  # data for text
        offset: int = 1  # if have margins, k?
    ) -> None:
        timestrs = self.parent._indexes_to_timestrs([int(data)])
        if not timestrs.any():
            return
        self.label_str = timestrs[0]
        width = self.boundingRect().width()
        new_pos = QPointF(abs_pos.x() - width / 2 - offset, 0)
        self.setPos(new_pos)


class YAxisLabel(AxisLabel):

    text_flags = (
        QtCore.Qt.AlignLeft
        | QtCore.Qt.TextDontClip
        | QtCore.Qt.AlignVCenter
    )

    def tick_to_string(self, tick_pos):
        # WTF IS THIS FORMAT?
        return ('{: ,.%df}' % self.digits).format(tick_pos).replace(',', ' ')

    def update_label(
        self,
        abs_pos: QPointF,  # scene coords
        data: float,  # data for text
        offset: int = 1  # if have margins, k?
    ) -> None:
        self.label_str = self.tick_to_string(data)
        height = self.boundingRect().height()
        new_pos = QPointF(0, abs_pos.y() - height / 2 - offset)
        self.setPos(new_pos)


class YSticky(YAxisLabel):
    """Y-axis label that sticks to where it's placed despite chart resizing.
    """
    def __init__(
        self,
        chart,
        *args,
        **kwargs
    ) -> None:

        super().__init__(*args, **kwargs)

        self._chart = chart
        chart.sigRangeChanged.connect(self.update_on_resize)

    def update_on_resize(self, vr, r):
        # TODO: add an `.index` to the array data-buffer layer
        # and make this way less shitty...
        chart = self._chart
        a = chart._array
        fields = a.dtype.fields
        if fields and 'close' in fields:
            index, last = a[-1][['index', 'close']]
        else:
            # non-ohlc case
            index = len(a) - 1
            last = a[chart.name][-1]
        self.update_from_data(
            index,
            last,
        )

    def update_from_data(
        self,
        index: int,
        value: float,
    ) -> None:
        self.update_label(
            self._chart.mapFromView(QPointF(index, value)),
            value
        )
