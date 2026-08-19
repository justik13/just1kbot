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


def test_caddy_config_does_not_require_custom_plugins():
    root = Path(__file__).parents[1]
    caddyfile = (root / "Caddyfile").read_text(encoding="utf-8")
    caddyfile_ci = (root / "Caddyfile.ci").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile.caddy").read_text(encoding="utf-8")

    assert "order rate_limit" not in caddyfile
    assert "rate_limit {" not in caddyfile
    assert "order rate_limit" not in caddyfile_ci
    assert "rate_limit {" not in caddyfile_ci
    assert "xcaddy" not in dockerfile


def test_compose_network_segmentation_and_resource_bounds():
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert "frontend_net" in compose
    assert "backend_net" in compose
    assert "--maxmemory 384mb" in compose
    assert "--maxmemory-policy noeviction" in compose
    assert '"443:443/udp"' in compose
