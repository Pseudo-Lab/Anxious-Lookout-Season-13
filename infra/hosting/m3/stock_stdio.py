"""Fixture-only single stdio client, same RPC matching as Unix Client."""
import json
import queue
import threading
from stock_wire import Client


class StdioClient(Client):
    def __init__(self, label, process):
        self.process = process
        self.events, self.errors, self.next_id = [], [], 1
        self.inbox = queue.Queue()
        threading.Thread(target=self.reader, daemon=True).start()
        self.rpc('initialize', {'clientInfo': {'name': 'fixture_'+label, 'version': '1'},
                               'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized'})

    def reader(self):
        try:
            for line in self.process.stdout:
                self.inbox.put(json.loads(line))
        finally:
            self.inbox.put(None)

    def send(self, value):
        self.process.stdin.write(json.dumps(value)+'\n')
        self.process.stdin.flush()

    def receive(self):
        value = self.inbox.get(timeout=5)
        if value is None:
            raise EOFError('Owned fixture stdio closed')
        return value

    def close(self):
        self.process.stdin.close()
