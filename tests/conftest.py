import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Ensure accidental network access fails tests fast.
    import socket

    def guard(*args, **kwargs):
        raise RuntimeError("Network access is disabled in tests")

    monkeypatch.setattr(socket, "socket", guard)

