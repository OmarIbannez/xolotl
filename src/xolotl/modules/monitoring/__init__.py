"""Transaction monitoring (SP-04).

``txn`` takes transactions in and keeps them, ``enrich`` values them against the
effective policy, ``aggregates`` keeps the declared windows per key and
``process`` drains each party's transactions in ``occurred_at`` order.
"""
