"""Movement keys for scroll views and pick lists (help, reader, dialogs).

A screen mixes in ``ScrollKeys``, binds ``keys.bindings_for("<scope>")`` and
implements ``scroll_view()``; the registry's movement actions then scroll it
the same way they scroll the details pane. ``OptionListKeys`` does the same
for a pick list: the keys move its highlight.
"""

from __future__ import annotations

from textual.containers import ScrollableContainer
from textual.widgets import OptionList


class ScrollKeys:
    """``action_*`` methods for the registry movement actions on one scroll view."""

    def scroll_view(self) -> ScrollableContainer:
        raise NotImplementedError

    def _half_page(self) -> int:
        return max(1, self.scroll_view().scrollable_content_region.height // 2)

    def action_cursor_down(self) -> None:
        self.scroll_view().scroll_down(animate=False)

    def action_cursor_up(self) -> None:
        self.scroll_view().scroll_up(animate=False)

    def action_cursor_first(self) -> None:
        self.scroll_view().scroll_home(animate=False)

    def action_cursor_last(self) -> None:
        self.scroll_view().scroll_end(animate=False)

    def action_half_page_down(self) -> None:
        self.scroll_view().scroll_relative(y=self._half_page(), animate=False)

    def action_half_page_up(self) -> None:
        self.scroll_view().scroll_relative(y=-self._half_page(), animate=False)

    def action_page_down(self) -> None:
        self.scroll_view().scroll_page_down(animate=False)

    def action_page_up(self) -> None:
        self.scroll_view().scroll_page_up(animate=False)


class OptionListKeys:
    """``action_*`` methods for the registry movement actions on one pick list."""

    def option_list(self) -> OptionList:
        raise NotImplementedError

    def _step(self, rows: int) -> None:
        option_list = self.option_list()
        if not option_list.option_count:
            return
        current = option_list.highlighted or 0
        option_list.highlighted = max(0, min(option_list.option_count - 1, current + rows))

    def _half_page(self) -> int:
        return max(1, self.option_list().scrollable_content_region.height // 2)

    def action_cursor_down(self) -> None:
        self.option_list().action_cursor_down()

    def action_cursor_up(self) -> None:
        self.option_list().action_cursor_up()

    def action_cursor_first(self) -> None:
        self.option_list().action_first()

    def action_cursor_last(self) -> None:
        self.option_list().action_last()

    def action_half_page_down(self) -> None:
        self._step(self._half_page())

    def action_half_page_up(self) -> None:
        self._step(-self._half_page())

    def action_page_down(self) -> None:
        self.option_list().action_page_down()

    def action_page_up(self) -> None:
        self.option_list().action_page_up()
