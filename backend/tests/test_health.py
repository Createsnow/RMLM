"""容器健康检查：live 只看进程；ready 要能连上数据库，连不上返回 503（compose 据此等 api 就绪后再起 web）。"""
from fastapi.testclient import TestClient

from app import main
from app.main import app


def test_health_live_and_ready():
    with TestClient(app) as c:
        assert c.get("/health/live").json() == {"status": "UP"}
        r = c.get("/health/ready")
        assert r.status_code == 200 and r.json() == {"status": "UP", "db": "UP"}


def test_health_ready_db_down(monkeypatch):
    def broken():
        raise OSError("db down")

    with TestClient(app) as c:
        monkeypatch.setattr(main.dbmod.engine, "connect", broken)
        r = c.get("/health/ready")
        assert r.status_code == 503 and r.json()["db"] == "DOWN"
        assert c.get("/health/live").status_code == 200
