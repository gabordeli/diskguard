import pytest

from diskguard.guard import (
    GIB,
    GuardState,
    evaluate,
    settings_from_config,
    torrents_to_pause,
    torrents_to_resume,
    validate_guard_config,
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


def test_evaluate_skips_non_deluge_disks():
    default, per_mount = settings_from_config(
        {"default_min_free_gb": 10, "resume_margin_gb": 5}
    )
    state = GuardState()

    events = evaluate(
        [{"mount": "/mnt/backup", "free": 0, "deluge": False}],
        default,
        per_mount,
        state,
    )

    assert events == []
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


def test_torrents_to_pause_respects_mount_aliases():
    mounts = [
        Mount("/dev/sdb1", "/mnt/usbhdd2", "ext4"),
        Mount("/dev/sdb1", "/mnt/usbhdd2/bind", "ext4"),
    ]
    uses = [PathUse("/mnt/usbhdd2/a", "t1", active=True)]

    assert torrents_to_pause(
        uses,
        mounts,
        "/mnt/usbhdd2/bind",
        set(),
        {"/mnt/usbhdd2", "/mnt/usbhdd2/bind"},
    ) == {"t1"}


def test_torrents_to_resume_only_guard_paused_existing():
    state = GuardState(
        paused_torrents={"t1", "t2"},
        paused_by_mount={"/mnt/usbhdd2": {"t1"}},
    )

    assert torrents_to_resume(state, "/mnt/usbhdd2", {"t1", "t2"}) == {"t1"}
    assert torrents_to_resume(state, "/mnt/usbhdd2", set()) == set()
    assert torrents_to_resume(state, "/mnt/other", {"t1"}) == set()


def test_validate_guard_config_normalises_partial_payload():
    assert validate_guard_config(
        {
            "enabled": False,
            "default_min_free_gb": 20,
            "per_mount": {
                "/mnt/usbhdd2": {
                    "min_free_gb": 30,
                    "resume_margin_gb": 6,
                    "action": "notify",
                }
            },
        }
    ) == {
        "enabled": False,
        "default_min_free_gb": 20.0,
        "per_mount": {
            "/mnt/usbhdd2": {
                "min_free_gb": 30.0,
                "resume_margin_gb": 6.0,
                "action": "notify",
            }
        },
    }


def test_validate_guard_config_accepts_zero_and_empty_mount_entry():
    assert validate_guard_config(
        {"default_min_free_gb": 0, "per_mount": {"/mnt/x": {}}}
    ) == {"default_min_free_gb": 0.0, "per_mount": {"/mnt/x": {}}}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "config must be an object"),
        ({"nope": 1}, "unknown config keys"),
        ({1: "x"}, "unknown config keys"),
        ({"default_min_free_gb": -1}, "must be >= 0"),
        ({"resume_margin_gb": "x"}, "must be a number"),
        ({"default_min_free_gb": float("nan")}, "must be finite"),
        ({"default_min_free_gb": float("inf")}, "must be finite"),
        ({"enabled": "yes"}, "enabled must be a boolean"),
        ({"per_mount": []}, "per_mount must be an object"),
        ({"per_mount": {"/mnt/x": {"nope": 1}}}, "unknown per_mount keys"),
        ({"per_mount": {"": {}}}, "non-empty mountpoints"),
        ({"per_mount": {1: {}}}, "non-empty mountpoints"),
        ({"per_mount": {"/mnt/x": []}}, "must be an object"),
        (
            {"per_mount": {"/mnt/x": {"action": "drop"}}},
            "must be notify or pause",
        ),
        ({"per_mount": {"/mnt/x": {"min_free_gb": -2}}}, "must be >= 0"),
    ],
)
def test_validate_guard_config_rejects_invalid_values(payload, message):
    with pytest.raises(ValueError, match=message):
        validate_guard_config(payload)


def test_settings_from_config_tolerates_bad_per_mount_shape():
    default, per_mount = settings_from_config({"per_mount": ["bad"]})

    assert per_mount == {}
    assert default.min_free_bytes == 10 * GIB


def test_settings_from_config_falls_back_for_non_finite_values():
    default, _ = settings_from_config(
        {
            "default_min_free_gb": float("inf"),
            "resume_margin_gb": float("nan"),
        }
    )

    assert default.min_free_bytes == 10 * GIB
    assert default.resume_margin_bytes == 5 * GIB
