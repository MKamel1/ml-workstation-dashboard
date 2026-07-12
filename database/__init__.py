"""Database module for storing historical metrics."""

import sqlite3
import json
import time
from pathlib import Path
from typing import Dict, List, Optional
from queue import Queue, Empty
import threading


class MetricsDatabase:
    """SQLite database for storing time-series metrics."""
    
    def __init__(self, db_path: str = "workstation_metrics.db"):
        """Initialize database connection with concurrency improvements."""
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        
        # Create write queue to serialize database writes
        self.write_queue = Queue()
        self.writer_thread = threading.Thread(target=self._process_writes, daemon=True)
        self.writer_thread.start()
        
        self._init_database()
    
    def _process_writes(self):
        """Background thread to process queued database writes."""
        while True:
            try:
                write_func = self.write_queue.get(timeout=1)
                if write_func:
                    write_func()
            except Empty:
                continue
            except Exception as e:
                print(f"[Database] Write error: {e}")
    
    def _init_database(self):
        """Create database and tables if they don't exist."""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        
        # Enable WAL mode for better concurrency (multiple readers + one writer)
        self.cursor.execute("PRAGMA journal_mode=WAL")
        self.cursor.execute("PRAGMA synchronous=NORMAL")  # Faster writes while still safe
        
        # Create metrics table
        self.cursor.execute('''
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
                anomalies TEXT
            )
        ''')
        
        # Create index on timestamp for fast queries
        self.cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_timestamp ON metrics (timestamp)
        ''')
        
        self.conn.commit()
    
    def insert_metrics(self, metrics: Dict):
        """Insert a metrics snapshot into the database (queued for thread-safe writes)."""
        def _do_insert():
            timestamp = int(time.time())
            
            # Extract and serialize data
            gpu_data = json.dumps(metrics.get('gpu', []))
            cpu = metrics.get('cpu', {})
            memory = metrics.get('memory', {})
            storage_data = json.dumps(metrics.get('storage', {}))
            ml_data = json.dumps(metrics.get('ml', {}))
            bottlenecks = json.dumps(metrics.get('bottlenecks', []))
            anomalies = json.dumps(metrics.get('anomalies', []))
            
            try:
                # Use INSERT OR REPLACE to handle duplicate timestamps gracefully
                self.cursor.execute('''
                    INSERT OR REPLACE INTO metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    anomalies
                ))
                self.conn.commit()
            except Exception as e:
                print(f"Error inserting metrics: {e}")
        
        # Queue the write operation
        self.write_queue.put(_do_insert)
    
    def query_metrics(self, start_time: Optional[int] = None, 
                     end_time: Optional[int] = None,
                     limit: int = 1000) -> List[Dict]:
        """Query metrics within a time range."""
        if start_time is None:
            start_time = int(time.time()) - 3600  # Last hour by default
        if end_time is None:
            end_time = int(time.time())
        
        try:
            self.cursor.execute('''
                SELECT * FROM metrics 
                WHERE timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (start_time, end_time, limit))
            
            rows = self.cursor.fetchall()
            
            # Convert to list of dicts
            results = []
            for row in rows:
                results.append({
                    'timestamp': row[0],
                    'gpu': json.loads(row[1]) if row[1] else [],
                    'cpu_util': row[2],
                    'cpu_freq': row[3],
                    'cpu_temp': row[4],
                    'cpu_load_1min': row[5],
                    'memory_used_gb': row[6],
                    'memory_total_gb': row[7],
                    'memory_percent': row[8],
                    'swap_used_gb': row[9],
                    'storage': json.loads(row[10]) if row[10] else {},
                    'ml': json.loads(row[11]) if row[11] else {},
                    'bottlenecks': json.loads(row[12]) if row[12] else [],
                    'anomalies': json.loads(row[13]) if row[13] else [],
                })
            
            return results
        except Exception as e:
            print(f"Error querying metrics: {e}")
            return []
    
    def cleanup_old_metrics(self, retention_days: int = 7):
        """Delete metrics older than retention period."""
        cutoff_time = int(time.time()) - (retention_days * 86400)
        
        try:
            self.cursor.execute('DELETE FROM metrics WHERE timestamp < ?', (cutoff_time,))
            deleted = self.cursor.rowcount
            self.conn.commit()
            return deleted
        except Exception as e:
            print(f"Error cleaning up old metrics: {e}")
            return 0
    
    def get_stats(self) -> Dict:
        """Get database statistics."""
        try:
            self.cursor.execute('SELECT COUNT(*) FROM metrics')
            total_records = self.cursor.fetchone()[0]
            
            self.cursor.execute('SELECT MIN(timestamp), MAX(timestamp) FROM metrics')
            min_ts, max_ts = self.cursor.fetchone()
            
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
        """Close database connection."""
        if self.conn:
            self.conn.close()


# Singleton instance
_db = None

def get_database() -> MetricsDatabase:
    """Get or create database instance."""
    global _db
    if _db is None:
        _db = MetricsDatabase()
    return _db
