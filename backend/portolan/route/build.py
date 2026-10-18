"""Route generation (spec §10.6)."""

from __future__ import annotations

from portolan.llm.gateway import Gateway
from portolan.models.paper import PaperDoc
from portolan.models.route import Route
from portolan.store.chart import ChartStore
from portolan.store.routes import RouteStore


async def build_main_route(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, routes: RouteStore
) -> Route:
    """Stops come deterministically from the paper's structure; one `fast` call per top-level
    section writes titles, roles, summaries, concept links and transitions. Sets `sub_route_id`
    on stops that can be entered, `segment_title` on the first stop of each section, the
    recurring-concept fields and appendix side roads. The caller saves the result."""
    raise NotImplementedError("the Route arrives with M7")


async def build_sub_route(
    doc: PaperDoc, gateway: Gateway, chart: ChartStore, routes: RouteStore, stop_id: str
) -> Route:
    """Sub-route for one stop, from that section's paragraphs in order (id == stop_id)."""
    raise NotImplementedError("the Route arrives with M7")
