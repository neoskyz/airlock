"""Single-instance sliding-window request limits for the public demo."""
from collections import deque
from ipaddress import ip_address
from math import ceil
import time


class RunRateLimiter:
    def __init__(self, per_minute=5, per_hour=30, *, clock=time.monotonic):
        self.per_minute, self.per_hour, self.clock = per_minute, per_hour, clock
        self.clients = {}

    def check(self, client):
        now = self.clock()
        # Also retire inactive client entries so public traffic cannot grow forever.
        for key in list(self.clients):
            timestamps = self.clients[key]
            while timestamps and timestamps[0] <= now - 3600:
                timestamps.popleft()
            if not timestamps:
                del self.clients[key]
        timestamps = self.clients.setdefault(client, deque())
        minute = [stamp for stamp in timestamps if stamp > now - 60]
        waits = []
        if len(minute) >= self.per_minute:
            waits.append(minute[-self.per_minute] + 60 - now)
        if len(timestamps) >= self.per_hour:
            waits.append(timestamps[-self.per_hour] + 3600 - now)
        if waits:
            return max(1, ceil(max(waits)))
        timestamps.append(now)
        return None


def client_ip(request):
    # Render supplies the forwarding chain; the first IP identifies the client.
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    try:
        return str(ip_address(forwarded))
    except ValueError:
        return request.client.host if request.client else "unknown"
