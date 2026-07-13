"""Memory browser (CONTRACT.md §2): CMA memories API behind the gateway.

Stubbed `available:false` unless CMA is configured. The anthropic SDK routes
memory-store calls under the agent-memory-2026-07-22 beta header on its own
(never combined with managed-agents on these calls) — do not add headers here.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from tp_gateway.relay import RunManager

router = APIRouter(prefix="/api/coach")


def _manager(request: Request) -> RunManager:
    return request.app.state.manager


def _memory_available(manager: RunManager) -> bool:
    s = manager.settings
    return bool(s.anthropic_api_key and s.cma_memory_store_id)


@router.get("/memory")
async def list_memory(request: Request) -> dict[str, Any]:
    manager = _manager(request)
    if not _memory_available(manager):
        return {"available": False, "memories": []}
    store_id = manager.settings.cma_memory_store_id
    memories: list[dict[str, Any]] = []
    async for item in manager.cma.client.beta.memory_stores.memories.list(store_id, depth=10):
        d = item if isinstance(item, dict) else item.model_dump(mode="json")
        if d.get("type") != "memory":
            continue  # prefix rollups are not files
        memories.append(
            {
                "id": d["id"],
                "path": d["path"],
                "size_bytes": d.get("content_size_bytes", 0),
                "updated_at": d.get("updated_at"),
            }
        )
    return {"available": True, "memories": memories}


@router.get("/memory/{memory_id}")
async def get_memory(request: Request, memory_id: str) -> dict[str, Any]:
    manager = _manager(request)
    if not _memory_available(manager):
        raise HTTPException(status_code=404, detail="memory is not available (CMA not configured)")
    store_id = manager.settings.cma_memory_store_id
    memory = await manager.cma.client.beta.memory_stores.memories.retrieve(
        memory_id, memory_store_id=store_id, view="full"
    )
    d = memory if isinstance(memory, dict) else memory.model_dump(mode="json")
    return {"id": d["id"], "path": d["path"], "content": d.get("content") or ""}
