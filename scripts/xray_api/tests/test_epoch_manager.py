import tempfile
from pathlib import Path
from app import ClientStore


def test_client_store_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "clients.json"
        store = ClientStore(file_path=store_file)

        # Initially empty
        assert store.load_clients() == set()

        # Add clients
        uuid1 = "a2b9d4e1-73c5-4812-b964-f3e7b85a1901"
        uuid2 = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        store.add_client(uuid1)
        store.add_client(uuid2)

        clients = store.load_clients()
        assert uuid1 in clients
        assert uuid2 in clients
        assert len(clients) == 2

        # Remove client
        store.remove_client(uuid1)
        clients_after = store.load_clients()
        assert uuid1 not in clients_after
        assert uuid2 in clients_after
        assert len(clients_after) == 1
