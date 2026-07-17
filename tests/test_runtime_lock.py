import pytest

from masterclaw.runtime_lock import InstanceAlreadyRunning, single_instance


def test_second_daemon_for_same_database_is_rejected(tmp_path) -> None:
    database = tmp_path / "masterclaw.sqlite3"
    with single_instance(database):
        with pytest.raises(InstanceAlreadyRunning):
            with single_instance(database):
                raise AssertionError("second instance acquired the lock")
