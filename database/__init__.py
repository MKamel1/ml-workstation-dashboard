"""Database module for storing historical metrics."""

import sqlite3
import json
import time
from pathlib import Path
from typing import Dict, List, Optional
from queue import Queue, Empty
import threading

if __name__ == "__main__":
    # Direct execution (`python database/__init__.py`) puts this file's own
    # directory on sys.path instead of the repo root; add the root back so
    # `import util` resolves the same as it does when imported as a package.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from util import lazy_singleton

# Sentinel put on the write queue to tell the writer thread to drain and exit.
_WRITER_SHUTDOWN = object()


class MetricsDatabase:
    """SQLite database for storing time-series metrics.

    All writes (inserts, deletes) are funneled through a single background
    writer thread via `write_queue`, which is the only code that ever touches
    `self.conn`. Reads open their own short-lived connection instead, since
    WAL mode allows concurrent readers without touching the writer's
    connection/cursor.
    """

    def __init__(self, db_path: str = "workstation_metrics.db"):
        """Initialize database connection with concurrency improvements."""
        self.db_path = db_path
        self.conn = None
        self._closed = False

        # Create write queue to serialize database writes
        self.write_queue = Queue()
        self.writer_thread = threading.Thread(target=self._process_writes, daemon=True)
        self.writer_thread.start()

        self._init_database()

    def _process_writes(self):
        """Background thread to process queued database writes.

        Exits cleanly when it dequeues the `_WRITER_SHUTDOWN` sentinel,
        which `close()` enqueues after any pending writes so they get
        drained first (the queue is FIFO).
        """
        while True:
            try:
                write_func = self.write_queue.get(timeout=1)
            except Empty:
                continue
            if write_func is _WRITER_SHUTDOWN:
                break
            try:
                write_func()
            except Exception as e:
                print(f"[Database] Write error: {e}")

    def _submit_write(self, fn):
        """Queue `fn` on the writer thread and block until it completes.

        Returns whatever `fn` returns, or re-raises whatever it raised, so
        callers like `cleanup_old_metrics` can stay synchronous while still
        having all writes serialized through the single writer thread.
        """
        if self._closed:
            raise RuntimeError("MetricsDatabase is closed")

        done = threading.Event()
        outcome = {}

        def _wrapped():
            try:
                outcome['result'] = fn()
            except Exception as e:
                outcome['error'] = e
            finally:
                done.set()

        self.write_queue.put(_wrapped)
        done.wait()
        if 'error' in outcome:
            raise outcome['error']
        return outcome.get('result')

    def _init_database(self):
        """Create database and tables if they don't exist."""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = self.conn.cursor()

        # Enable WAL mode for better concurrency (multiple readers + one writer)
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")  # Faster writes while still safe

        # Create metrics table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metrics (
                timestamp INTEGER PRIMARY KEY,

                -- GPU metrics (stored as JSON for multi-GPU support)
                gpu_data TEXT,

                -- CPU metrics
                cpu_util REAL,
                cpu_freq REAL,
                cpu_temp REAL,
                cpu_load_1min REAL,

                -- Memory metrics
                memory_used_gb REAL,
                memory_total_gb REAL,
                memory_percent REAL,
                swap_used_gb REAL,

                -- Storage metrics (JSON)
                storage_data TEXT,

                -- ML metrics (JSON)
                ml_data TEXT,

                -- Alerts and detections (JSON)
                bottlenecks TEXT,
                anomalies TEXT,

                -- Network metrics (JSON)
                network_data TEXT
            )
        ''')

        # Create index on timestamp for fast queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_timestamp ON metrics (timestamp)
        ''')

        # Migration for pre-existing databases created before network_data
        # existed -- CREATE TABLE IF NOT EXISTS above is a no-op against an
        # already-existing table, so an older DB needs the column added
        # explicitly. Appended last so existing column indices don't shift.
        cursor.execute("PRAGMA table_info(metrics)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "network_data" not in existing_columns:
            cursor.execute("ALTER TABLE metrics ADD COLUMN network_data TEXT")

        self.conn.commit()

    def insert_metrics(self, metrics: Dict):
        """Insert a metrics snapshot into the database (queued for thread-safe writes)."""
        if self._closed:
            return

        def _do_insert():
            # Use the sample's own collection time, not dequeue time, as the
            # primary key -- otherwise queue latency can land two distinct
            # samples in the same wall-clock second and INSERT OR REPLACE
            # silently drops one of them.
            timestamp = int(metrics.get('timestamp', time.time()))

            # Extract and serialize data
            gpu_data = json.dumps(metrics.get('gpu', []))
            cpu = metrics.get('cpu', {})
            memory = metrics.get('memory', {})
            storage_data = json.dumps(metrics.get('storage', {}))
            ml_data = json.dumps(metrics.get('ml', {}))
            bottlenecks = json.dumps(metrics.get('bottlenecks', []))
            anomalies = json.dumps(metrics.get('anomalies', []))
            network_data = json.dumps(metrics.get('network', {}))

            try:
                # Use INSERT OR REPLACE to handle duplicate timestamps gracefully
                cursor = self.conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    timestamp,
                    gpu_data,
                    cpu.get('utilization_total'),
                    cpu.get('frequency_current_mhz'),
                    cpu.get('temperature'),
                    cpu.get('load_1min'),
                    memory.get('used_gb'),
                    memory.get('total_gb'),
                    memory.get('percent'),
                    memory.get('swap_used_gb'),
                    storage_data,
                    ml_data,
                    bottlenecks,
                    anomalies,
                    network_data
                ))
                self.conn.commit()
            except Exception as e:
                print(f"Error inserting metrics: {e}")

        # Queue the write operation (fire-and-forget; errors are logged above)
        self.write_queue.put(_do_insert)

    def query_metrics(self, start_time: Optional[int] = None,
                     end_time: Optional[int] = None,
                     limit: int = 1000) -> List[Dict]:
        """Query metrics within a time range.

        Uses its own short-lived connection rather than the writer thread's
        shared connection/cursor, since WAL mode supports concurrent readers
        without any locking against the writer.
        """
        if start_time is None:
            start_time = int(time.time()) - 3600  # Last hour by default
        if end_time is None:
            end_time = int(time.time())

        try:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM metrics
                    WHERE timestamp >= ? AND timestamp <= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ''', (start_time, end_time, limit))
                rows = cursor.fetchall()
            finally:
                conn.close()

            # Convert to list of dicts, remapping the flat cpu_*/memory_* columns
            # back into the nested {"cpu": {...}, "memory": {...}} shape that
            # metrics/schema.py's CPUMetrics/MemoryMetrics define and that the
            # live /ws and /api/metrics paths already send. Only the four
            # scalar fields of each that are actually persisted (see
            # insert_metrics) are present -- this is a subset of the full
            # CPUMetrics/MemoryMetrics TypedDicts, not the complete shape,
            # since the other fields (cores, brand, swap_total_gb, ...) were
            # never stored as columns. gpu/storage/ml/network/bottlenecks/
            # anomalies are stored as JSON blobs already matching
            # metrics/schema.py, so they need no remapping.
            results = []
            for row in rows:
                results.append({
                    'timestamp': row[0],
                    'gpu': json.loads(row[1]) if row[1] else [],
                    'cpu': {
                        'utilization_total': row[2],
                        'frequency_current_mhz': row[3],
                        'temperature': row[4],
                        'load_1min': row[5],
                    },
                    'memory': {
                        'used_gb': row[6],
                        'total_gb': row[7],
                        'percent': row[8],
                        'swap_used_gb': row[9],
                    },
                    'storage': json.loads(row[10]) if row[10] else {},
                    'ml': json.loads(row[11]) if row[11] else {},
                    'bottlenecks': json.loads(row[12]) if row[12] else [],
                    'anomalies': json.loads(row[13]) if row[13] else [],
                    'network': json.loads(row[14]) if row[14] else {},
                })

            return results
        except Exception as e:
            print(f"Error querying metrics: {e}")
            return []

    def cleanup_old_metrics(self, retention_days: int = 7):
        """Delete metrics older than retention period.

        Routed through the writer thread (same as insert_metrics) instead of
        executing directly, since a DELETE is a write and must not run on a
        connection/cursor shared with the writer thread from another thread.
        """
        cutoff_time = int(time.time()) - (retention_days * 86400)

        def _do_delete():
            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM metrics WHERE timestamp < ?', (cutoff_time,))
            deleted = cursor.rowcount
            self.conn.commit()
            return deleted

        try:
            return self._submit_write(_do_delete)
        except Exception as e:
            print(f"Error cleaning up old metrics: {e}")
            return 0

    def get_stats(self) -> Dict:
        """Get database statistics (own short-lived connection; see query_metrics)."""
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM metrics')
                total_records = cursor.fetchone()[0]

                cursor.execute('SELECT MIN(timestamp), MAX(timestamp) FROM metrics')
                min_ts, max_ts = cursor.fetchone()
            finally:
                conn.close()

            return {
                'total_records': total_records,
                'oldest_timestamp': min_ts,
                'newest_timestamp': max_ts,
                'time_span_hours': (max_ts - min_ts) / 3600 if min_ts and max_ts else 0
            }
        except Exception as e:
            print(f"Error getting stats: {e}")
            return {}

    def close(self):
        """Stop accepting new writes, drain queued writes, and close the connection."""
        self._closed = True
        self.write_queue.put(_WRITER_SHUTDOWN)
        self.writer_thread.join(timeout=5)
        if self.conn:
            self.conn.close()


_get_db = lazy_singleton(MetricsDatabase)

def get_database() -> MetricsDatabase:
    """Get or create the database instance."""
    return _get_db()


if __name__ == "__main__":
    # Small runnable self-check for the concurrency fixes (CONC-01/02/03).
    # Uses a throwaway db file; no test framework, just asserts.
    import os
    import tempfile

    tmp_path = os.path.join(tempfile.gettempdir(), "workstation_metrics_selftest.db")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(tmp_path + suffix):
            os.remove(tmp_path + suffix)

    db = MetricsDatabase(tmp_path)

    def make_metrics(ts):
        return {
            'timestamp': ts,
            'gpu': [], 'cpu': {}, 'memory': {}, 'storage': {}, 'ml': {}, 'network': {},
            'bottlenecks': [], 'anomalies': [],
        }

    # CONC-02: several samples queued back-to-back get dequeued (and thus
    # processed) within the same real wall-clock second, but each carries a
    # distinct sample timestamp several seconds apart. The old code keyed
    # each row by `time.time()` at dequeue, so fast draining would collapse
    # them all into one or two rows. The fix keys by the sample's own
    # `metrics['timestamp']`, so all of them must survive as distinct rows.
    base = int(time.time())
    sample_timestamps = [base + 10, base + 11, base + 12, base + 13]
    for ts in sample_timestamps:
        db.insert_metrics(make_metrics(ts))
    time.sleep(0.5)  # let the writer thread drain the queue

    rows = db.query_metrics(start_time=base, end_time=base + 20, limit=100)
    stored_timestamps = {r['timestamp'] for r in rows}
    assert stored_timestamps == set(sample_timestamps), (
        f"expected each sample keyed by its own timestamp, got {stored_timestamps}"
    )

    # CONC-01: concurrent inserts, cleanup, and queries from multiple threads
    # must not crash or corrupt the shared connection/cursor.
    errors = []

    def hammer_inserts():
        try:
            for i in range(20):
                db.insert_metrics(make_metrics(time.time() + i * 1000))
        except Exception as e:
            errors.append(e)

    def hammer_reads():
        try:
            for _ in range(20):
                db.query_metrics(limit=10)
                db.get_stats()
        except Exception as e:
            errors.append(e)

    def hammer_cleanup():
        try:
            for _ in range(5):
                db.cleanup_old_metrics(retention_days=0)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=hammer_inserts),
        threading.Thread(target=hammer_reads),
        threading.Thread(target=hammer_cleanup),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent access raised: {errors}"

    # CONC-03: close() should drain the queue and stop the writer thread.
    db.insert_metrics(make_metrics(time.time() + 999999))
    db.close()
    assert not db.writer_thread.is_alive(), "writer thread should exit after close()"

    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(tmp_path + suffix):
            os.remove(tmp_path + suffix)

    print("database/__init__.py self-check passed")
