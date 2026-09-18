"""Compatibility entry point for the unified localhost model broker.

Use ``python3 -m jevlean.model_broker`` in new automation.  This command starts
that same service; it does not provide a separate rank-only listener.
"""
from .model_broker import BrokerServer, ModelBroker, PersistentTypeSafeClient, main

# Compatibility aliases for callers that construct the former rank broker directly.
RankBroker = ModelBroker

if __name__ == "__main__":
    main()
