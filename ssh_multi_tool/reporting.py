"""Asynchronous local reporting; SSH input/output never waits for GUI IPC."""
import queue
import threading

FINAL = {"succeeded", "failed", "disconnected", "closed"}


class Reporter:
    def __init__(self, send, heartbeat_interval=3):
        self.send = send; self.interval = heartbeat_interval
        self.queue = queue.Queue(maxsize=32); self.stop = threading.Event()
        self.latest = None
        self.thread = threading.Thread(target=self._run, daemon=True); self.thread.start()

    def emit(self, event):
        if self.stop.is_set(): return
        try: self.queue.put_nowait(dict(event))
        except queue.Full:
            try: self.queue.get_nowait()
            except queue.Empty: pass
            self.queue.put_nowait(dict(event))

    def _run(self):
        try:
            while not self.stop.is_set():
                try: event = self.queue.get(timeout=self.interval)
                except queue.Empty:
                    if self.latest is None: continue
                    event = self.latest
                if event is None: break
                self.latest = event
                try: self.send(event)
                except (OSError, ValueError): break
                if event["state"] in FINAL: break
        finally: self.stop.set()

    def close(self):
        # Give a queued final result time to arrive; an offline GUI cannot stall exit.
        self.thread.join(timeout=.5)
        self.stop.set()
        try: self.queue.put_nowait(None)
        except queue.Full: pass
        self.thread.join(timeout=.5)
