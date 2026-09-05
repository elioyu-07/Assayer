"""Generic immutable Evidence collection paging and coverage helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from .contract import PlatformContractError


class EvidenceCollectionPager:
    """Page one Host-declared collection without domain interpretation."""

    def __init__(self, collection: Mapping[str, Any], *, work_item_id: str = "", collection_id: str = ""):
        self.collection = collection
        self.work_item_id = work_item_id
        self.collection_id = collection_id
        self.item_ids = tuple(str(item) for item in collection.get("itemIds", ()))
        self.items = tuple(collection.get("items", ()))
        if len(self.item_ids) != len(self.items) or len(set(self.item_ids)) != len(self.item_ids):
            raise PlatformContractError("INVALID_EVIDENCE_COLLECTION", "Evidence collection IDs and items must align uniquely")
        self.groups = collection.get("groups", {})

    def page(self, *, cursor: str | None = None, page_size: int | None = None, group_key: str | None = None) -> dict[str, Any]:
        if page_size is not None and (not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1):
            raise PlatformContractError("INVALID_PAGE_SIZE", "Evidence collection page size must be a positive integer")
        size = min(page_size or 20, 100)
        selected_ids, selected_items = self.item_ids, self.items
        if group_key is not None:
            group = self.groups.get(group_key)
            if group is None:
                raise PlatformContractError("UNKNOWN_EVIDENCE_GROUP", "Evidence collection group key is not valid for this collection")
            wanted = set(group["itemIds"])
            pairs = tuple((item_id, item) for item_id, item in zip(self.item_ids, self.items) if item_id in wanted)
            selected_ids = tuple(item_id for item_id, _ in pairs)
            selected_items = tuple(item for _, item in pairs)
        fingerprint = hashlib.sha256("\x1f".join((self.work_item_id, self.collection_id, group_key or "", *selected_ids)).encode()).hexdigest()[:16]
        start = 0
        if cursor is not None:
            if not isinstance(cursor, str) or not cursor.startswith(f"{fingerprint}:"):
                raise PlatformContractError("INVALID_CURSOR", "Evidence collection cursor does not match the requested collection and group")
            try:
                start = int(cursor.split(":", 1)[1])
            except (TypeError, ValueError):
                raise PlatformContractError("INVALID_CURSOR", "Evidence collection cursor is malformed") from None
            if start < 0 or start > len(selected_ids):
                raise PlatformContractError("INVALID_CURSOR", "Evidence collection cursor is outside the collection")
        end = min(start + size, len(selected_ids))
        return {
            "items": selected_items[start:end], "itemIds": list(selected_ids[start:end]),
            "page": {"start": start, "count": end - start, "total": len(selected_ids)},
            "nextCursor": f"{fingerprint}:{end}" if end < len(selected_ids) else None,
            "groupSummaries": [{
                "groupKey": group["groupKey"], "values": group["values"],
                "count": len(group["itemIds"]), "itemIds": list(group["itemIds"]),
            } for group in self.groups.values()] if group_key is None and cursor is None else [],
            "selectedItems": len(selected_ids), "selectedGroupKey": group_key,
        }


__all__ = ["EvidenceCollectionPager"]
