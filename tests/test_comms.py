from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.comms.lanes import PrincipalMessageLane
from astrata.storage.db import AstrataDatabase


def test_operator_message_lane_round_trip(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        db = AstrataDatabase(base / "astrata.db")
        db.initialize()
        lane = PrincipalMessageLane(db=db)
        sent = lane.send(
            sender="principal",
            recipient="astrata",
            kind="request",
            intent="principal_message",
            payload={"message": "hello"},
        )
        inbox = lane.list_messages(recipient="astrata")
        assert inbox
        assert inbox[-1].communication_id == sent.communication_id
        acked = lane.acknowledge(sent.communication_id)
        assert acked is not None
        assert acked.status == "acknowledged"

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))
