"""Standalone check that query_metrics() round-trips the nested cpu/memory
shape from metrics/schema.py (CONTRACT-02), instead of the old flat
cpu_util/memory_used_gb top-level keys.

Uses a throwaway db file; no test framework, just asserts.
Run directly: python tests/test_history_shape.py
"""

import sys
import os
import tempfile
import time
from typing import get_type_hints

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import MetricsDatabase
from metrics.schema import CPUMetrics, MemoryMetrics


def make_snapshot(ts):
    """A synthetic MetricsSnapshot-shaped dict (only the fields
    insert_metrics/query_metrics actually round-trip)."""
    return {
        'timestamp': ts,
        'gpu': [{'index': 0, 'name': 'Fake GPU', 'gpu_util': 42}],
        'cpu': {
            'utilization_total': 12.5,
            'frequency_current_mhz': 3200.0,
            'temperature': 55.0,
            'load_1min': 0.8,
        },
        'memory': {
            'used_gb': 8.0,
            'total_gb': 32.0,
            'percent': 25.0,
            'swap_used_gb': 0.1,
        },
        'storage': {'partitions': [], 'disk_io': [], 'huggingface_cache_gb': 1.0},
        'ml': {'active_processes': [], 'cuda_version': None,
               'installed_packages': {}, 'active_environments': []},
        'bottlenecks': [],
        'anomalies': [],
    }


tmp_path = os.path.join(tempfile.gettempdir(), "workstation_metrics_history_shape_test.db")
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(tmp_path + suffix):
        os.remove(tmp_path + suffix)

db = MetricsDatabase(tmp_path)

ts = int(time.time())
snapshot = make_snapshot(ts)
db.insert_metrics(snapshot)
time.sleep(0.5)  # let the writer thread drain the queue

rows = db.query_metrics(start_time=ts - 5, end_time=ts + 5, limit=10)
assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"
row = rows[0]

# Old, wrong shape must be gone.
flat_keys = {'cpu_util', 'cpu_freq', 'cpu_temp', 'cpu_load_1min',
             'memory_used_gb', 'memory_total_gb', 'memory_percent', 'swap_used_gb'}
leaked = flat_keys & set(row.keys())
assert not leaked, f"query_metrics still returns old flat keys: {leaked}"

# New shape: nested 'cpu'/'memory' dicts, keyed the same as metrics/schema.py.
assert 'cpu' in row and isinstance(row['cpu'], dict), "row missing nested 'cpu' dict"
assert 'memory' in row and isinstance(row['memory'], dict), "row missing nested 'memory' dict"

assert row['cpu'] == snapshot['cpu'], (
    f"cpu round-trip mismatch: {row['cpu']} != {snapshot['cpu']}"
)
assert row['memory'] == snapshot['memory'], (
    f"memory round-trip mismatch: {row['memory']} != {snapshot['memory']}"
)

# Every returned cpu/memory key name must be declared in the live schema
# (metrics/schema.py), so /api/history is structurally a subset of a /ws
# message's cpu/memory sub-objects, not an invented parallel shape.
cpu_allowed = set(get_type_hints(CPUMetrics).keys())
memory_allowed = set(get_type_hints(MemoryMetrics).keys())
assert set(row['cpu'].keys()) <= cpu_allowed, (
    f"row['cpu'] keys {set(row['cpu'].keys())} not a subset of CPUMetrics {cpu_allowed}"
)
assert set(row['memory'].keys()) <= memory_allowed, (
    f"row['memory'] keys {set(row['memory'].keys())} not a subset of MemoryMetrics {memory_allowed}"
)

db.close()
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(tmp_path + suffix):
        os.remove(tmp_path + suffix)

print("tests/test_history_shape.py passed")
