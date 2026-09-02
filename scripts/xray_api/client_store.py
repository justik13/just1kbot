import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

logger = logging.getLogger("xray_api.client_store")


class ClientStore:
    """Manages persistent active client UUIDs and versions in a local JSON file (Zero-Loss State) with file locking.

    Note: Local clients.json is strictly an ephemeral crash-recovery hint, NEVER the authoritative SSOT.
    Authoritative state is managed by the Central Database.
    """

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.lock_path = self.file_path.with_suffix(".lock")

    def _ensure_dir(self) -> None:
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Could not create directory %s: %s", self.file_path.parent, e)

    def load_client_entries(self) -> Dict[str, Dict[str, Any]]:
        """Loads all client metadata entries {uuid: {is_active: bool, version: int, updated_at: float}}."""
        if not self.file_path.exists() or self.file_path.stat().st_size == 0:
            return {}
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Format 1: Direct dict of dicts: {"clients": {uuid: {...}}}
                if isinstance(data, dict) and "clients" in data and isinstance(data["clients"], dict):
                    return data["clients"]
                # Format 2: Dict of lists: {"clients": ["uuid1", "uuid2"]}
                if isinstance(data, dict) and "clients" in data and isinstance(data["clients"], list):
                    return {
                        u: {"is_active": True, "version": 1, "updated_at": time.time()}
                        for u in data["clients"]
                    }
                # Format 3: Raw list: ["uuid1", "uuid2"]
                if isinstance(data, list):
                    return {
                        u: {"is_active": True, "version": 1, "updated_at": time.time()}
                        for u in data
                    }
        except Exception as e:
            logger.error("Failed to load clients from %s: %s", self.file_path, e)
        return {}

    def load_clients(self) -> Set[str]:
        """Returns set of currently active client UUIDs, excluding tombstones."""
        entries = self.load_client_entries()
        return {u for u, meta in entries.items() if meta.get("is_active", True) is True and not meta.get("tombstone", False)}

    def save_client_entries(self, entries: Dict[str, Dict[str, Any]]) -> bool:
        self._ensure_dir()
        temp_path = self.file_path.with_suffix(".tmp")
        data = {
            "clients": entries,
            "updated_at": time.time(),
            "count": len([u for u, m in entries.items() if m.get("is_active", True) is True and not m.get("tombstone", False)]),
        }
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            temp_path.replace(self.file_path)
            try:
                os.chmod(self.file_path, 0o660)
            except Exception:
                pass
            try:
                dir_fd = os.open(str(self.file_path.parent), getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error("Failed to save clients to %s: %s", self.file_path, e)
            return False

    def save_clients(self, clients: Set[str]) -> bool:
        """Backward-compatible save using set of active UUIDs."""
        entries = {u: {"is_active": True, "version": 1, "updated_at": time.time()} for u in clients}
        return self.save_client_entries(entries)

    def add_client(
        self,
        client_uuid: str,
        version: Optional[int] = None,
        email: Optional[str] = None,
    ) -> None:
        self._ensure_dir()
        lock_fd = None
        if fcntl is not None:
            try:
                lock_fd = open(self.lock_path, "a")
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            except Exception as e:
                logger.debug("Could not acquire client store lock: %s", e)
        try:
            entries = self.load_client_entries()
            curr_ver = entries.get(client_uuid, {}).get("version", 0) if client_uuid in entries else 0
            new_ver = version if version is not None else max(curr_ver + 1, 1)
            entry: Dict[str, Any] = {
                "is_active": True,
                "version": new_ver,
                "updated_at": time.time(),
            }
            if email:
                entry["email"] = email
            entries[client_uuid] = entry
            if not self.save_client_entries(entries):
                raise IOError(f"Failed to persist client addition to disk: {client_uuid}")
        finally:
            if fcntl is not None and lock_fd is not None:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                except Exception:
                    pass

    def remove_client(self, client_uuid: str, version: Optional[int] = None) -> None:
        self._ensure_dir()
        lock_fd = None
        if fcntl is not None:
            try:
                lock_fd = open(self.lock_path, "a")
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            except Exception as e:
                logger.debug("Could not acquire client store lock: %s", e)
        try:
            entries = self.load_client_entries()
            curr_ver = entries.get(client_uuid, {}).get("version", 0) if client_uuid in entries else 0
            new_ver = version if version is not None else max(curr_ver + 1, 1)
            entries[client_uuid] = {
                "is_active": False,
                "version": new_ver,
                "updated_at": time.time(),
            }
            if not self.save_client_entries(entries):
                raise IOError(f"Failed to persist client deactivation to disk: {client_uuid}")
        finally:
            if fcntl is not None and lock_fd is not None:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                except Exception:
                    pass

    def delete_client(self, client_uuid: str, version: Optional[int] = None) -> None:
        self._ensure_dir()
        lock_fd = None
        if fcntl is not None:
            try:
                lock_fd = open(self.lock_path, "a")
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            except Exception as e:
                logger.debug("Could not acquire client store lock: %s", e)
        try:
            entries = self.load_client_entries()
            curr_ver = entries.get(client_uuid, {}).get("version", 0) if client_uuid in entries else 0
            new_ver = version if version is not None else max(curr_ver + 1, 1)
            entries[client_uuid] = {
                "is_active": False,
                "version": new_ver,
                "tombstone": True,
                "updated_at": time.time(),
            }
            if not self.save_client_entries(entries):
                raise IOError(f"Failed to persist client deletion tombstone to disk: {client_uuid}")
        finally:
            if fcntl is not None and lock_fd is not None:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                except Exception:
                    pass
