from diskguard.guard import (
    GIB,
    GuardState,
    evaluate,
    settings_from_config,
    torrents_to_pause,
)
from diskguard.storage import Mount, PathUse


def test_settings_from_config_converts_gb_and_overrides():
    default, per_mount = settings_from_config(
        {
            "default_min_free_gb": 10,
            "resume_margin_gb": 5,
            "per_mount": {
                "/mnt/usbhdd2": {"min_free_gb": 20, "action": "notify"}
            },
        }
    )
    assert default.min_free_bytes == 10 * GIB
    assert default.resume_margin_bytes == 5 * GIB
    assert per_mount["/mnt/usbhdd2"].min_free_bytes == 20 * GIB
    assert per_mount["/mnt/usbhdd2"].action == "notify"


def test_evaluate_hysteresis():
    default, per_mount = settings_from_config(
        {"default_min_free_gb": 10, "resume_margin_gb": 5}
    )
    state = GuardState()

    low = evaluate(
        [{"mount": "/mnt/usbhdd2", "free": 5 * GIB}],
        default,
        per_mount,
        state,
    )
    assert [event["event"] for event in low] == ["low"]
    assert state.low_mounts == {"/mnt/usbhdd2"}

    still_low = evaluate(
        [{"mount": "/mnt/usbhdd2", "free": 14 * GIB}],
        default,
        per_mount,
        state,
    )
    assert still_low == []

    ok = evaluate(
        [{"mount": "/mnt/usbhdd2", "free": 15 * GIB}],
        default,
        per_mount,
        state,
    )
    assert [event["event"] for event in ok] == ["ok"]
    assert state.low_mounts == set()


def test_torrents_to_pause_only_active_on_mount():
    mounts = [
        Mount("/dev/sdb1", "/mnt/usbhdd2", "ext4"),
        Mount("/dev/sdc1", "/mnt/usbhdd3", "ext4"),
    ]
    uses = [
        PathUse("/mnt/usbhdd2/a", "t1", active=True),
        PathUse("/mnt/usbhdd2/b", "t2", active=False),
        PathUse("/mnt/usbhdd3/c", "t3", active=True),
    ]
    assert torrents_to_pause(uses, mounts, "/mnt/usbhdd2", set()) == {"t1"}
    assert torrents_to_pause(uses, mounts, "/mnt/usbhdd2", {"t1"}) == set()
