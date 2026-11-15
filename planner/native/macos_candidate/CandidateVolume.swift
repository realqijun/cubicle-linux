// Original Planner candidate implementation; no Apple sample source is copied.
import Foundation
import FSKit

/// Compileable native callback seam, deliberately unavailable to the launcher.
/// Namespace operations remain blocked until their shared broker protocol exists.
/// This class never opens a backing path or grants kernel-offloaded I/O.
final class CandidateVolume: FSVolume, FSVolume.Handler,
    FSVolume.DataCacheHandler, FSVolume.ReadWriteHandler {
    private let broker: any CandidateBroker
    private let operationLock = NSLock()

    init(broker: any CandidateBroker) {
        self.broker = broker
        super.init(volumeID: FSVolume.Identifier(), volumeName: FSFileName(string: "Planner candidate"))
    }

    var supportedVolumeCapabilities: FSVolume.SupportedCapabilities { .init() }
    var volumeStatistics: FSStatFSResult { FSStatFSResult(fileSystemTypeName: "planner_candidate") }
    var maximumLinkCount: Int { 1 }
    var maximumNameLength: Int { 255 }
    var restrictsOwnershipChanges: Bool { true }
    var truncatesLongNames: Bool { false }
    var maximumXattrSize: Int { 0 }
    var maximumFileSize: UInt64 { UInt64(Int64.max) }

    private func serialized<T>(_ body: () throws -> T) rethrows -> T {
        operationLock.lock()
        defer { operationLock.unlock() }
        return try body()
    }

    private func candidate(_ item: FSItem) throws -> CandidateItem {
        guard let item = item as? CandidateItem else { throw POSIXError(.ESTALE) }
        return item
    }

    func open(_ item: FSItem, modes: FSVolume.OpenModes,
              cacheMode: FSVolume.DataCacheMode, context: FSContext,
              replyHandler reply: @escaping (FSOpenItemResult?, Error?) -> Void) {
        do {
            try serialized { try broker.authorizeOpen(objectID: candidate(item).objectID, modes: modes) }
            reply(FSOpenItemResult(grantedCoherency: .noCache), nil)
        } catch { reply(nil, error) }
    }

    func upgrade(_ item: FSItem, cacheMode: FSVolume.DataCacheMode, context: FSContext,
                 replyHandler reply: @escaping (FSUpgradeItemResult?, Error?) -> Void) {
        do {
            try serialized { try broker.authorizeUpgrade(objectID: candidate(item).objectID) }
            reply(FSUpgradeItemResult(grantedCoherency: .noCache), nil)
        } catch { reply(nil, error) }
    }

    func close(_ item: FSItem, context: FSContext, replyHandler reply: @escaping () -> Void) {
        reply()
    }

    /// Separate callable methods permit callback-boundary checks without mounting
    /// or manufacturing FSContext instances (whose initializer is unavailable).
    func readBytes(from item: FSItem, offset: Int64, length: Int) throws -> (Data, FSItem.Attributes) {
        guard offset >= 0, length >= 0, length <= 8 * 1024 * 1024,
              offset <= Int64.max - Int64(length) else { throw POSIXError(.EINVAL) }
        return try serialized {
            let id = try candidate(item).objectID
            let bytes = try broker.read(objectID: id, offset: offset, length: length)
            guard bytes.count <= length else { throw POSIXError(.EIO) }
            return (bytes, try broker.attributes(objectID: id))
        }
    }

    func writeBytes(_ contents: Data, to item: FSItem, offset: Int64) throws -> (Int, FSItem.Attributes) {
        guard offset >= 0, contents.count <= 8 * 1024 * 1024,
              offset <= Int64.max - Int64(contents.count) else { throw POSIXError(.EINVAL) }
        return try serialized {
            let id = try candidate(item).objectID
            let count = try broker.write(objectID: id, offset: offset, contents: contents)
            guard count >= 0, count <= contents.count else { throw POSIXError(.EIO) }
            return (count, try broker.attributes(objectID: id))
        }
    }

    func read(from item: FSItem, at offset: off_t, length: Int,
              into buffer: FSMutableFileDataBuffer,
              replyHandler reply: @escaping (FSReadFileResult?, Error?) -> Void) {
        do {
            let (bytes, attributes) = try readBytes(from: item, offset: Int64(offset), length: length)
            try buffer.withUnsafeMutableBytes { destination in
                guard bytes.count <= destination.count else { throw POSIXError(.EIO) }
                _ = bytes.copyBytes(to: destination.bindMemory(to: UInt8.self))
            }
            guard let result = FSReadFileResult(bytesRead: bytes.count, itemAttributes: attributes)
            else { throw POSIXError(.EIO) }
            reply(result, nil)
        } catch { reply(nil, error) }
    }

    func write(contents: Data, to item: FSItem, at offset: off_t,
               replyHandler reply: @escaping (FSWriteFileResult?, Error?) -> Void) {
        do {
            let (count, attributes) = try writeBytes(contents, to: item, offset: Int64(offset))
            guard let result = FSWriteFileResult(bytesWritten: count, itemAttributes: attributes, freeSpace: nil)
            else { throw POSIXError(.EIO) }
            reply(result, nil)
        } catch { reply(nil, error) }
    }

    // No filesystem can currently be activated or mounted through this seam.
    func activateVolume(options: FSTaskOptions, replyHandler reply: @escaping (FSActivateResult?, Error?) -> Void) {
        reply(nil, POSIXError(.ENOTCONN))
    }
    func deactivateVolume(options: FSDeactivateOptions, replyHandler reply: @escaping (Error?) -> Void) {
        serialized { broker.disconnect() }
        reply(nil)
    }
    func mount(options: FSTaskOptions, replyHandler reply: @escaping (Error?) -> Void) { reply(POSIXError(.ENOTCONN)) }
    func unmount(replyHandler reply: @escaping () -> Void) { serialized { broker.disconnect() }; reply() }
    func synchronize(flags: FSSyncFlags, replyHandler reply: @escaping (Error?) -> Void) { reply(POSIXError(.ENOTCONN)) }
    func reclaimItem(_ item: FSItem, replyHandler reply: @escaping (Error?) -> Void) { reply(nil) }
    func lookupItem(named name: FSFileName, in directory: FSItem, context: FSContext,
                    replyHandler reply: @escaping (FSLookupItemResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTCONN)) }
    func createItem(named name: FSFileName, type: FSItem.ItemType, in directory: FSItem,
                    attributes: FSItem.SetAttributesRequest, context: FSContext,
                    replyHandler reply: @escaping (FSCreateItemResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTCONN)) }
    func createSymbolicLink(named name: FSFileName, in directory: FSItem,
                           attributes: FSItem.SetAttributesRequest, linkContents: FSFileName, context: FSContext,
                           replyHandler reply: @escaping (FSCreateSymlinkResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTSUP)) }
    func createLink(to item: FSItem, named name: FSFileName, in directory: FSItem, context: FSContext,
                    replyHandler reply: @escaping (FSCreateLinkResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTSUP)) }
    func renameItem(_ item: FSItem, inDirectory source: FSItem, named name: FSFileName,
                    to destinationName: FSFileName, inDirectory destination: FSItem, overItem: FSItem?,
                    context: FSContext, replyHandler reply: @escaping (FSRenameItemResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTCONN)) }
    func removeItem(_ item: FSItem, named name: FSFileName, from directory: FSItem, context: FSContext,
                    replyHandler reply: @escaping (FSRemoveItemResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTCONN)) }
    func getAttributes(_ desired: FSItem.GetAttributesRequest, of item: FSItem, context: FSContext,
                       replyHandler reply: @escaping (FSGetAttributesResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTCONN)) }
    func setAttributes(_ attributes: FSItem.SetAttributesRequest, on item: FSItem, context: FSContext,
                       replyHandler reply: @escaping (FSSetAttributesResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTCONN)) }
    func enumerateDirectory(_ directory: FSItem, startingAt cookie: FSDirectoryCookie, verifier: FSDirectoryVerifier,
                            attributes: FSItem.GetAttributesRequest?, packer: FSDirectoryEntryPacker, context: FSContext,
                            replyHandler reply: @escaping (FSEnumerateDirectoryResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTCONN)) }
    func readSymbolicLink(_ item: FSItem, context: FSContext,
                          replyHandler reply: @escaping (FSReadSymlinkResult?, Error?) -> Void) { reply(nil, POSIXError(.ENOTSUP)) }
}
