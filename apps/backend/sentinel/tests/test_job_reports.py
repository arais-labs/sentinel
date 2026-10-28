import asyncio

from app.models import Session
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.job_reports import deliver_job_report
from tests.fake_db import FakeDB

JOB = {
    "id": "job-1",
    "status": "completed",
    "returncode": 0,
    "command": "make test",
    "pane_id": "%1",
}


def test_job_report_is_a_notice_for_its_own_agent_and_wakes_an_idle_one():
    async def check():
        db = FakeDB()
        session = Session(user_id="local", title="main", status="active")
        db.add(session)
        registry = AgentRunRegistry()
        wakeups = []

        async def wakeup(sid):
            wakeups.append(sid)

        registry.configure_idle_interjections_callback(wakeup)
        assert await deliver_job_report(
            db, session, JOB, stdout_tail="ok", stderr_tail="", run_registry=registry, wake=True
        )
        assert not await deliver_job_report(
            db, session, JOB, stdout_tail="ok", stderr_tail="", run_registry=registry, wake=True
        )
        item = registry.drain_interjections(str(session.id))[0]
        assert item.role == "user" and "[Machine Job Report]" in item.content[0].text
        # Shown as a system notice, never as the user's own message.
        assert item.metadata["notice"]["title"] == "Background job report"
        assert (
            item.metadata["source"] == "runtime_job_completion"
            and item.metadata["steering"] == "pending"
        )
        assert wakeups == [str(session.id)]

    asyncio.run(check())


def test_finished_sub_agent_gets_its_report_without_being_woken():
    async def check():
        db = FakeDB()
        parent = Session(user_id="local", title="main", status="active")
        db.add(parent)
        child = Session(
            user_id="local", title="sub-agent:task", status="active", parent_session_id=parent.id
        )
        db.add(child)
        registry = AgentRunRegistry()
        wakeups = []

        async def wakeup(sid):
            wakeups.append(sid)

        registry.configure_idle_interjections_callback(wakeup)
        assert await deliver_job_report(
            db, child, JOB, stdout_tail="", stderr_tail="", run_registry=registry, wake=False
        )
        assert not wakeups
        assert not registry.peek_interjections(str(parent.id))  # the parent never sees it
        assert len(registry.peek_interjections(str(child.id))) == 1

    asyncio.run(check())
