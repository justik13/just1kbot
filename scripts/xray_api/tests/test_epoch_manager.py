import tempfile
from pathlib import Path
from epoch_manager import EpochManager


def test_epoch_generation_and_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        epoch_file = Path(tmpdir) / "sub" / "epoch.json"
        mgr = EpochManager(file_path=str(epoch_file))

        epoch1 = mgr.get_current_epoch()
        assert epoch1.startswith("epoch_")
        assert epoch_file.exists()

        state = mgr.load_state()
        assert state["node_epoch"] == epoch1

        # Second call should return identical epoch when no process change detected
        epoch2 = mgr.get_current_epoch()
        assert epoch1 == epoch2


def test_epoch_change_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        epoch_file = Path(tmpdir) / "epoch.json"
        mgr = EpochManager(file_path=str(epoch_file))

        # Seed initial state with a fake PID and starttime
        mgr.save_state("epoch_initial", pid=1001, starttime=50000)
        assert mgr.get_current_epoch() == "epoch_initial"

        # Mock get_xray_process_info to simulate process restart (new PID, new starttime)
        mgr.get_xray_process_info = lambda: (1002, 60000)

        epoch_new = mgr.get_current_epoch()
        assert epoch_new != "epoch_initial"
        assert epoch_new.startswith("epoch_")

        # Verify persisted state
        state = mgr.load_state()
        assert state["node_epoch"] == epoch_new
        assert state["xray_pid"] == 1002
        assert state["xray_starttime"] == 60000
