import socket

from scripts.serve import reserve


def test_busy_preferred_port_selects_and_reserves_another():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        preferred = occupied.getsockname()[1]
        with reserve("127.0.0.1", preferred) as listener:
            actual = listener.getsockname()[1]
            assert actual > 0 and actual != preferred
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", actual)) == 0


def test_available_preferred_port_is_retained():
    with reserve("127.0.0.1", 0) as initial:
        available = initial.getsockname()[1]
    with reserve("127.0.0.1", available) as listener:
        assert listener.getsockname()[1] == available
