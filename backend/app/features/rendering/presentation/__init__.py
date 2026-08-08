"""Document renderers for PRISM.

One brief in, two documents out. Everything here is a pure function of an
``Estimate`` - no I/O, no network, no state.

The import surface is fixed by docs/CONTRACT.md section 6::

    from app.presentation.renderers import (
        render_client_proposal,        # (estimate) -> markdown str
        render_developer_requirements, # (estimate) -> markdown str
        render_print_html,             # (markdown, title, estimate) -> html str
        render_proposal,               # (document, estimate) -> markdown str
    )
    from app.presentation.renderers.money import format_money  # (1234.5, "PHP") -> "P1,234.50"
"""

from .pdf import render_pdf
from .html import markdown_to_html, render_print_html
from .quotation import (
    quotation_reference,
    render_client_proposal,
    render_developer_requirements,
)
from .proposal import render_proposal
from .money import (
    currency_symbol,
    format_amount,
    format_money,
    format_pct,
    format_qty,
    format_unit,
)

__all__ = [
    "render_pdf",
    "render_client_proposal",
    "render_developer_requirements",
    "render_proposal",
    "render_print_html",
    "markdown_to_html",
    "quotation_reference",
    "format_amount",
    "format_money",
    "format_qty",
    "format_pct",
    "format_unit",
    "currency_symbol",
]
