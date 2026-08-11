from pathlib import Path


def test_database_and_redis_are_not_published_on_host():
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert '"5432:5432"' not in compose
    assert '"6379:6379"' not in compose


def test_public_compose_ports_are_limited_to_caddy():
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert '"80:80"' in compose
    assert '"443:443"' in compose
