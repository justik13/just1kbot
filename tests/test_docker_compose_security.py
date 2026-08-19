from pathlib import Path

import yaml


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
    compose_text = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    data = yaml.safe_load(compose_text)

    # 1. Networks defined
    networks = data.get("networks", {})
    assert "frontend_net" in networks
    assert "backend_net" in networks

    # 2. Service network assignments
    services = data.get("services", {})
    assert services["db"]["networks"] == ["backend_net"]
    assert services["redis"]["networks"] == ["backend_net"]
    assert services["migrate"]["networks"] == ["backend_net"]
    assert services["backup"]["networks"] == ["backend_net"]
    assert services["caddy"]["networks"] == ["frontend_net"]
    assert set(services["bot"]["networks"]) == {"frontend_net", "backend_net"}

    # 3. Redis bounds
    redis_cmd = services["redis"]["command"]
    assert "--maxmemory 384mb" in redis_cmd
    assert "--maxmemory-policy noeviction" in redis_cmd

    # 4. Caddy ports
    caddy_ports = services["caddy"]["ports"]
    assert "80:80" in caddy_ports
    assert "443:443" in caddy_ports
    assert "443:443/udp" in caddy_ports

    # 5. Postgres and Bot graceful shutdown
    assert services["db"].get("stop_grace_period") == "30s"
    assert services["bot"].get("stop_grace_period") == "30s"
