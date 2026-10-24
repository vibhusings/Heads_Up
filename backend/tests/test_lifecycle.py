import sqlite3
from datetime import datetime, timedelta, timezone

from headsup.db import FoundationRepository
from headsup.lifecycle import LifecycleService
from headsup.models import Action, Plan


def test_executor_skips_dependencies_and_keeps_independent_actions(tmp_path):
    db = sqlite3.connect(tmp_path / "lifecycle.db")
    repository = FoundationRepository(db)
    now = datetime.now(timezone.utc)
    plan = Plan(
        id="dependencies",
        version=1,
        impact_id="impact",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        actions=[
            Action(id="calendar", type="send_message", message_text="missing recipient"),
            Action(
                id="dependent",
                type="send_message",
                contact_id="contact_sam",
                message_text="The event moved",
                depends_on_action_ids=["calendar"],
            ),
            Action(id="independent", type="send_message", contact_id="contact_sam", message_text="Update"),
        ],
    )
    repository.put_plan(plan)

    outcome = LifecycleService(repository).approve("dependencies", 1, "user", "button")

    assert outcome.plan.status == "partially_failed"
    assert [result.status for result in outcome.results] == ["failed", "skipped", "simulated"]
    assert outcome.results[1].error_code == "DEPENDENCY_FAILED"
    duplicate = LifecycleService(repository).approve("dependencies", 1, "user", "button")
    assert duplicate.duplicate is True
    assert len(repository.session().results) == 3
    db.close()
