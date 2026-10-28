"""Movement keys for full-screen scroll views (help, reader).

A screen mixes in ``ScrollKeys``, binds ``keys.bindings_for("<scope>")`` and
implements ``scroll_view()``; the registry's movement actions then scroll it
the same way they scroll the details pane.
"""

from __future__ import annotations

from textual.containers import ScrollableContainer


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
