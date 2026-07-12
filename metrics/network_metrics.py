"""Network throughput ("internet speed used by the device") collector."""

import time
import psutil
from pathlib import Path

from metrics.schema import NetworkMetrics
from util import lazy_singleton


class NetworkMetricsCollector:
    """Collects current network throughput across the device's physical
    network interfaces.

    Reports live usage (rate since the last sample), like every other
    collector in this app -- not an active speed test against an external
    server, which would be a slower, bandwidth-consuming kind of measurement.
    """

    def __init__(self):
        self.previous_io = self._read_io()
        # Deliberately None, not time.time(): the first collect() call must
        # skip rate computation entirely rather than divide by whatever
        # near-zero (or even zero) time has elapsed since construction,
        # which would produce an erratic/inflated spike. Matches
        # StorageMetricsCollector's identical previous_time=None convention.
        self.previous_time = None

    @staticmethod
    def _physical_interfaces():
        """Names of interfaces backed by real hardware, or None if that
        can't be determined on this system.

        A `/sys/class/net/<name>/device` symlink exists only for interfaces
        with a real device behind them (ethernet/wifi NICs, including
        virtio-net in a VM) -- not for loopback, bridges (docker0), veth
        pairs, or VPN tunnels (tailscale0). That distinction matters here
        because virtual interfaces' traffic isn't separate from a physical
        interface's -- it's carried *over* one. Tailscale packets are
        encapsulated UDP sent over the real uplink, and Docker container
        traffic bound for the internet is NAT'd out through it too. Summing
        those virtual interfaces alongside the physical one they ride on
        would double- or triple-count the same bytes.
        """
        net_class = Path('/sys/class/net')
        if not net_class.is_dir():
            return None
        physical = {p.name for p in net_class.iterdir() if (p / 'device').exists()}
        return physical or None

    @staticmethod
    def _read_io():
        """Sum bytes_sent/bytes_recv across the physical interfaces.

        Falls back to every non-loopback interface if physical-interface
        detection finds nothing (e.g. no /sys/class/net on this platform) --
        over-counting via virtual interfaces beats silently reporting 0.
        """
        counters = psutil.net_io_counters(pernic=True)
        physical = NetworkMetricsCollector._physical_interfaces()
        selected = (physical & counters.keys()) if physical else (counters.keys() - {'lo'})

        sent = recv = 0
        for name in selected:
            sent += counters[name].bytes_sent
            recv += counters[name].bytes_recv
        return sent, recv

    def collect(self) -> NetworkMetrics:
        current_time = time.time()
        current_sent, current_recv = self._read_io()

        download_mbps = 0.0
        upload_mbps = 0.0

        if self.previous_time is not None:
            time_delta = current_time - self.previous_time
            if time_delta > 0:
                prev_sent, prev_recv = self.previous_io
                recv_bytes_sec = max(current_recv - prev_recv, 0) / time_delta
                sent_bytes_sec = max(current_sent - prev_sent, 0) / time_delta
                # bytes/sec -> megabits/sec (network convention: bits, not
                # bytes -- see metrics/schema.py:NetworkMetrics docstring).
                download_mbps = round(recv_bytes_sec * 8 / 1_000_000, 2)
                upload_mbps = round(sent_bytes_sec * 8 / 1_000_000, 2)

        self.previous_io = (current_sent, current_recv)
        self.previous_time = current_time

        return {
            "download_mbps": download_mbps,
            "upload_mbps": upload_mbps,
        }


_get_network_collector = lazy_singleton(NetworkMetricsCollector)

def get_network_metrics() -> NetworkMetrics:
    """Get current network throughput. See metrics/schema.py:NetworkMetrics for the shape."""
    return _get_network_collector().collect()
