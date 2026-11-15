// Original Planner candidate implementation; no Apple sample source is copied.
import Foundation
import FSKit

/// The native adapter has no policy interpreter. A future authenticated transport
/// must implement this boundary using the existing Python supervisor. Each call
/// must recheck worker/revision/lifetime and resource identity before any effect.
protocol CandidateBroker: AnyObject {
    func authorizeOpen(objectID: UInt64, modes: FSVolume.OpenModes) throws
    func authorizeUpgrade(objectID: UInt64) throws
    func read(objectID: UInt64, offset: Int64, length: Int) throws -> Data
    func write(objectID: UInt64, offset: Int64, contents: Data) throws -> Int
    func attributes(objectID: UInt64) throws -> FSItem.Attributes
    func disconnect()
}

final class DisconnectedBroker: CandidateBroker {
    func authorizeOpen(objectID: UInt64, modes: FSVolume.OpenModes) throws {
        throw POSIXError(.ENOTCONN)
    }
    func authorizeUpgrade(objectID: UInt64) throws { throw POSIXError(.ENOTCONN) }
    func read(objectID: UInt64, offset: Int64, length: Int) throws -> Data {
        throw POSIXError(.ENOTCONN)
    }
    func write(objectID: UInt64, offset: Int64, contents: Data) throws -> Int {
        throw POSIXError(.ENOTCONN)
    }
    func attributes(objectID: UInt64) throws -> FSItem.Attributes {
        throw POSIXError(.ENOTCONN)
    }
    func disconnect() {}
}

final class CandidateItem: FSItem {
    let objectID: UInt64
    init(objectID: UInt64) {
        self.objectID = objectID
        super.init()
    }
}
