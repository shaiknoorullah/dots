"""Reconstruction of days that happened before live capture existed.

Every row these writers produce carries provenance='backfill:<source>' and a confidence
below 1.0, so a reconstructed day can never be mistaken for a measured one -- in the
database or in the export. Unlike live capture, a reconstruction is replaceable: re-running
a backfill for a date range removes its own earlier rows for that range and rewrites them.
Live events are never deleted.
"""
