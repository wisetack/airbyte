/*
 * Copyright (c) 2024 Airbyte, Inc., all rights reserved.
 */

package io.airbyte.cdk.load.config

import io.airbyte.cdk.load.command.DestinationCatalog
import io.airbyte.cdk.load.command.DestinationConfiguration
import io.airbyte.cdk.load.command.DestinationStream
import io.airbyte.cdk.load.message.BatchEnvelope
import io.airbyte.cdk.load.message.ChannelMessageQueue
import io.airbyte.cdk.load.message.DestinationRecordRaw
import io.airbyte.cdk.load.message.MultiProducerChannel
import io.airbyte.cdk.load.message.PartitionedQueue
import io.airbyte.cdk.load.message.PipelineEvent
import io.airbyte.cdk.load.message.StreamKey
import io.airbyte.cdk.load.message.StrictPartitionedQueue
import io.airbyte.cdk.load.pipeline.BatchUpdate
import io.airbyte.cdk.load.state.ReservationManager
import io.airbyte.cdk.load.task.implementor.FileAggregateMessage
import io.airbyte.cdk.load.task.implementor.FileTransferQueueMessage
import io.airbyte.cdk.load.write.LoadStrategy
import io.github.oshai.kotlinlogging.KotlinLogging
import io.micronaut.context.annotation.Factory
import io.micronaut.context.annotation.Secondary
import io.micronaut.context.annotation.Value
import jakarta.inject.Named
import jakarta.inject.Singleton
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.runBlocking

const val CHECK_STREAM_NAMESPACE = "airbyte_internal_test"

/** Factory for instantiating beans necessary for the sync process. */
@Factory
class SyncBeanFactory {
    private val log = KotlinLogging.logger {}

    /* ******************
     * RESOURCE MANAGERS
     * ******************/

    @Singleton
    @Secondary
    @Named("globalMemoryManager")
    fun globalMemoryManager(): ReservationManager {
        return ReservationManager(Runtime.getRuntime().maxMemory())
    }

    @Singleton
    @Named("queueMemoryManager")
    fun queueMemoryMananger(
        config: DestinationConfiguration,
        @Named("globalMemoryManager") globalMemoryManager: ReservationManager
    ): ReservationManager {
        val recordQueueBytes =
            config.maxMessageQueueMemoryUsageRatio * globalMemoryManager.totalCapacityBytes
        val reservation = runBlocking {
            globalMemoryManager.reserve(recordQueueBytes.toLong(), null)
        }
        return ReservationManager(reservation.bytesReserved)
    }

    /** DEPRECATED: Old interface. */
    @Singleton
    @Named("diskManager")
    fun diskManager(
        @Value("\${airbyte.destination.core.resources.disk.bytes}") availableBytes: Long,
    ): ReservationManager {
        return ReservationManager(availableBytes)
    }

    /* ********************
     * ASYNCHRONOUS QUEUES
     * ********************/

    /**
     * The queue that sits between the aggregation (SpillToDiskTask) and load steps
     * (ProcessRecordsTask).
     *
     * Since we are buffering on disk, we must consider the available disk space in our depth
     * configuration.
     *
     * DEPRECATED: Old interface.
     */
    @Singleton
    @Named("fileAggregateQueue")
    fun fileAggregateQueue(
        @Value("\${airbyte.destination.core.resources.disk.bytes}") availableBytes: Long,
        config: DestinationConfiguration,
        catalog: DestinationCatalog
    ): MultiProducerChannel<FileAggregateMessage> {
        val streamCount = catalog.size()
        // total batches by disk capacity
        val maxBatchesThatFitOnDisk = (availableBytes / config.recordBatchSizeBytes).toInt()
        // account for batches in flight processing by the workers
        val maxBatchesMinusUploadOverhead =
            maxBatchesThatFitOnDisk - config.numProcessRecordsWorkers
        // ideally we'd allow enough headroom to smooth out rate differences between consumer /
        // producer streams
        val idealDepth = 4 * config.numProcessRecordsWorkers
        // take the smaller of the two—this should be the idealDepth except in corner cases
        val capacity = min(maxBatchesMinusUploadOverhead, idealDepth)
        log.info { "Creating file aggregate queue with limit $capacity" }
        val channel = Channel<FileAggregateMessage>(capacity)
        return MultiProducerChannel(streamCount.toLong(), channel, "fileAggregateQueue")
    }

    /** DEPRECATED: Old interface. */
    @Singleton
    @Named("batchQueue")
    fun batchQueue(
        config: DestinationConfiguration,
    ): MultiProducerChannel<BatchEnvelope<*>> {
        val channel = Channel<BatchEnvelope<*>>(config.batchQueueDepth)
        return MultiProducerChannel(config.numProcessRecordsWorkers.toLong(), channel, "batchQueue")
    }

    @Singleton
    @Named("fileMessageQueue")
    fun fileMessageQueue(
        config: DestinationConfiguration,
    ): MultiProducerChannel<FileTransferQueueMessage> {
        val channel = Channel<FileTransferQueueMessage>(config.batchQueueDepth)
        return MultiProducerChannel(1, channel, "fileMessageQueue")
    }

    @Singleton
    @Named("openStreamQueue")
    class OpenStreamQueue : ChannelMessageQueue<DestinationStream>(Channel(Channel.UNLIMITED))

    /**
     * A single record queue for the whole sync, containing all streams, optionally partitioned by a
     * configurable number of partitions. Number of partitions is controlled by the specified
     * LoadStrategy, if any.
     */
    @Singleton
    @Named("pipelineInputQueue")
    fun pipelineInputQueue(
        loadStrategy: LoadStrategy? = null,
        @Named("isFileTransfer") isFileTransfer: Boolean = false,
    ): PartitionedQueue<PipelineEvent<StreamKey, DestinationRecordRaw>> {
        return StrictPartitionedQueue(
            Array(if (isFileTransfer) 1 else loadStrategy?.inputPartitions ?: 1) {
                ChannelMessageQueue(Channel(Channel.UNLIMITED))
            }
        )
    }

    /** A queue for updating batch states, which is not partitioned. */
    @Singleton
    @Named("batchStateUpdateQueue")
    fun batchStateUpdateQueue(): ChannelMessageQueue<BatchUpdate> {
        return ChannelMessageQueue(Channel(100))
    }

    /* *************
     * GLOBAL FLAGS
     * *************/

    /**
     * If the client uses a new-style LoadStrategy, then we need to checkpoint by checkpoint id
     * instead of record index.
     */
    @Singleton
    @Named("checkpointById")
    fun isCheckpointById(loadStrategy: LoadStrategy? = null): Boolean = loadStrategy != null

    /** True if the catalog has at least one stream that includeFiles. */
    @Singleton
    @Named("isFileTransfer")
    fun isFileTransfer(catalog: DestinationCatalog): Boolean =
        catalog.streams.any { it.includeFiles }

    /* *************
     * GLOBAL STATE
     * *************/

    @Singleton
    @Named("defaultDestinationTaskLauncherHasThrown")
    fun defaultDestinationTaskLauncherHasThrown(): AtomicBoolean = AtomicBoolean(false)
}
