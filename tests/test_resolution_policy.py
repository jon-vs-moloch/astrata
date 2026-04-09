from astrata.loop0.resolution import determine_task_resolution


def test_resolution_policy_prefers_decompose_for_multistage_work():
    resolution = determine_task_resolution(
        task_payload={
            "task_id": "task-1",
            "title": "Inspect and then patch runtime posture",
            "description": "Inspect the runtime posture and then patch the config.",
            "priority": 5,
            "urgency": 3,
            "risk": "low",
        },
        message_payload={"status": "failed", "reason": "generic failure"},
        attempts=[],
    )
    assert resolution.kind == "decompose"
    assert resolution.next_status == "blocked"
    assert resolution.followup_specs


def test_resolution_policy_prefers_process_repair_for_repeated_failure():
    attempts = [
        {
            "outcome": "failed",
            "degraded_reason": "provider_execution_failed",
            "ended_at": "2026-04-09T01:00:00+00:00",
        },
        {
            "outcome": "failed",
            "degraded_reason": "provider_execution_failed",
            "ended_at": "2026-04-09T00:00:00+00:00",
        },
    ]
    resolution = determine_task_resolution(
        task_payload={
            "task_id": "task-2",
            "title": "Execute runtime repair",
            "description": "Try the same runtime repair again.",
            "priority": 5,
            "urgency": 3,
            "risk": "low",
        },
        message_payload={"status": "failed", "reason": "provider_execution_failed"},
        attempts=attempts,
    )
    assert resolution.kind == "repair_process"
    assert resolution.repeated_failure_count >= 2

