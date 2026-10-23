class AutoParse:
    def __init__(self, schedule, cancel, callback, delay=320):
        self.schedule = schedule
        self.cancel_timer = cancel
        self.callback = callback
        self.delay = delay
        self.timer = None
        self.busy = False
        self.pending = False

    def request(self, active=True):
        self._cancel()
        self.pending = active
        if active:
            self.timer = self.schedule(self.delay, self._fire)

    def _cancel(self):
        if self.timer is not None:
            self.cancel_timer(self.timer)
            self.timer = None

    def _fire(self):
        self.timer = None
        if not self.busy and self.pending:
            self.callback()

    def begin(self):
        self._cancel()
        if self.busy:
            self.pending = True
            return False
        self.pending = False
        self.busy = True
        return True

    def complete(self):
        self.busy = False
        if self.pending and self.timer is None:
            self.timer = self.schedule(0, self._fire)

    def close(self):
        self._cancel()
        self.pending = False
