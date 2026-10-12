"""Isolated real resource service; no Engine, microphone or model startup."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from boxagent.interfaces.desktop.service import DesktopService
from boxagent.interfaces.desktop.pets.catalog import PetCatalog

root = Path(__file__).resolve().parents[2]
data = Path(os.environ["BOXAGENT_DATA_DIR"])
soul = data / "SOUL.md"
soul.write_text("名字：伙伴\n\n测试角色。\n")
avatar = root / "assets/vrm/models/zome"
service = DesktopService(backend=Mock(), catalog=PetCatalog(data / "catalog", default_pet=avatar, bundled_root=avatar.parent), data_dir=data, log_dir=data / "logs", soul_file=soul, directory=avatar)
bad = service.catalog.root / "broken"
bad.mkdir(parents=True)
(bad / "pet.json").write_text(json.dumps({"type": "vrm", "version": 1, "model": "model.vrm"}))
(bad / "model.vrm").write_bytes(b"deliberately broken GLTF fixture")
try:
    for line in sys.stdin:
        request = json.loads(line)
        try:
            result = service.dispatch(request["method"], request.get("params", {}))
            response = {"id": request["id"], "ok": True, "result": result}
        except Exception as error:
            response = {"id": request["id"], "ok": False, "error": str(error)}
        print(json.dumps(response), flush=True)
finally:
    service.close()
    (data / "service-closed").touch()
