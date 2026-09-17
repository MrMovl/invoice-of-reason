"""Layout regressions that are cheap to catch: section spacing and the number of stat tiles."""

import re
from pathlib import Path

from tests.conftest import invoice_form

CSS = Path("src/invoices/static/style.css").read_text()


def test_sections_after_stats_do_not_pull_up_into_the_text():
    """A negative top margin on .cash used to drag the next heading over the hint above it."""
    rule = re.search(r"\.cash \{([^}]*)\}", CSS).group(1)
    assert "-" not in rule.split("margin:")[1].split(";")[0]


def test_turnover_block_stays_compact(logged_in, csrf):
    logged_in.post("/invoices", data={**invoice_form(), "csrf_token": csrf})
    html = logged_in.get("/invoices").get_data(as_text=True)
    block = html.split('id="turnover-head"', 1)[1].split("</section>", 1)[0]
    assert block.count("<dt>") <= 5, "too many tiles: they wrap into a second row"
    assert block.rindex("hint") > block.rindex("</dl>"), "the hint belongs below the tiles"
    assert "in diesem Programm" in block and "außerhalb" in block
