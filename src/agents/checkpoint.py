"""SQLAlchemy-backed LangGraph checkpoint saver.

This is the project-local equivalent of ``AsyncPostgresSaver``. It stores
LangGraph's typed checkpoint blobs, channel values and pending writes in the
same PostgreSQL database as the business data, so a worker can resume a graph
after a restart.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agents.state import OfferRequest
from src.models.enums import (
    Currency,
    CustomerOfferStatus,
    GloveSize,
    MaterialType,
    ProcurementProcedure,
    StandardizedColor,
    SupplierQuoteStatus,
    TenderSourceFormat,
    TenderStatus,
    UnitOfMeasure,
)
from src.models.orm import (
    WorkflowCheckpoint,
    WorkflowCheckpointBlob,
    WorkflowCheckpointWrite,
)
from src.models.schemas import (
    CustomerOffer,
    CustomerOfferLine,
    PriceTier,
    SupplierQuote,
    SupplierQuoteLine,
    Tender,
    TenderItem,
)
from src.services.matching_service import CatalogCandidate, MatchResult

__all__ = ["SqlAlchemyCheckpointSaver"]

_ALLOWED_CHECKPOINT_TYPES = (
    *(
        Currency,
        CustomerOfferStatus,
        GloveSize,
        MaterialType,
        ProcurementProcedure,
        StandardizedColor,
        SupplierQuoteStatus,
        TenderSourceFormat,
        TenderStatus,
        UnitOfMeasure,
        CustomerOffer,
        CustomerOfferLine,
        PriceTier,
        SupplierQuote,
        SupplierQuoteLine,
        Tender,
        TenderItem,
        OfferRequest,
        CatalogCandidate,
        MatchResult,
    ),
    # pygaeb is an optional dependency; allow its concrete checkpoint types
    # without importing the package when it is not installed.
    ("pygaeb.models.enums", "SourceVersion"),
    ("pygaeb.models.enums", "ExchangePhase"),
    ("pygaeb.models.enums", "BkdnType"),
    ("pygaeb.models.enums", "ItemType"),
    ("pygaeb.models.enums", "ValidationSeverity"),
    ("pygaeb.models.document", "GAEBDocument"),
)


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver[str]):
    """Persist LangGraph checkpoints through an async SQLAlchemy session factory."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        serde: Any | None = None,
    ) -> None:
        # Checkpoints contain project Pydantic models/enums. They are stored in
        # our own trusted PostgreSQL schema, so explicitly allow project classes
        # instead of relying on the serializer's permissive fallback.
        super().__init__(
            serde=serde or JsonPlusSerializer(allowed_msgpack_modules=list(_ALLOWED_CHECKPOINT_TYPES))
        )
        self._session_factory = session_factory

    def _load(self, value_type: str, value_blob: bytes) -> Any:
        return self.serde.loads_typed((value_type, value_blob))

    def _dump(self, value: Any) -> tuple[str, bytes]:
        return self.serde.dumps_typed(value)

    @staticmethod
    def _config(thread_id: str, checkpoint_ns: str, checkpoint_id: str | None) -> RunnableConfig:
        configurable: dict[str, str] = {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
        }
        if checkpoint_id is not None:
            configurable["checkpoint_id"] = checkpoint_id
        return {"configurable": configurable}

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        requested_id = get_checkpoint_id(config)

        async with self._session_factory() as session:
            statement = select(WorkflowCheckpoint).where(
                WorkflowCheckpoint.thread_id == thread_id,
                WorkflowCheckpoint.checkpoint_ns == checkpoint_ns,
            )
            if requested_id:
                statement = statement.where(WorkflowCheckpoint.checkpoint_id == requested_id)
            else:
                statement = statement.order_by(WorkflowCheckpoint.checkpoint_id.desc()).limit(1)
            row = (await session.execute(statement)).scalars().first()
            if row is None:
                return None

            checkpoint: Checkpoint = self._load(row.checkpoint_type, row.checkpoint_blob)
            metadata: CheckpointMetadata = self._load(row.metadata_type, row.metadata_blob)
            versions = checkpoint.get("channel_versions", {})
            channel_values: dict[str, Any] = {}
            if versions:
                blob_rows = (
                    (
                        await session.execute(
                            select(WorkflowCheckpointBlob).where(
                                WorkflowCheckpointBlob.thread_id == thread_id,
                                WorkflowCheckpointBlob.checkpoint_ns == checkpoint_ns,
                                WorkflowCheckpointBlob.channel.in_(list(versions)),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for blob in blob_rows:
                    if str(blob.version) != str(versions.get(blob.channel)) or blob.value_type == "empty":
                        continue
                    channel_values[blob.channel] = self._load(blob.value_type, blob.value_blob)

            write_rows = (
                (
                    await session.execute(
                        select(WorkflowCheckpointWrite)
                        .where(
                            WorkflowCheckpointWrite.thread_id == thread_id,
                            WorkflowCheckpointWrite.checkpoint_ns == checkpoint_ns,
                            WorkflowCheckpointWrite.checkpoint_id == row.checkpoint_id,
                        )
                        .order_by(WorkflowCheckpointWrite.task_id, WorkflowCheckpointWrite.idx)
                    )
                )
                .scalars()
                .all()
            )
            pending_writes = [
                (write.task_id, write.channel, self._load(write.value_type, write.value_blob))
                for write in write_rows
            ]
            checkpoint["channel_values"] = channel_values
            parent_config = (
                self._config(thread_id, checkpoint_ns, row.parent_checkpoint_id)
                if row.parent_checkpoint_id
                else None
            )
            return CheckpointTuple(
                config=self._config(thread_id, checkpoint_ns, row.checkpoint_id),
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
                pending_writes=pending_writes,
            )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_checkpoint_id = get_checkpoint_id(config)

        checkpoint_copy = checkpoint.copy()
        values = checkpoint_copy.pop("channel_values")  # type: ignore[misc]
        checkpoint_type, checkpoint_blob = self._dump(checkpoint_copy)
        metadata_type, metadata_blob = self._dump(get_checkpoint_metadata(config, metadata))

        async with self._session_factory() as session:
            for channel, version in new_versions.items():
                value_type, value_blob = (
                    ("empty", b"") if channel not in values else self._dump(values[channel])
                )
                await session.execute(
                    delete(WorkflowCheckpointBlob).where(
                        WorkflowCheckpointBlob.thread_id == thread_id,
                        WorkflowCheckpointBlob.checkpoint_ns == checkpoint_ns,
                        WorkflowCheckpointBlob.channel == channel,
                        WorkflowCheckpointBlob.version == str(version),
                    )
                )
                session.add(
                    WorkflowCheckpointBlob(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        channel=channel,
                        version=str(version),
                        value_type=value_type,
                        value_blob=value_blob,
                    )
                )

            existing = (
                (
                    await session.execute(
                        select(WorkflowCheckpoint).where(
                            WorkflowCheckpoint.thread_id == thread_id,
                            WorkflowCheckpoint.checkpoint_ns == checkpoint_ns,
                            WorkflowCheckpoint.checkpoint_id == checkpoint_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing is None:
                session.add(
                    WorkflowCheckpoint(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        checkpoint_id=checkpoint_id,
                        parent_checkpoint_id=parent_checkpoint_id,
                        checkpoint_type=checkpoint_type,
                        checkpoint_blob=checkpoint_blob,
                        metadata_type=metadata_type,
                        metadata_blob=metadata_blob,
                    )
                )
            else:
                existing.parent_checkpoint_id = parent_checkpoint_id
                existing.checkpoint_type = checkpoint_type
                existing.checkpoint_blob = checkpoint_blob
                existing.metadata_type = metadata_type
                existing.metadata_blob = metadata_blob
            await session.commit()

        return self._config(thread_id, checkpoint_ns, checkpoint_id)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        async with self._session_factory() as session:
            existing = {
                (row.task_id, row.idx)
                for row in (
                    await session.execute(
                        select(WorkflowCheckpointWrite).where(
                            WorkflowCheckpointWrite.thread_id == thread_id,
                            WorkflowCheckpointWrite.checkpoint_ns == checkpoint_ns,
                            WorkflowCheckpointWrite.checkpoint_id == checkpoint_id,
                            WorkflowCheckpointWrite.task_id == task_id,
                        )
                    )
                )
                .scalars()
                .all()
            }
            for idx, (channel, value) in enumerate(writes):
                write_idx = WRITES_IDX_MAP.get(channel, idx)
                if write_idx >= 0 and (task_id, write_idx) in existing:
                    continue
                value_type, value_blob = self._dump(value)
                session.add(
                    WorkflowCheckpointWrite(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        checkpoint_id=checkpoint_id,
                        task_id=task_id,
                        idx=write_idx,
                        channel=channel,
                        value_type=value_type,
                        value_blob=value_blob,
                        task_path=task_path,
                    )
                )
            await session.commit()

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        async with self._session_factory() as session:
            statement = select(WorkflowCheckpoint)
            if config is not None:
                thread_id = config["configurable"]["thread_id"]
                statement = statement.where(WorkflowCheckpoint.thread_id == thread_id)
                checkpoint_ns = config["configurable"].get("checkpoint_ns")
                if checkpoint_ns is not None:
                    statement = statement.where(WorkflowCheckpoint.checkpoint_ns == checkpoint_ns)
                checkpoint_id = get_checkpoint_id(config)
                if checkpoint_id:
                    statement = statement.where(WorkflowCheckpoint.checkpoint_id == checkpoint_id)
            if before is not None:
                before_id = get_checkpoint_id(before)
                if before_id:
                    statement = statement.where(WorkflowCheckpoint.checkpoint_id < before_id)
            statement = statement.order_by(WorkflowCheckpoint.checkpoint_id.desc())
            rows = (await session.execute(statement)).scalars().all()

        emitted = 0
        for row in rows:
            if limit is not None and emitted >= limit:
                break
            tuple_ = await self.aget_tuple(self._config(row.thread_id, row.checkpoint_ns, row.checkpoint_id))
            if tuple_ is None:
                continue
            if filter and not all(tuple_.metadata.get(key) == value for key, value in filter.items()):
                continue
            yield tuple_
            emitted += 1

    async def adelete_thread(self, thread_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(WorkflowCheckpointWrite).where(WorkflowCheckpointWrite.thread_id == thread_id)
            )
            await session.execute(
                delete(WorkflowCheckpointBlob).where(WorkflowCheckpointBlob.thread_id == thread_id)
            )
            await session.execute(delete(WorkflowCheckpoint).where(WorkflowCheckpoint.thread_id == thread_id))
            await session.commit()

    # The production graph is invoked asynchronously. Sync access is deliberately
    # unsupported so no accidental blocking database calls leak into the API.
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError("use aget_tuple with the async SQLAlchemy saver")

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise NotImplementedError("use aput with the async SQLAlchemy saver")

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise NotImplementedError("use aput_writes with the async SQLAlchemy saver")

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Any:
        raise NotImplementedError("use alist with the async SQLAlchemy saver")

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("use adelete_thread with the async SQLAlchemy saver")
