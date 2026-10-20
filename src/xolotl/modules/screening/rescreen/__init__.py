"""Re-screening: party events, batches and the screen-party job (SP-03 spec §10).

Every re-screen kind is a job that handles one page in one transaction: the page's
runs, hits and findings, its batch's counts and cursor, and the job for the next page
commit together, so a crash replays at most one page and a page the batch's cursor
has passed is skipped. Batches are opened by ``(kind, ref)`` (``batches.py``), so a
redelivered event or a second schedule tick finds the batch it already opened.
"""

from xolotl.modules.screening.rescreen.wiring import register_rescreen

__all__ = ["register_rescreen"]
