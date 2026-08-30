import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_EPOCH_PATH = "/var/lib/xray-api/epoch.json"


class EpochManager:
    """
    Manages xray_instance_epoch lifecycle.
    Detects restarts of the xray process using PID and starttime from /proc/<pid>/stat.
    Persists state to /var/lib/xray-api/epoch.json.
    """

    def __init__(self, file_path: Optional[str] = None):
        self.file_path = Path(
            file_path or os.getenv("EPOCH_FILE_PATH", DEFAULT_EPOCH_PATH)
        )
        self._current_epoch: Optional[str] = None
        self._last_pid: Optional[int] = None
        self._last_starttime: Optional[int] = None

    def _ensure_dir(self) -> None:
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Could not create directory %s: %s", self.file_path.parent, e)

    def load_state(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            return {}
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning("Failed to load epoch state from %s: %s", self.file_path, e)
        return {}

    def save_state(self, epoch: str, pid: Optional[int], starttime: Optional[int]) -> None:
        self._ensure_dir()
        temp_path = self.file_path.with_suffix(".tmp")
        data = {
            "node_epoch": epoch,
            "xray_pid": pid,
            "xray_starttime": starttime,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_path.replace(self.file_path)
            self._current_epoch = epoch
            self._last_pid = pid
            self._last_starttime = starttime
        except Exception as e:
            logger.error("Failed to save epoch state to %s: %s", self.file_path, e)

    def get_xray_process_info(self) -> Tuple[Optional[int], Optional[int]]:
        """
        Finds the running xray process and extracts (pid, starttime).
        Reads /proc filesystem directly.
        Returns (pid, starttime) or (None, None) if not running.
        """
        proc_dir = Path("/proc")
        if not proc_dir.exists() or not proc_dir.is_dir():
            return None, None

        for entry in proc_dir.iterdir():
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                # 1. Check /proc/<pid>/comm or cmdline
                comm_path = entry / "comm"
                is_xray = False
                if comm_path.exists():
                    try:
                        comm = comm_path.read_text(encoding="utf-8", errors="ignore").strip()
                        if comm == "xray":
                            is_xray = True
                    except Exception:
                        pass

                if not is_xray:
                    cmdline_path = entry / "cmdline"
                    if cmdline_path.exists():
                        try:
                            cmdline = cmdline_path.read_text(encoding="utf-8", errors="ignore")
                            # Look for 'xray' binary name or 'xray run'
                            parts = cmdline.split("\0")
                            if parts and (parts[0].endswith("/xray") or parts[0] == "xray"):
                                is_xray = True
                        except Exception:
                            pass

                if not is_xray:
                    continue

                # 2. Parse /proc/<pid>/stat
                stat_path = entry / "stat"
                if not stat_path.exists():
                    continue

                content = stat_path.read_text(encoding="utf-8", errors="ignore")
                # Parse: find closing parenthesis for comm field
                rparen_idx = content.rfind(")")
                if rparen_idx == -1:
                    continue
                rest = content[rparen_idx + 1 :].strip()
                tokens = rest.split()
                # token index 19 corresponds to field 22 (starttime)
                if len(tokens) > 19:
                    starttime = int(tokens[19])
                    return pid, starttime
            except (ProcessLookupError, PermissionError):
                continue
            except Exception as e:
                logger.debug("Error inspecting /proc/%s: %s", pid, e)
                continue

        return None, None

    def get_last_known_epoch(self) -> Optional[str]:
        """Returns the last known persisted epoch, regardless of whether Xray is currently running."""
        state = self.load_state()
        return state.get("node_epoch")

    def get_process_and_epoch(self) -> tuple[Optional[int], Optional[int], Optional[str]]:
        """
        Atomically inspects /proc once and returns (pid, starttime, running_epoch).
        Returns (None, None, None) if Xray is not running.
        """
        pid, starttime = self.get_xray_process_info()
        if pid is None or starttime is None:
            return None, None, None

        state = self.load_state()
        saved_epoch = state.get("node_epoch")
        saved_pid = state.get("xray_pid")
        saved_starttime = state.get("xray_starttime")

        if not saved_epoch or saved_pid != pid or saved_starttime != starttime:
            new_epoch = f"epoch_{int(time.time())}_{uuid.uuid4().hex[:12]}"
            logger.info(
                "Detected xray instance change (pid=%s, starttime=%s). New epoch: %s",
                pid,
                starttime,
                new_epoch,
            )
            self.save_state(new_epoch, pid, starttime)
            return pid, starttime, new_epoch
        return pid, starttime, saved_epoch

    def get_current_running_epoch(self) -> Optional[str]:
        """
        Returns the active runtime epoch if and only if Xray is currently running.
        If Xray is stopped, returns None (fail-closed).
        """
        _pid, _starttime, epoch = self.get_process_and_epoch()
        return epoch

    def get_current_epoch(self) -> str:
        """
        Returns the current active epoch.
        If xray was restarted (PID or starttime changed), generates and persists a new epoch.
        If xray is not detected as running, returns last known persisted epoch (or initializes a baseline).
        """
        _pid, _starttime, epoch = self.get_process_and_epoch()
        if epoch is not None:
            return epoch

        state = self.load_state()
        saved_epoch = state.get("node_epoch")
        if saved_epoch:
            return saved_epoch

        new_epoch = f"epoch_{int(time.time())}_{uuid.uuid4().hex[:12]}"
        self.save_state(new_epoch, None, None)
        return new_epoch

    def get_state_summary(self) -> Dict[str, Any]:
        pid, _starttime, running_epoch = self.get_process_and_epoch()
        last_known = self.get_last_known_epoch()
        return {
            "node_epoch": running_epoch,
            "last_known_epoch": last_known,
            "xray_running": pid is not None,
            "xray_pid": pid,
        }


