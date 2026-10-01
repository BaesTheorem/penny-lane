"""Penny Lane: per-store clearance and penny-item detection across US big-box
retailers. One adapter per retailer (`retailers/`), community list ingesters
(`sources/`), a SQLite price history (`db.py`) and a detector (`detect.py`)
that ranks items by how close they are to $0.01 at a specific store.
"""

__version__ = "0.1.0"
