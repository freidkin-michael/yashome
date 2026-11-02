"""The mqtt: block of an ESPHome node: a device account, the broker as the dashboard is reached, a valid
secrets.yaml line whatever the password holds; only the house token sees it."""
import os

import yaml
from fastapi.testclient import TestClient

import app as core

HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


class SecretLoader(yaml.SafeLoader):
    pass


SecretLoader.add_constructor("!secret", lambda loader, node: ("secret", loader.construct_scalar(node)))


def test_no_device_account_gives_no_block(monkeypatch):
    monkeypatch.delenv("MQTT_DEVICE_USERS", raising=False)
    b = TestClient(core.app).get("/api/esphome/mqtt_block", headers=HOUSE).json()
    assert b == {"accounts": [], "yaml": "", "secret": ""}


def test_the_block_and_its_secret(monkeypatch):
    monkeypatch.delenv("BIND_ADDRESS", raising=False)
    monkeypatch.setenv("MQTT_DEVICE_USERS", "esp, screen")
    monkeypatch.setenv("MQTT_ESP_PASSWORD", 'p"w: #x')
    monkeypatch.setenv("MQTT_SCREEN_PASSWORD", "t-pw")
    c = TestClient(core.app)
    b = c.get("/api/esphome/mqtt_block", headers=HOUSE).json()
    assert b["accounts"] == ["esp", "screen"] and b["account"] == "esp"
    m = yaml.load(b["yaml"], Loader=SecretLoader)["mqtt"]
    assert m["broker"] == "testserver" and m["username"] == "esp" and m["discovery"] is True
    assert "BIND_ADDRESS" in b["warning"]                           # a name may not reach the broker
    assert m["password"] == ("secret", "mqtt_password")
    assert yaml.safe_load(b["secret"]) == {"mqtt_password": 'p"w: #x'}
    assert c.get("/api/esphome/mqtt_block?account=screen", headers=HOUSE).json()["account"] == "screen"
    assert c.get("/api/esphome/mqtt_block?account=nope", headers=HOUSE).json()["account"] == "esp"
    monkeypatch.setenv("BIND_ADDRESS", "192.0.2.5")                 # the house's LAN address: given to the nodes
    b = c.get("/api/esphome/mqtt_block", headers=HOUSE).json()
    assert "broker: 192.0.2.5" in b["yaml"] and b["warning"] == ""


def test_only_the_house_token(monkeypatch):
    c = TestClient(core.app)
    assert c.get("/api/esphome/mqtt_block").status_code == 401
    monkeypatch.setattr(core, "TOKEN_CHECKS", core.TOKEN_CHECKS + [lambda tok, m, p: tok == "a-full-phone"])
    assert c.get("/api/esphome/mqtt_block", headers={"Authorization": "Bearer a-full-phone"}).status_code == 403


def test_the_host_port_and_a_loopback_dashboard(monkeypatch):
    monkeypatch.delenv("BIND_ADDRESS", raising=False)
    monkeypatch.setenv("MQTT_DEVICE_USERS", "esp")
    monkeypatch.setenv("MQTT_ESP_PASSWORD", "pw")
    monkeypatch.setenv("MQTT_HOST_PORT", "18830")
    c = TestClient(core.app)
    r = TestClient(core.app, base_url="http://192.0.2.9:8765").get("/api/esphome/mqtt_block", headers=HOUSE)
    assert "port: 18830" in r.json()["yaml"] and r.headers["cache-control"] == "no-store" and r.json()["warning"] == ""
    r = TestClient(core.app, base_url="http://192.0.2.9:8765").get("/api/esphome/mqtt_block", headers={
        **HOUSE, "X-Forwarded-Proto": "https"}).json()
    assert "BIND_ADDRESS" in r["warning"]
    monkeypatch.setenv("MQTT_HOST_PORT", "0.0.0.0:1884")
    assert "port: 1884" in c.get("/api/esphome/mqtt_block", headers=HOUSE).json()["yaml"]
    r = TestClient(core.app, base_url="http://localhost:8765").get("/api/esphome/mqtt_block", headers=HOUSE).json()
    assert "BIND_ADDRESS" in r["warning"] and "broker: localhost" not in r["yaml"]
