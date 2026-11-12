"""Owner-triggered creation of a trusted pytest image; no model inference."""

import os

from .sandbox_runner import PYTHON


def saved_snapshot(store):
    configured = os.environ.get("SYNVIO_SANDBOX_SNAPSHOT", "").strip()
    if configured:
        return configured
    settings = store.get_json("settings", "sandbox", {})
    return settings.get("snapshot_id")


async def prepare(store):
    if saved_snapshot(store):
        return {"ready": True, "already_prepared": True}
    if not store.call("SET", store.key("setup-lock"), "1", "NX", "EX", 180):
        raise RuntimeError("Przygotowanie środowiska już trwa. Poczekaj chwilę.")
    producer = None
    try:
        from vercel import sandbox
        from vercel.sandbox import NetworkPolicy, SandboxResources

        # This trusted bootstrap has package-registry access before any user code is uploaded.
        producer = await sandbox.create_sandbox(name="synvio-pytest-image", persistent=True, execution_time_limit=120, resources=SandboxResources(vcpus=1, memory=2048), network_policy=NetworkPolicy.custom(allow={"pypi.org": (), "files.pythonhosted.org": ()}), env={}, ports=[])
        await producer.run_command("uv", ["venv", "--python", "python", "/vercel/sandbox/.venv"], capture_output=True, kill_after=30, check=True)
        await producer.run_command("uv", ["pip", "install", "--python", PYTHON, "pytest==9.2.0"], capture_output=True, kill_after=60, check=True)
        await producer.update(network_policy=NetworkPolicy.deny_all())
        await producer.run_command(PYTHON, ["-c", "import pytest; print(pytest.__version__)"], capture_output=True, kill_after=10, check=True)
        snapshot = await producer.snapshot(expiration=0)
        # Stop without destroying: the producer retains the image used by future disposable runs.
        await producer.stop()
        store.put_json("settings", "sandbox", {"snapshot_id": snapshot.id, "producer_name": producer.name}, expiry=0)
        return {"ready": True, "already_prepared": False}
    except Exception:
        if producer is not None:
            await producer.destroy()
        raise
    finally:
        store.call("DEL", store.key("setup-lock"))
