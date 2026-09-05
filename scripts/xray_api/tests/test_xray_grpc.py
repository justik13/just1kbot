from unittest.mock import MagicMock, patch

from xray_grpc import XrayGrpcClient
import xray.app.stats.command.command_pb2 as stats_cmd


def test_add_user_serialization():
    client = XrayGrpcClient()
    mock_channel = MagicMock()
    mock_stub = MagicMock()

    with patch.object(client, "_get_channel", return_value=mock_channel):
        with patch("xray.app.proxyman.command.command_pb2_grpc.HandlerServiceStub", return_value=mock_stub):
            user_uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
            res = client.add_user("inbound-de", user_uuid)
            assert res is True
            assert mock_stub.AlterInbound.called
            call_args = mock_stub.AlterInbound.call_args[0][0]
            assert call_args.tag == "inbound-de"
            assert call_args.operation.type == "xray.app.proxyman.command.AddUserOperation"


def test_remove_user_serialization():
    client = XrayGrpcClient()
    mock_channel = MagicMock()
    mock_stub = MagicMock()

    with patch.object(client, "_get_channel", return_value=mock_channel):
        with patch("xray.app.proxyman.command.command_pb2_grpc.HandlerServiceStub", return_value=mock_stub):
            user_uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
            res = client.remove_user("inbound-nl", user_uuid)
            assert res is True
            assert mock_stub.AlterInbound.called
            call_args = mock_stub.AlterInbound.call_args[0][0]
            assert call_args.tag == "inbound-nl"
            assert call_args.operation.type == "xray.app.proxyman.command.RemoveUserOperation"


def test_stats_normalization_via_query_stats():
    client = XrayGrpcClient()
    mock_channel = MagicMock()
    mock_stub = MagicMock()

    # Simulate QueryStats response with multiple records for the same user (e.g. from different inbounds)
    mock_stub.GetUsersStats.side_effect = Exception("Not implemented")
    mock_stub.QueryStats.return_value = stats_cmd.QueryStatsResponse(
        stat=[
            stats_cmd.Stat(name="user>>>user-1>>>traffic>>>uplink", value=100),
            stats_cmd.Stat(name="user>>>user-1>>>traffic>>>downlink", value=200),
            stats_cmd.Stat(name="user>>>user-1>>>traffic>>>uplink", value=50),
            stats_cmd.Stat(name="user>>>user-2>>>traffic>>>uplink", value=300),
            stats_cmd.Stat(name="user>>>user-2>>>traffic>>>downlink", value=400),
            stats_cmd.Stat(name="inbound>>>inbound-de>>>traffic>>>uplink", value=9999),
        ]
    )

    with patch.object(client, "_get_channel", return_value=mock_channel):
        with patch("xray.app.stats.command.command_pb2_grpc.StatsServiceStub", return_value=mock_stub):
            stats = client.get_users_stats(reset=False)
            assert stats == {
                "user-1": {"uplink": 150, "downlink": 200},
                "user-2": {"uplink": 300, "downlink": 400},
            }


def test_stats_normalization_via_get_users_stats():
    client = XrayGrpcClient()
    mock_channel = MagicMock()
    mock_stub = MagicMock()

    mock_stub.GetUsersStats.return_value = stats_cmd.GetUsersStatsResponse(
        users=[
            stats_cmd.UserStat(
                email="user-1",
                traffic=stats_cmd.TrafficUserStat(uplink=1000, downlink=2000),
            ),
            stats_cmd.UserStat(
                email="user-2",
                traffic=stats_cmd.TrafficUserStat(uplink=3000, downlink=4000),
            ),
        ]
    )

    with patch.object(client, "_get_channel", return_value=mock_channel):
        with patch("xray.app.stats.command.command_pb2_grpc.StatsServiceStub", return_value=mock_stub):
            stats = client.get_users_stats(reset=False)
            assert stats == {
                "user-1": {"uplink": 1000, "downlink": 2000},
                "user-2": {"uplink": 3000, "downlink": 4000},
            }


def test_probe_user_presence_and_verify_absent_lifecycle():
    """H2: Non-destructive presence probe and absence verification lifecycle without AlterInbound mutations."""
    client = XrayGrpcClient()
    mock_channel = MagicMock()
    mock_stub = MagicMock()

    tag = "inbound-de"
    user_uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"

    # Initially user is not present
    assert client.probe_user_presence(tag, user_uuid) is False
    assert client.verify_user_absent(tag, user_uuid) is True

    # Add user
    with patch.object(client, "_get_channel", return_value=mock_channel):
        with patch("xray.app.proxyman.command.command_pb2_grpc.HandlerServiceStub", return_value=mock_stub):
            assert client.add_user(tag, user_uuid) is True

    # After add, probe returns True and verify_absent returns False without ANY AlterInbound calls
    mock_stub.AlterInbound.reset_mock()
    assert client.probe_user_presence(tag, user_uuid) is True
    assert client.verify_user_absent(tag, user_uuid) is False
    assert not mock_stub.AlterInbound.called

    # Remove user
    with patch.object(client, "_get_channel", return_value=mock_channel):
        with patch("xray.app.proxyman.command.command_pb2_grpc.HandlerServiceStub", return_value=mock_stub):
            assert client.remove_user(tag, user_uuid) is True

    # After remove, probe returns False and verify_absent returns True without AlterInbound calls
    mock_stub.AlterInbound.reset_mock()
    assert client.probe_user_presence(tag, user_uuid) is False
    assert client.verify_user_absent(tag, user_uuid) is True
    assert not mock_stub.AlterInbound.called

