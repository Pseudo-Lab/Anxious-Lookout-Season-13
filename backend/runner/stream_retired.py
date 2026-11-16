"""#99: service-only retired-literal holdback switch. No sidecar, expiry proof or broker requests.

Classification is performed by ServiceAccessAuth.stream_generation under the install mutex and a validated
ledger transaction. Old #98 sidecars and journal markers are retained on disk but no longer consulted.
"""
import os


def enabled():
    return os.environ.get('STREAM_RETIRED', 'false') == 'true'
